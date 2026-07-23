import request from "@/utils/request";

/** 患者列表 */
export function getPatients() {
  return request.get("/patients");
}

/** 患者详情 */
export function getPatient(id) {
  return request.get(`/patients/${id}`);
}

/** 医生列表 */
export function getDoctors() {
  return request.get("/patients/doctors/list");
}

/** 医生列表（含执业档案信息） */
export function getDoctorsWithProfileApi() {
  return request.get("/patients/doctors/profiles");
}

/** 分配医患关系 */
export function assignPatient(data) {
  return request.post("/patients/relations", data);
}

/** 解除医患关系 */
export function removeRelation(id) {
  return request.delete(`/patients/relations/${id}`);
}
