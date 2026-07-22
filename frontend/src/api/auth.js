/**
 * 认证相关 API 接口
 */
import request from "@/utils/request";

/**
 * 用户注册
 * @param {Object} data - { username, email, password }
 */
export function registerApi(data) {
  return request.post("/auth/register", data);
}

/** 发送注册邮箱验证码 */
export function sendRegistrationCodeApi(email) {
  return request.post("/auth/email-verification/send", { email });
}

/**
 * 用户登录
 * @param {Object} data - { username, password }
 * @returns {Promise} - { access_token, token_type, user }
 */
export function loginApi(data) {
  return request.post("/auth/login", data);
}

/**
 * 获取当前用户信息（需要 Token）
 */
export function getUserInfoApi() {
  return request.get("/auth/me");
}

/**
 * 忘记密码 — 发送重置链接
 * @param {Object} data - { email }
 */
export function forgotPasswordApi(data) {
  return request.post("/auth/forgot-password", data);
}

// ══════════════════════════════════════════════════════
// 个人中心
// ══════════════════════════════════════════════════════

/**
 * 更新个人基本信息
 * @param {Object} data - { username?, email?, phone?, avatar? }
 */
export function updateProfileApi(data) {
  return request.put("/profile/me", data);
}

/**
 * 修改密码
 * @param {Object} data - { old_password, new_password }
 */
export function changePasswordApi(data) {
  return request.put("/profile/me/password", data);
}

/**
 * 获取我的患者档案
 */
export function getPatientProfileApi() {
  return request.get("/profile/me/patient-profile");
}

/**
 * 更新我的患者档案
 * @param {Object} data
 */
export function updatePatientProfileApi(data) {
  return request.put("/profile/me/patient-profile", data);
}

/**
 * 获取个人中心统计数据
 */
export function getProfileStatsApi() {
  return request.get("/profile/stats");
}
