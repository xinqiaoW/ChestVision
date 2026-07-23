import request from "@/utils/request";

/** 患者请求分配医生 */
export function requestDoctorApi(data) {
  return request.post("/doctor-assignment/request", data);
}

/** 患者查看我的请求/关系状态 */
export function getMyDoctorRequestApi() {
  return request.get("/doctor-assignment/my-request");
}

/** 管理员查看待审批请求 */
export function getPendingDoctorRequestsApi() {
  return request.get("/doctor-assignment/pending");
}

/** 管理员批准请求 */
export function approveDoctorRequestApi(id, note = "") {
  return request.post(`/doctor-assignment/${id}/approve`, { note });
}

/** 管理员驳回请求 */
export function rejectDoctorRequestApi(id, note = "") {
  return request.post(`/doctor-assignment/${id}/reject`, { note });
}
