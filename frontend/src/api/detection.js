/**
 * 检测相关 API 接口
 * 快捷按钮直接调用（跳过 LLM），结果渲染在对话中
 */
import request from "@/utils/request";

/** 单图检测 */
export function detectSingle(formData) {
  return request.post("/detection/single", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60000,
  });
}

/** 批量检测 */
export function detectBatch(formData) {
  return request.post("/detection/batch", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
}

/** ZIP 检测 */
export function detectZip(formData) {
  return request.post("/detection/zip", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 180000,
  });
}

/** 获取检测任务状态 */
export function getDetectionStatus(taskId) {
  return request.get(`/detection/status/${taskId}`);
}
