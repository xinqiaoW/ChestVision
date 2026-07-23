"""远程训练产物校验与登记。"""

from __future__ import annotations

from typing import Any

from app.core.logger import get_logger
from app.entity.db_models import ModelArtifactLocation, RemoteTrainingJob, TrainingTask
from app.train.remote_train_utils import _now
from sqlalchemy.orm import Session


logger = get_logger(__name__)


class RemoteTrainingArtifactMixin:
    """训练产物查询、OSS 完整性校验和产物索引维护。"""

    def list_artifact_locations(
        self, db: Session, task_id: int, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """查询训练任务关联的所有产物位置。"""
        self._get_task_and_remote_job(db, task_id, user_id=user_id)
        rows = (
            db.query(ModelArtifactLocation)
            .filter(ModelArtifactLocation.training_task_id == task_id)
            .order_by(ModelArtifactLocation.id.asc())
            .all()
        )
        return [self.serialize_artifact(row) for row in rows]

    def _complete_if_artifacts_ready(
        self, db: Session, task: TrainingTask, remote_job: RemoteTrainingJob
    ) -> None:
        """在远程任务成功后校验并登记产物。

        不能只信任 DLC 的 Succeeded 状态。只有关键 OSS 对象齐全，
        业务任务才进入 completed，并登记 model_artifact_locations。
        """
        required = [remote_job.success_key, remote_job.results_csv_key, remote_job.best_weight_key]
        missing = [key for key in required if key and not self.storage.exists(key)]
        if missing:
            logger.warning(
                "远程训练产物未齐全 | task_id=%s | dlc_job_id=%s | missing=%s",
                task.id,
                remote_job.dlc_job_id,
                missing,
            )
            task.status = "running"
            task.error_message = "远程任务已成功，正在等待训练产物同步"
            remote_job.error_message = task.error_message
            return
        task.status = "completed"
        task.progress = 100
        task.current_epoch = task.epochs
        task.completed_at = task.completed_at or _now()
        remote_job.completed_at = remote_job.completed_at or _now()
        for artifact_type, key, content_type in [
            ("success", remote_job.success_key, "application/json"),
            ("results_csv", remote_job.results_csv_key, "text/csv"),
            ("best_weight", remote_job.best_weight_key, "application/octet-stream"),
            ("eval_report", remote_job.output_prefix + "eval_report.json", "application/json"),
        ]:
            if key and (artifact_type != "eval_report" or self.storage.exists(key)):
                self._upsert_artifact_location(
                    db,
                    training_task_id=task.id,
                    artifact_type=artifact_type,
                    key=key,
                    content_type=content_type,
                    is_primary=artifact_type == "best_weight",
                )
        self._sync_results_csv_metrics(db, task, remote_job)
        from app.model_management.service import model_management_service

        model_management_service.register_trained_model(db, task, remote_job)

    def _upsert_artifact_location(
        self,
        db: Session,
        training_task_id: int,
        artifact_type: str,
        key: str,
        content_type: str,
        is_primary: bool,
    ) -> ModelArtifactLocation:
        """登记或更新某个训练产物的 OSS 位置。

        model_artifact_locations 是模型和报告位置的统一索引；
        model_versions 只表达业务版本，不再强行承载所有存储后端字段。
        """
        row = (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.training_task_id == training_task_id,
                ModelArtifactLocation.artifact_type == artifact_type,
                ModelArtifactLocation.storage_backend == "oss",
            )
            .first()
        )
        if not row:
            row = ModelArtifactLocation(
                training_task_id=training_task_id,
                artifact_type=artifact_type,
                storage_backend="oss",
                created_at=_now(),
            )
            db.add(row)
        head = self.storage.head_object(key)
        row.bucket = self.settings.oss_bucket
        row.object_key = key
        row.url = f"oss://{self.settings.oss_bucket}/{key}"
        row.content_type = content_type
        row.file_size = head.get("content_length")
        row.etag = head.get("etag")
        row.is_primary = is_primary
        row.lifecycle_state = "active"
        row.object_metadata = head.get("headers")
        row.updated_at = _now()
        return row
