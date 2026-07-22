import request from "@/utils/request";

export const DATASET_UPLOAD_PART_SIZE = 32 * 1024 * 1024;

export function getDatasets() {
  return request.get("/training/remote/datasets");
}

export function createDatasetUpload({ datasetName, file, partSize }) {
  return request.post("/training/remote/uploads", {
    scene_name: datasetName,
    dataset_name: datasetName,
    filename: file.name,
    content_type: file.type || "application/zip",
    expected_size: file.size,
    upload_mode: "multipart",
    part_size: partSize,
  });
}

export function signDatasetUploadParts(uploadId, partNumbers) {
  return request.post(`/training/remote/uploads/${uploadId}/multipart/parts/sign`, {
    part_numbers: partNumbers,
  });
}

export function completeDatasetUpload(uploadId, parts) {
  return request.post(`/training/remote/uploads/${uploadId}/multipart/complete`, {
    parts,
  });
}

export function abortDatasetUpload(uploadId) {
  return request.post(`/training/remote/uploads/${uploadId}/multipart/abort`);
}

export function deleteDataset(datasetRef) {
  return request.delete(
    `/training/remote/datasets/${encodeURIComponent(datasetRef)}`,
  );
}

export async function uploadDataset({ datasetName, file, onProgress }) {
  const partSize = DATASET_UPLOAD_PART_SIZE;
  const session = await createDatasetUpload({ datasetName, file, partSize });
  const multipart = session.multipart || {};
  const resolvedPartSize = multipart.part_size || partSize;
  const totalParts = Math.ceil(file.size / resolvedPartSize);
  const maxPartsPerSign = multipart.max_parts_per_sign || 50;
  const uploadedBytesByPart = new Array(totalParts).fill(0);
  const completedParts = [];
  const startedAt = Date.now();

  const emitProgress = (phase, currentPartNumber = null) => {
    const uploadedBytes = uploadedBytesByPart.reduce((sum, value) => sum + value, 0);
    const elapsedSeconds = Math.max((Date.now() - startedAt) / 1000, 0.001);
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

  try {
    emitProgress("signing");
    for (let start = 1; start <= totalParts; start += maxPartsPerSign) {
      const end = Math.min(start + maxPartsPerSign - 1, totalParts);
      const partNumbers = [];
      for (let partNumber = start; partNumber <= end; partNumber += 1) {
        partNumbers.push(partNumber);
      }
      const signed = await signDatasetUploadParts(session.upload_id, partNumbers);
      const signedParts = signed.parts || [];
      for (const part of signedParts) {
        const partIndex = part.part_number - 1;
        const blobStart = partIndex * resolvedPartSize;
        const blobEnd = Math.min(blobStart + resolvedPartSize, file.size);
        const blob = file.slice(blobStart, blobEnd);
        const etag = await putOssPart(part.url, blob, (loaded) => {
          uploadedBytesByPart[partIndex] = loaded;
          emitProgress("uploading", part.part_number);
        });
        uploadedBytesByPart[partIndex] = blob.size;
        completedParts.push({
          part_number: part.part_number,
          etag,
        });
        emitProgress("uploading", part.part_number);
      }
    }
    emitProgress("finalizing");
    const result = await completeDatasetUpload(
      session.upload_id,
      completedParts.sort((a, b) => a.part_number - b.part_number),
    );
    emitProgress("completed");
    return result;
  } catch (error) {
    try {
      await abortDatasetUpload(session.upload_id);
    } catch {
      // ignore abort failure; the original upload error is more useful to callers
    }
    throw error;
  }
}

function putOssPart(url, blob, onUploadProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
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
          reject(new Error("OSS 未返回 ETag，请检查 Bucket CORS 暴露响应头配置"));
          return;
        }
        resolve(etag);
        return;
      }
      reject(new Error(`OSS 分片上传失败 (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("OSS 分片上传网络异常"));
    xhr.send(blob);
  });
}
