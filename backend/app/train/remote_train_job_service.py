"""远程训练任务编排。"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from app.core.logger import get_logger
from app.entity.db_models import DatasetUpload, RemoteTrainingJob, TrainingTask
from app.train.remote_train_errors import RemoteTrainingValidationError
from app.train.remote_train_utils import (
    PAI_STATUS_MAP,
    _hash_token,
    _now,
    _object_parent_prefix,
)
from sqlalchemy.orm import Session


logger = get_logger(__name__)


class RemoteTrainingJobServiceMixin:
    """训练任务创建、状态同步、停止和 DLC 回调。"""

    def start_remote_training(
        self,
        db: Session,
        user_id: int,
        dataset_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """创建业务训练任务并提交 PAI-DLC。

        用户不选择本地/远程/CPU/GPU；此处固定走远程训练路径。
        training_tasks 继续作为前端主视图，remote_training_jobs 记录 PAI-DLC 细节。
        """
        upload = (
            db.query(DatasetUpload)
            .filter(DatasetUpload.dataset_uuid == dataset_id)
            .first()
        )
        if not upload or upload.user_id != user_id:
            raise RemoteTrainingValidationError("数据集不存在")
        if upload.status != "UPLOADED":
            raise RemoteTrainingValidationError("数据集尚未上传完成，不能启动训练")
        self.settings.require_callback()

        scene = self.resolve_scene(db, user_id, upload.scene_id, None)
        task_uuid = uuid.uuid4().hex[:8]
        output_prefix = f"train/jobs/{task_uuid}/"
        input_dataset_prefix = upload.processed_prefix or _object_parent_prefix(
            upload.raw_object_key
        )
        task = TrainingTask(
            user_id=user_id,
            scene_id=scene.id,
            task_uuid=task_uuid,
            status="pending",
            model_name=config.get("model_name", "yolo11n"),
            epochs=config.get("epochs", 100),
            img_size=config.get("img_size", 640),
            batch_size=config.get("batch_size", 16),
            device="remote",
            optimizer=config.get("optimizer", "SGD"),
            lr0=config.get("lr0", 0.01),
            augment_config=config.get("augment_config"),
            dataset_path=f"oss://{upload.bucket}/{input_dataset_prefix}",
            dataset_size=upload.actual_size,
            data_yaml=(
                f"oss://{upload.bucket}/{upload.processed_prefix}data.yaml"
                if upload.processed_prefix
                else None
            ),
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(task)
        db.flush()

        # callback token 只在创建任务时注入环境变量，数据库只保存 hash。
        # 回调仍然不能直接判定成功，后续还要校验 OSS 产物。
        callback_token = secrets.token_urlsafe(32)
        results_key = output_prefix + "results.csv"
        best_key = output_prefix + "weights/best.pt"
        success_key = output_prefix + "_SUCCESS"
        envs = self._build_job_envs(
            task=task,
            upload=upload,
            output_prefix=output_prefix,
            callback_token=callback_token,
        )
        command = self._build_user_command(task)
        remote_job = RemoteTrainingJob(
            task_id=task.id,
            dataset_upload_id=upload.id,
            provider="aliyun",
            workspace_id=self.settings.pai_workspace_id,
            resource_id=self.settings.pai_resource_id or None,
            remote_status="CREATED",
            region=self.settings.pai_region_id,
            image_uri=self.settings.pai_image_uri,
            job_type=self.settings.pai_job_type,
            ecs_spec=self.settings.pai_ecs_spec or None,
            pod_count=self.settings.pai_pod_count,
            user_command=command,
            envs=envs,
            input_dataset_prefix=input_dataset_prefix,
            output_prefix=output_prefix,
            results_csv_key=results_key,
            best_weight_key=best_key,
            success_key=success_key,
            callback_token_hash=_hash_token(callback_token),
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(remote_job)
        db.commit()
        db.refresh(task)
        db.refresh(remote_job)

        # 先落库再提交云任务，避免 CreateJob 成功但本地没有记录导致无法对账。
        payload = self._build_create_job_payload(remote_job)
        try:
            dlc_job_id = self.dlc.create_job(payload)
        except Exception as exc:
            logger.error("创建 PAI-DLC 任务失败: %s", exc, exc_info=True)
            task.status = "failed"
            task.error_message = "创建远程训练任务失败，请联系管理员查看后端日志"
            remote_job.remote_status = "FAILED"
            remote_job.error_message = task.error_message
            db.commit()
            raise
        remote_job.dlc_job_id = dlc_job_id
        remote_job.remote_status = "SUBMITTED"
        remote_job.submitted_at = _now()
        remote_job.updated_at = _now()
        db.commit()
        db.refresh(task)
        db.refresh(remote_job)
        return self.serialize_training(task, remote_job)

    def sync_training_job(
        self, db: Session, task_id: int, user_id: int | None = None
    ) -> dict[str, Any]:
        """同步 PAI-DLC 状态与 OSS 产物。

        该方法可由前端轮询触发，也可由后端 Worker 定时调用。
        """
        task, remote_job = self._get_task_and_remote_job(db, task_id, user_id=user_id)
        if not remote_job.dlc_job_id:
            raise RemoteTrainingValidationError("远程任务尚未提交")
        job = self.dlc.get_job(remote_job.dlc_job_id)
        raw_status = getattr(job, "status", None) or "UNKNOWN"
        remote_status = PAI_STATUS_MAP.get(raw_status, raw_status.upper())
        remote_job.remote_status = remote_status
        remote_job.last_synced_at = _now()
        remote_job.updated_at = _now()
        if (
            task.status == "failed"
            and remote_job.error_message
            and remote_status in {"SUBMITTED", "QUEUED", "RUNNING"}
        ):
            remote_job.remote_status = "FAILED"
            db.commit()
            db.refresh(task)
            db.refresh(remote_job)
            return self.serialize_training(
                task,
                remote_job,
                latest_metric=self._latest_metric_payload(db, task.id),
            )

        if remote_status in {"SUBMITTED", "QUEUED"}:
            task.status = "pending"
        elif remote_status == "RUNNING":
            task.status = "running"
            task.started_at = task.started_at or _now()
        elif remote_status == "SUCCEEDED":
            self._complete_if_artifacts_ready(db, task, remote_job)
        elif remote_status == "FAILED":
            logger.error(
                "远程训练任务失败 | task_id=%s | dlc_job_id=%s | raw_status=%s | message=%s",
                task.id,
                remote_job.dlc_job_id,
                raw_status,
                getattr(job, "message", None),
            )
            task.status = "failed"
            if not task.error_message:
                task.error_message = "远程训练失败，请联系管理员查看后端日志"
            if not remote_job.error_message:
                remote_job.error_message = task.error_message
        elif remote_status == "STOPPED":
            task.status = "cancelled"
            task.completed_at = task.completed_at or _now()
            remote_job.completed_at = remote_job.completed_at or _now()

        task.updated_at = _now()
        db.commit()
        db.refresh(task)
        db.refresh(remote_job)
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._latest_metric_payload(db, task.id),
        )

    def stop_training(
        self, db: Session, task_id: int, user_id: int | None = None
    ) -> dict[str, Any]:
        """停止远程训练任务。

        停止命令发给 PAI-DLC，业务状态立即转为 cancelled；后续对账可继续确认远程终态。
        """
        task, remote_job = self._get_task_and_remote_job(db, task_id, user_id=user_id)
        if remote_job.dlc_job_id:
            self.dlc.stop_job(remote_job.dlc_job_id)
        task.status = "cancelled"
        task.completed_at = task.completed_at or _now()
        remote_job.remote_status = "STOPPED"
        remote_job.completed_at = remote_job.completed_at or _now()
        db.commit()
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._latest_metric_payload(db, task.id),
        )

    def handle_dlc_callback(
        self,
        db: Session,
        dlc_job_id: str,
        status: str,
        token: str,
    ) -> dict[str, Any]:
        """处理 PAI-DLC 主动回调。

        回调只作为“请尽快同步”的信号。即使状态是 SUCCEEDED，也必须再验证
        _SUCCESS、results.csv 和 weights/best.pt。
        """
        remote_job = (
            db.query(RemoteTrainingJob)
            .filter(RemoteTrainingJob.dlc_job_id == dlc_job_id)
            .first()
        )
        if not remote_job:
            raise RemoteTrainingValidationError("远程任务不存在")
        if remote_job.callback_token_hash != _hash_token(token):
            raise RemoteTrainingValidationError("回调 token 无效")
        task = db.query(TrainingTask).filter(TrainingTask.id == remote_job.task_id).first()
        remote_job.remote_status = PAI_STATUS_MAP.get(status, status.upper())
        if remote_job.remote_status == "SUCCEEDED":
            self._complete_if_artifacts_ready(db, task, remote_job)
        elif remote_job.remote_status == "FAILED":
            task.status = "failed"
            if not task.error_message:
                task.error_message = "远程回调报告训练失败"
            if not remote_job.error_message:
                remote_job.error_message = task.error_message
        elif remote_job.remote_status == "STOPPED":
            task.status = "cancelled"
        db.commit()
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._latest_metric_payload(db, task.id),
        )
