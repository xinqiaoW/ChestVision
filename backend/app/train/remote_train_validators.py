"""远程训练数据库记录与上传参数校验。"""

from __future__ import annotations

from typing import Any

from app.entity.db_models import DatasetUpload, RemoteTrainingJob, TrainingTask
from app.train.remote_train_errors import RemoteTrainingValidationError
from sqlalchemy.orm import Session


class RemoteTrainingValidationMixin:
    """上传分片参数、上传记录和远程任务记录校验。"""

    def _require_multipart_metadata(self, upload: DatasetUpload) -> dict[str, Any]:
        """读取并校验 multipart 上传所需 metadata。"""
        metadata = upload.object_metadata or {}
        if metadata.get("upload_mode") != "multipart":
            raise RemoteTrainingValidationError("上传会话不是 multipart 模式")
        if not metadata.get("multipart_upload_id"):
            raise RemoteTrainingValidationError("上传会话缺少 OSS multipart_upload_id")
        if not metadata.get("part_size"):
            raise RemoteTrainingValidationError("上传会话缺少 part_size")
        return metadata

    def _ensure_upload_can_accept_parts(self, upload: DatasetUpload) -> None:
        """确认上传会话仍可继续上传或合并分片。"""
        if upload.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            raise RemoteTrainingValidationError("上传会话已结束，不能继续分片上传")
        if upload.status == "UPLOADED":
            raise RemoteTrainingValidationError("上传会话已完成，不能继续分片上传")

    def _get_upload_for_user(
        self, db: Session, user_id: int, upload_uuid: str
    ) -> DatasetUpload:
        upload = (
            db.query(DatasetUpload)
            .filter(DatasetUpload.upload_uuid == upload_uuid)
            .first()
        )
        if not upload or upload.user_id != user_id:
            raise RemoteTrainingValidationError("上传会话不存在")
        return upload

    def _get_upload_by_ref_for_user(
        self,
        db: Session,
        user_id: int,
        dataset_ref: str,
        include_all: bool = False,
    ) -> DatasetUpload:
        query = db.query(DatasetUpload).filter(
            (
                (DatasetUpload.upload_uuid == dataset_ref)
                | (DatasetUpload.dataset_uuid == dataset_ref)
                | (DatasetUpload.dataset_name == dataset_ref)
            )
        )
        if not include_all:
            query = query.filter(DatasetUpload.user_id == user_id)
        upload = (
            query.order_by(DatasetUpload.updated_at.desc(), DatasetUpload.id.desc())
            .first()
        )
        if not upload:
            raise RemoteTrainingValidationError("数据集不存在")
        return upload

    def _get_task_and_remote_job(
        self, db: Session, task_id: int, user_id: int | None = None
    ) -> tuple[TrainingTask, RemoteTrainingJob]:
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            raise RemoteTrainingValidationError("训练任务不存在")
        if user_id is not None and task.user_id != user_id:
            raise RemoteTrainingValidationError("无权访问该训练任务")
        remote_job = (
            db.query(RemoteTrainingJob)
            .filter(RemoteTrainingJob.task_id == task_id)
            .first()
        )
        if not remote_job:
            raise RemoteTrainingValidationError("训练任务不是远程任务")
        return task, remote_job
