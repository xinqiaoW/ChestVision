import request from "@/utils/request";

export function getDatasets() {
  return request.get("/training/datasets");
}

export function uploadDataset({ datasetName, file }) {
  const formData = new FormData();
  formData.append("dataset_name", datasetName);
  formData.append("file", file);
  return request.post("/training/datasets/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
}

export function deleteDataset(datasetName) {
  return request.delete(`/training/datasets/${encodeURIComponent(datasetName)}`);
}
