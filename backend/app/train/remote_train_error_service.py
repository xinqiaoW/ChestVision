"""远程训练异常诊断接收与清洗。"""

from __future__ import annotations

import json
from typing import Any

from app.entity.db_models import RemoteTrainingJob, TrainingTask
from app.train.remote_train_errors import RemoteTrainingValidationError
from app.train.remote_train_utils import (
    REMOTE_ERROR_DEPTH_LIMIT,
    REMOTE_ERROR_LIST_LIMIT,
    REMOTE_ERROR_TEXT_LIMIT,
    _hash_token,
    _now,
)
from sqlalchemy.orm import Session


class RemoteTrainingErrorMixin:
    """训练异常 callback 与安全化错误摘要。"""

    def handle_error_callback(
        self,
        db: Session,
        task_uuid: str,
        token: str,
        error_detail: dict[str, Any],
    ) -> dict[str, Any]:
        """接收 PAI-DLC 容器上报的训练异常诊断。"""
        task = db.query(TrainingTask).filter(TrainingTask.task_uuid == task_uuid).first()
        if not task:
            raise RemoteTrainingValidationError("远程训练任务不存在")
        remote_job = (
            db.query(RemoteTrainingJob)
            .filter(RemoteTrainingJob.task_id == task.id)
            .first()
        )
        if not remote_job:
            raise RemoteTrainingValidationError("远程训练任务不存在")
        if remote_job.callback_token_hash != _hash_token(token):
            raise RemoteTrainingValidationError("回调 token 无效")

        detail = self._sanitize_error_detail(error_detail)
        summary = self._error_summary(detail)
        task.status = "failed"
        task.error_message = summary
        task.updated_at = _now()
        task.completed_at = task.completed_at or _now()
        remote_job.remote_status = "FAILED"
        remote_job.error_message = json.dumps(detail, ensure_ascii=False)
        remote_job.completed_at = remote_job.completed_at or _now()
        remote_job.last_synced_at = _now()
        remote_job.updated_at = _now()
        db.commit()
        db.refresh(task)
        db.refresh(remote_job)
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._latest_metric_payload(db, task.id),
        )

    def _sanitize_error_detail(self, value: Any, depth: int = 0) -> Any:
        if depth > REMOTE_ERROR_DEPTH_LIMIT:
            return self._clamp_text(repr(value))
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in list(value.items())[:REMOTE_ERROR_LIST_LIMIT]:
                if key == "token":
                    continue
                sanitized[str(key)] = self._sanitize_error_detail(item, depth + 1)
            return sanitized
        if isinstance(value, list):
            return [
                self._sanitize_error_detail(item, depth + 1)
                for item in value[:REMOTE_ERROR_LIST_LIMIT]
            ]
        if isinstance(value, tuple):
            return [
                self._sanitize_error_detail(item, depth + 1)
                for item in value[:REMOTE_ERROR_LIST_LIMIT]
            ]
        if isinstance(value, str):
            return self._clamp_text(value)
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return self._clamp_text(repr(value))

    @staticmethod
    def _clamp_text(value: str) -> str:
        if len(value) <= REMOTE_ERROR_TEXT_LIMIT:
            return value
        return value[:REMOTE_ERROR_TEXT_LIMIT] + "\n...<truncated>"

    @staticmethod
    def _error_summary(detail: dict[str, Any]) -> str:
        stage = detail.get("stage") or "unknown"
        error_type = detail.get("error_type") or "Error"
        error = detail.get("error") or "远程训练失败"
        return f"{stage}: {error_type}: {error}"
