import {
  OSS_UPLOAD_PART_SIZE,
  OssMultipartUploadCancelledError,
  createOssMultipartUploadController,
  uploadOssMultipartFile,
} from "@/api/ossMultipartUpload";
import request from "@/utils/request";

export const DATASET_UPLOAD_PART_SIZE = OSS_UPLOAD_PART_SIZE;
export const DatasetUploadCancelledError = OssMultipartUploadCancelledError;
export const createDatasetUploadController = createOssMultipartUploadController;

export function getDatasets() {
  return request.get("/training/remote/datasets");
}

export function createDatasetUpload({ datasetName, file, partSize }, config = {}) {
  return request.post(
    "/training/remote/uploads",
    {
      scene_name: "chest_xray",
      dataset_name: datasetName,
      filename: file.name,
      content_type: file.type || "application/zip",
      expected_size: file.size,
      part_size: partSize,
    },
    config,
  );
}

export function signDatasetUploadParts(uploadId, partNumbers, config = {}) {
  return request.post(
    `/training/remote/uploads/${uploadId}/multipart/parts/sign`,
    {
      part_numbers: partNumbers,
    },
    config,
  );
}

export function completeDatasetUpload(uploadId, parts, config = {}) {
  return request.post(
    `/training/remote/uploads/${uploadId}/multipart/complete`,
    {
      parts,
    },
    { timeout: 300000, ...config },
  );
}

export function abortDatasetUpload(uploadId, config = {}) {
  return request.post(
    `/training/remote/uploads/${uploadId}/multipart/abort`,
    undefined,
    config,
  );
}

export function deleteDataset(datasetRef) {
  return request.delete(
    `/training/remote/datasets/${encodeURIComponent(datasetRef)}`,
  );
}

export async function uploadDataset({ datasetName, file, onProgress, controller }) {
  return uploadOssMultipartFile({
    file,
    partSize: DATASET_UPLOAD_PART_SIZE,
    createSession: ({ file: uploadFile, partSize, config }) =>
      createDatasetUpload(
        {
          datasetName,
          file: uploadFile,
          partSize,
        },
        config,
      ),
    signParts: (session, partNumbers, config) =>
      signDatasetUploadParts(session.upload_id, partNumbers, config),
    complete: (session, parts, config) =>
      completeDatasetUpload(session.upload_id, parts, config),
    abort: (session, config) => abortDatasetUpload(session.upload_id, config),
    onProgress,
    controller,
  });
}
