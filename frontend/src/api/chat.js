/**
 * 智能体对话相关 API 接口
 */
import request from "@/utils/request";

/**
 * 获取当前用户的会话列表
 * @param {Object} params - { status?, limit?, offset? }
 */
export function getSessionsApi(params = {}) {
  return request.get("/chat/sessions", { params });
}

/**
 * 获取指定会话的消息历史
 * @param {number} sessionId
 * @param {Object} params - { limit?, offset? }
 */
export function getSessionMessagesApi(sessionId, params = {}) {
  return request.get(`/chat/sessions/${sessionId}/messages`, { params });
}

/**
 * 删除指定会话
 * @param {number} sessionId
 */
export function deleteSessionApi(sessionId) {
  return request.delete(`/chat/sessions/${sessionId}`);
}

/**
 * 归档指定会话
 * @param {number} sessionId
 */
export function archiveSessionApi(sessionId) {
  return request.put(`/chat/sessions/${sessionId}/archive`);
}

/**
 * 上传胸片文件
 * @param {File} file
 */
export function uploadImageApi(file) {
  const fd = new FormData();
  fd.append("file", file);
  return request.post("/chat/upload", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

/**
 * Multi-Agent SSE 流式对话（Day 12 多智能体协作架构）
 * 注意：此函数返回请求参数，实际 SSE 调用由 streamChat 工具处理
 * @param {Object} params
 * @param {string} params.message - 用户消息
 * @param {string} [params.image_path] - 上传后的图片路径
 * @param {number} [params.session_id] - 会话ID
 * @param {number} [params.patient_profile_id] - 患者档案ID
 */
export function getMultiAgentParams(params) {
  return {
    url: "/api/chat/multi-agent",
    body: {
      message: params.message,
      image_path: params.image_path || undefined,
      session_id: params.session_id || undefined,
      patient_profile_id: params.patient_profile_id || undefined,
    },
  };
}
