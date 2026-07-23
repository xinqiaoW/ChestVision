import {
  OSS_UPLOAD_PART_SIZE,
  OssMultipartUploadCancelledError,
  createOssMultipartUploadController,
  uploadOssMultipartFile,
} from "@/api/ossMultipartUpload";
import request from "@/utils/request";

export const MODEL_UPLOAD_PART_SIZE = OSS_UPLOAD_PART_SIZE;
export const ModelUploadCancelledError = OssMultipartUploadCancelledError;
export const createModelUploadController = createOssMultipartUploadController;

export const MODEL_TYPES = [
  { label: "YOLO11n", value: "yolo11n", extensions: [".pt"] },
  { label: "YOLO11s", value: "yolo11s", extensions: [".pt"] },
  { label: "YOLO11m", value: "yolo11m", extensions: [".pt"] },
  { label: "YOLO11l", value: "yolo11l", extensions: [".pt"] },
  { label: "YOLO11x", value: "yolo11x", extensions: [".pt"] },
];

export function modelTypeRule(modelType) {
  return MODEL_TYPES.find((item) => item.value === modelType) || MODEL_TYPES[0];
}

export function validateModelFileExtension(fileName, modelType) {
  const rule = modelTypeRule(modelType);
  const normalized = String(fileName || "").toLowerCase();
  return rule.extensions.some((ext) => normalized.endsWith(ext));
}

export function getModels(params = {}) {
  return request.get("/model-management/models", { params });
}

export function getDefaultModel(params = {}) {
  return request.get("/model-management/default-model", { params });
}

export function createModelUpload(
  {
    sceneId,
    sceneName = "chest_xray",
    modelName,
    version,
    modelType,
    file,
    partSize,
    description,
  },
  config = {},
) {
  return request.post(
    "/model-management/uploads",
    {
      scene_id: sceneId || null,
      scene_name: sceneId ? null : sceneName,
      model_name: modelName,
      version,
      model_type: modelType,
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      expected_size: file.size,
      part_size: partSize,
      description,
    },
    config,
  );
}

export function signModelUploadParts(uploadId, partNumbers, config = {}) {
  return request.post(
    `/model-management/uploads/${uploadId}/multipart/parts/sign`,
    {
      part_numbers: partNumbers,
    },
    config,
  );
}

export function completeModelUpload(uploadId, parts, config = {}) {
  return request.post(
    `/model-management/uploads/${uploadId}/multipart/complete`,
    {
      parts,
    },
    { timeout: 300000, ...config },
  );
}

export function abortModelUpload(uploadId, config = {}) {
  return request.post(
    `/model-management/uploads/${uploadId}/multipart/abort`,
    undefined,
    config,
  );
}

export function getModelDownloadUrl(modelVersionId) {
  return request.get(`/model-management/models/${modelVersionId}/download-url`);
}

export function setDefaultModel(modelVersionId, payload = {}) {
  return request.post(
    `/model-management/models/${modelVersionId}/set-default`,
    payload,
    { timeout: 300000 },
  );
}

export function deleteModel(modelVersionId, params = {}) {
  return request.delete(`/model-management/models/${modelVersionId}`, { params });
}

export async function uploadModel({
  sceneId,
  sceneName,
  modelName,
  version,
  modelType,
  description,
  file,
  onProgress,
  controller,
}) {
  return uploadOssMultipartFile({
    file,
    partSize: MODEL_UPLOAD_PART_SIZE,
    createSession: ({ file: uploadFile, partSize, config }) =>
      createModelUpload(
        {
          sceneId,
          sceneName,
          modelName,
          version,
          modelType,
          description,
          file: uploadFile,
          partSize,
        },
        config,
      ),
    signParts: (session, partNumbers, config) =>
      signModelUploadParts(session.upload_id, partNumbers, config),
    complete: (session, parts, config) =>
      completeModelUpload(session.upload_id, parts, config),
    abort: (session, config) => abortModelUpload(session.upload_id, config),
    onProgress,
    controller,
  });
}
