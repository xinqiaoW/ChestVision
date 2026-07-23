"""远程训练 API 响应序列化。"""

from __future__ import annotations

import json
from typing import Any

from app.entity.db_models import DatasetUpload, ModelArtifactLocation, RemoteTrainingJob, TrainingTask


class RemoteTrainingSerializerMixin:
    """把 ORM 记录转换为前端 API 响应。"""

    def _error_detail_payload(
        self, remote_job: RemoteTrainingJob
    ) -> dict[str, Any] | None:
        raw = remote_job.error_message
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {"error": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"error": parsed}

    def serialize_upload(self, upload: DatasetUpload) -> dict[str, Any]:
        return {
            "upload_id": upload.upload_uuid,
            "dataset_id": upload.dataset_uuid,
            "name": upload.dataset_name,
            "dataset_name": upload.dataset_name,
            "status": upload.status,
            "has_data": upload.status == "UPLOADED",
            "storage_backend": "oss",
            "bucket": upload.bucket,
            "raw_object_key": upload.raw_object_key,
            "processed_prefix": upload.processed_prefix,
            "manifest_key": upload.manifest_key,
            "success_key": upload.success_key,
            "original_filename": upload.original_filename,
            "content_type": upload.content_type,
            "expected_size": upload.expected_size,
            "actual_size": upload.actual_size,
            "etag": upload.etag,
            "error_message": upload.error_message,
            "train_count": None,
            "val_count": None,
            "total_count": None,
            "client_completed_at": upload.client_completed_at,
            "server_verified_at": upload.server_verified_at,
            "expires_at": upload.expires_at,
            "ready_at": upload.ready_at,
            "created_at": upload.created_at,
            "updated_at": upload.updated_at,
        }

    def serialize_training(
        self,
        task: TrainingTask,
        remote_job: RemoteTrainingJob,
        latest_metric: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": task.id,
            "task_uuid": task.task_uuid,
            "status": task.status,
            "model_name": task.model_name,
            "epochs": task.epochs,
            "current_epoch": task.current_epoch,
            "progress": task.progress,
            "device": task.device,
            "batch_size": task.batch_size,
            "img_size": task.img_size,
            "dataset_path": task.dataset_path,
            "error_message": task.error_message,
            "error_detail": self._error_detail_payload(remote_job),
            "latest_metric": latest_metric,
            "remote": {
                "dlc_job_id": remote_job.dlc_job_id,
                "remote_status": remote_job.remote_status,
                "workspace_id": remote_job.workspace_id,
                "region": remote_job.region,
                "image_uri": remote_job.image_uri,
                "ecs_spec": remote_job.ecs_spec,
                "input_dataset_prefix": remote_job.input_dataset_prefix,
                "output_prefix": remote_job.output_prefix,
                "results_csv_key": remote_job.results_csv_key,
                "best_weight_key": remote_job.best_weight_key,
                "success_key": remote_job.success_key,
                "error_message": remote_job.error_message,
            },
        }

    def serialize_artifact(self, row: ModelArtifactLocation) -> dict[str, Any]:
        return {
            "id": row.id,
            "model_version_id": row.model_version_id,
            "training_task_id": row.training_task_id,
            "artifact_type": row.artifact_type,
            "storage_backend": row.storage_backend,
            "bucket": row.bucket,
            "object_key": row.object_key,
            "local_path": row.local_path,
            "url": row.url,
            "content_type": row.content_type,
            "file_size": row.file_size,
            "etag": row.etag,
            "is_primary": row.is_primary,
            "lifecycle_state": row.lifecycle_state,
        }
