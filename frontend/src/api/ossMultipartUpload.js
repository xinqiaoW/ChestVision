export const OSS_UPLOAD_PART_SIZE = 32 * 1024 * 1024;

export class OssMultipartUploadCancelledError extends Error {
  constructor(message = "上传已取消") {
    super(message);
    this.name = "OssMultipartUploadCancelledError";
    this.code = "UPLOAD_CANCELLED";
  }
}

class OssMultipartUploadPausedError extends Error {
  constructor() {
    super("上传已暂停");
    this.name = "OssMultipartUploadPausedError";
  }
}

export function createOssMultipartUploadController() {
  const listeners = new Set();
  const state = {
    cancelled: false,
    paused: false,
    activeXhr: null,
    abortController: new AbortController(),
    pausePromise: null,
    pauseResolver: null,
  };

  const snapshot = () => ({
    cancelled: state.cancelled,
    paused: state.paused,
  });

  const notify = () => {
    const current = snapshot();
    listeners.forEach((listener) => listener(current));
  };

  const ensurePausePromise = () => {
    if (!state.pausePromise) {
      state.pausePromise = new Promise((resolve) => {
        state.pauseResolver = resolve;
      });
    }
    return state.pausePromise;
  };

  const resolvePause = () => {
    if (state.pauseResolver) {
      state.pauseResolver();
    }
    state.pauseResolver = null;
    state.pausePromise = null;
  };

  const throwIfCancelled = () => {
    if (state.cancelled) {
      throw new OssMultipartUploadCancelledError();
    }
  };

  return {
    get isCancelled() {
      return state.cancelled;
    },
    get isPaused() {
      return state.paused;
    },
    get signal() {
      return state.abortController.signal;
    },
    onStateChange(listener) {
      listeners.add(listener);
      listener(snapshot());
      return () => listeners.delete(listener);
    },
    pause() {
      if (state.cancelled || state.paused) return;
      state.paused = true;
      state.activeXhr?.abort();
      notify();
    },
    resume() {
      if (state.cancelled || !state.paused) return;
      state.paused = false;
      resolvePause();
      notify();
    },
    cancel() {
      if (state.cancelled) return;
      state.cancelled = true;
      state.paused = false;
      state.abortController.abort();
      state.activeXhr?.abort();
      resolvePause();
      notify();
    },
    bindXhr(xhr) {
      state.activeXhr = xhr;
      return () => {
        if (state.activeXhr === xhr) {
          state.activeXhr = null;
        }
      };
    },
    async waitIfPaused() {
      while (state.paused && !state.cancelled) {
        await ensurePausePromise();
      }
      throwIfCancelled();
    },
    throwIfCancelled,
  };
}

export async function uploadOssMultipartFile({
  file,
  partSize = OSS_UPLOAD_PART_SIZE,
  createSession,
  signParts,
  complete,
  abort,
  onProgress,
  controller,
}) {
  let session = null;
  let pausedDurationMs = 0;
  let pauseVersion = 0;

  const requestConfig = controller?.signal
    ? { signal: controller.signal, silent: true }
    : {};

  try {
    await controller?.waitIfPaused();
    controller?.throwIfCancelled();
    session = await createSession({ file, partSize, config: requestConfig });
  } catch (error) {
    if (controller?.isCancelled && isAbortLikeError(error)) {
      throw new OssMultipartUploadCancelledError();
    }
    throw error;
  }

  const multipart = session.multipart || {};
  const resolvedPartSize = multipart.part_size || partSize;
  const totalParts = Math.ceil(file.size / resolvedPartSize);
  const maxPartsPerSign = multipart.max_parts_per_sign || 50;
  const uploadedBytesByPart = new Array(totalParts).fill(0);
  const completedParts = [];
  const startedAt = Date.now();

  const emitProgress = (phase, currentPartNumber = null) => {
    const uploadedBytes = uploadedBytesByPart.reduce((sum, value) => sum + value, 0);
    const elapsedSeconds = Math.max(
      (Date.now() - startedAt - pausedDurationMs) / 1000,
      0.001,
    );
    const speedBytesPerSecond = uploadedBytes / elapsedSeconds;
    const remainingBytes = Math.max(file.size - uploadedBytes, 0);
    const remainingSeconds =
      speedBytesPerSecond > 0 ? remainingBytes / speedBytesPerSecond : null;
    onProgress?.({
      phase,
      currentPartNumber,
      uploadedParts: completedParts.length,
      totalParts,
      uploadedBytes,
      totalBytes: file.size,
      percent: file.size ? Math.min((uploadedBytes / file.size) * 100, 100) : 0,
      speedBytesPerSecond,
      remainingSeconds,
    });
  };

  const waitForResume = async (currentPartNumber = null) => {
    if (!controller?.isPaused) {
      controller?.throwIfCancelled();
      return;
    }
    const pausedAt = Date.now();
    emitProgress("paused", currentPartNumber);
    await controller.waitIfPaused();
    pausedDurationMs += Date.now() - pausedAt;
    pauseVersion += 1;
  };

  const signSinglePart = async (partNumber) => {
    const signed = await signParts(session, [partNumber], requestConfig);
    controller?.throwIfCancelled();
    const part = (signed.parts || [])[0];
    if (!part?.url) {
      throw new Error(`第 ${partNumber} 片上传 URL 签发失败`);
    }
    return part;
  };

  try {
    emitProgress("signing");
    for (let start = 1; start <= totalParts; start += maxPartsPerSign) {
      await waitForResume();
      const end = Math.min(start + maxPartsPerSign - 1, totalParts);
      const partNumbers = [];
      for (let partNumber = start; partNumber <= end; partNumber += 1) {
        partNumbers.push(partNumber);
      }
      const signed = await signParts(session, partNumbers, requestConfig);
      controller?.throwIfCancelled();
      const signedParts = signed.parts || [];
      const batchPauseVersion = pauseVersion;
      for (const part of signedParts) {
        const partIndex = part.part_number - 1;
        const blobStart = partIndex * resolvedPartSize;
        const blobEnd = Math.min(blobStart + resolvedPartSize, file.size);
        const blob = file.slice(blobStart, blobEnd);
        let etag = null;
        let uploadPart = part;
        let partPauseVersion = batchPauseVersion;
        while (!etag) {
          await waitForResume(part.part_number);
          if (partPauseVersion !== pauseVersion) {
            uploadPart = await signSinglePart(part.part_number);
            partPauseVersion = pauseVersion;
          }
          uploadedBytesByPart[partIndex] = 0;
          emitProgress("uploading", part.part_number);
          try {
            etag = await putOssPart(
              uploadPart.url,
              blob,
              (loaded) => {
                uploadedBytesByPart[partIndex] = loaded;
                emitProgress("uploading", part.part_number);
              },
              controller,
            );
          } catch (error) {
            if (error instanceof OssMultipartUploadPausedError) {
              uploadedBytesByPart[partIndex] = 0;
              await waitForResume(part.part_number);
              continue;
            }
            throw error;
          }
        }
        uploadedBytesByPart[partIndex] = blob.size;
        completedParts.push({
          part_number: part.part_number,
          etag,
        });
        emitProgress("uploading", part.part_number);
      }
    }
    await waitForResume();
    emitProgress("finalizing");
    const result = await complete(
      session,
      completedParts.sort((a, b) => a.part_number - b.part_number),
      requestConfig,
    );
    controller?.throwIfCancelled();
    emitProgress("completed");
    return result;
  } catch (error) {
    const uploadError =
      controller?.isCancelled && isAbortLikeError(error)
        ? new OssMultipartUploadCancelledError()
        : error;
    if (session?.upload_id && abort) {
      try {
        await abort(session, { silent: true });
      } catch {
        // Keep the original upload error.
      }
    }
    throw uploadError;
  }
}

function putOssPart(url, blob, onUploadProgress, controller) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const unbindXhr = controller?.bindXhr(xhr);
    let settled = false;

    const settle = (handler, value) => {
      if (settled) return;
      settled = true;
      unbindXhr?.();
      handler(value);
    };

    xhr.open("PUT", url);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onUploadProgress(event.loaded);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = (xhr.getResponseHeader("ETag") || "").replace(/^"|"$/g, "");
        if (!etag) {
          settle(
            reject,
            new Error("OSS 未返回 ETag，请检查 Bucket CORS 暴露响应头配置"),
          );
          return;
        }
        settle(resolve, etag);
        return;
      }
      settle(reject, new Error(`OSS 分片上传失败 (${xhr.status})`));
    };
    xhr.onerror = () => settle(reject, new Error("OSS 分片上传网络异常"));
    xhr.onabort = () => {
      if (controller?.isCancelled) {
        settle(reject, new OssMultipartUploadCancelledError());
        return;
      }
      if (controller?.isPaused) {
        settle(reject, new OssMultipartUploadPausedError());
        return;
      }
      settle(reject, new Error("OSS 分片上传已中断"));
    };

    try {
      controller?.throwIfCancelled();
      xhr.send(blob);
    } catch (error) {
      settle(reject, error);
    }
  });
}

function isAbortLikeError(error) {
  return (
    error instanceof OssMultipartUploadCancelledError ||
    error?.code === "ERR_CANCELED" ||
    error?.name === "CanceledError" ||
    error?.name === "AbortError"
  );
}
