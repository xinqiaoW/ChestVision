import request from "@/utils/request";

export function generateDoctorRecommendations(data) {
  return request.post("/doctor-recommendations/generate", data, {
    timeout: 90000,
  });
}

export function selectDoctorRecommendation(id) {
  return request.post(`/doctor-recommendations/${id}/select`);
}

export function getDoctorRecommendations(taskId) {
  return request.get(`/doctor-recommendations/task/${taskId}`);
}

export function getPendingDoctorReviews() {
  return request.get("/doctor-recommendations/review/pending");
}

export function confirmDoctorRecommendation(id, note = "") {
  return request.post(`/doctor-recommendations/${id}/confirm`, { note });
}

export function rejectDoctorRecommendation(id, note = "") {
  return request.post(`/doctor-recommendations/${id}/reject`, { note });
}
