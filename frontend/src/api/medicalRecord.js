import request from "@/utils/request";

/** 病例列表 */
export function getMedicalRecords(params) {
  return request.get("/medical-records", { params });
}

/** 病例详情 */
export function getMedicalRecord(id) {
  return request.get(`/medical-records/${id}`);
}

/** 创建病例 */
export function createMedicalRecord(data) {
  return request.post("/medical-records", data);
}

/** 编辑病例 */
export function updateMedicalRecord(id, data) {
  return request.put(`/medical-records/${id}`, data);
}

/** 删除病例 */
export function deleteMedicalRecord(id) {
  return request.delete(`/medical-records/${id}`);
}
