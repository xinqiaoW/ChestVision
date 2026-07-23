"""模型管理业务服务。"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.core.logger import get_logger
from app.entity.db_models import (
    DetectionScene,
    DetectionTask,
    ModelArtifactLocation,
    ModelUpload,
    ModelVersion,
    RemoteTrainingJob,
    TrainingMetric,
    TrainingTask,
)
from app.model_management.errors import ModelManagementError
from app.oss_multipart.errors import OssObjectSizeMismatchError
from app.oss_multipart.schemas import MultipartUploadTarget
from app.oss_multipart.service import OssFileMultipartUploadService
from app.oss_multipart.validators import (
    MAX_MULTIPART_PARTS,
    MAX_PARTS_PER_SIGN,
    normalize_part_size,
    validate_total_part_count,
)
from app.train.remote_train_config import RemoteTrainSettings, load_remote_train_settings
from app.train.remote_train_storage import OssStorageGateway
from app.train.remote_train_utils import (
    _normalize_prefix,
    _now,
)
from sqlalchemy import or_
from sqlalchemy.orm import Session


logger = get_logger(__name__)

MODEL_TEXT_RE = re.compile(r"^[^\x00-\x1f/\\]{1,100}$")
VERSION_TEXT_RE = re.compile(r"^[^\x00-\x1f/\\]{1,50}$")
SUPPORTED_MODEL_TYPES = {"yolo11n", "yolo11s", "yolo11m", "yolo11l", "yolo11x"}
MODEL_CONTENT_TYPE = "application/octet-stream"


class ModelManagementService:
    """模型管理主服务。

    OSS 是模型权重的权威存储；本地文件只用于当前默认推理模型缓存。
    """

    def __init__(
        self,
        settings: RemoteTrainSettings | None = None,
        storage: OssStorageGateway | None = None,
    ):
        self.settings = settings or load_remote_train_settings()
        self._storage = storage

    @property
    def storage(self) -> OssStorageGateway:
        if self._storage is None:
            self._storage = OssStorageGateway(self.settings)
        return self._storage

    @property
    def multipart_uploads(self) -> OssFileMultipartUploadService:
        return OssFileMultipartUploadService(self.storage)

    @staticmethod
    def default_cache_path() -> Path:
        backend_dir = Path(__file__).resolve().parents[2]
        return backend_dir / "models" / "best.pt"

    def resolve_scene(
        self,
        db: Session,
        scene_id: int | None = None,
        scene_name: str | None = None,
    ) -> DetectionScene:
        scene = None
        if scene_id:
            scene = db.query(DetectionScene).filter(DetectionScene.id == scene_id).first()
        if not scene and scene_name:
            scene = (
                db.query(DetectionScene)
                .filter(DetectionScene.name == scene_name)
                .first()
            )
        if not scene:
            scene = (
                db.query(DetectionScene)
                .filter(DetectionScene.name == "chest_xray")
                .first()
            )
        if not scene:
            raise ModelManagementError("检测场景不存在，请先初始化数据库", 404)
        return scene

    def list_models(
        self,
        db: Session,
        model_name: str | None = None,
        version: str | None = None,
        scene_id: int | None = None,
        model_type: str | None = None,
        source_type: str = "all",
    ) -> list[dict[str, Any]]:
        query = db.query(ModelVersion).filter(ModelVersion.status == "active")
        if model_name:
            query = query.filter(ModelVersion.model_name.ilike(f"%{model_name.strip()}%"))
        if version:
            query = query.filter(ModelVersion.version.ilike(f"%{version.strip()}%"))
        if scene_id:
            query = query.filter(ModelVersion.scene_id == scene_id)
        if model_type:
            self._validate_model_type(model_type)
            query = query.filter(ModelVersion.model_type == model_type)
        if source_type == "trained":
            query = query.filter(ModelVersion.training_task_id.isnot(None))
        elif source_type == "uploaded":
            query = query.filter(ModelVersion.training_task_id.is_(None))
        elif source_type != "all":
            raise ModelManagementError("source_type 仅支持 all/trained/uploaded")

        rows = query.order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc()).all()
        return [self.serialize_model(row) for row in rows]

    def get_default_model(
        self,
        db: Session,
        scene_id: int | None = None,
        scene_name: str | None = None,
    ) -> dict[str, Any]:
        scene = self.resolve_scene(db, scene_id=scene_id, scene_name=scene_name)
        model = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.scene_id == scene.id,
                ModelVersion.status == "active",
                ModelVersion.is_default.is_(True),
            )
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .first()
        )
        return {
            "scene": self.serialize_scene(scene),
            "model": self.serialize_model(model) if model else None,
            "cache": self.default_cache_payload(db, model),
        }

    def initiate_upload(
        self,
        db: Session,
        user_id: int,
        scene_id: int | None,
        scene_name: str | None,
        model_name: str,
        version: str,
        model_type: str,
        filename: str,
        content_type: str | None,
        expected_size: int | None,
        part_size: int | None,
        description: str | None,
    ) -> dict[str, Any]:
        model_name = self._safe_model_name(model_name)
        version = self._safe_version(version)
        self._validate_model_type(model_type)
        if not (filename or "").lower().endswith(".pt"):
            raise ModelManagementError("模型权重文件必须是 .pt")

        scene = self.resolve_scene(db, scene_id=scene_id, scene_name=scene_name)
        self._ensure_version_available(db, scene.id, model_name, version)
        resolved_part_size = normalize_part_size(part_size)
        validate_total_part_count(expected_size, resolved_part_size)

        upload_uuid = "mup_" + uuid.uuid4().hex
        base_prefix = _normalize_prefix(self.settings.oss_prefix)
        object_key = f"{base_prefix}models/uploaded/{user_id}/{upload_uuid}/model.pt"
        expires_at = _now() + timedelta(seconds=self.settings.upload_url_expires_seconds)
        upload = ModelUpload(
            upload_uuid=upload_uuid,
            user_id=user_id,
            scene_id=scene.id,
            model_name=model_name,
            version=version,
            model_type=model_type,
            status="INITIATED",
            bucket=self.settings.oss_bucket,
            object_key=object_key,
            original_filename=filename,
            content_type=content_type or MODEL_CONTENT_TYPE,
            expected_size=expected_size,
            expires_at=expires_at,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(upload)
        db.commit()
        db.refresh(upload)

        headers = {
            "Content-Type": upload.content_type or MODEL_CONTENT_TYPE,
            "x-oss-meta-upload-id": upload.upload_uuid,
            "x-oss-meta-model-name": upload.model_name,
            "x-oss-meta-model-version": upload.version,
            "x-oss-meta-model-type": upload.model_type,
        }
        if expected_size:
            headers["x-oss-meta-expected-size"] = str(expected_size)

        try:
            oss_upload_id = self.multipart_uploads.init_upload(
                object_key,
                headers=headers,
            )
        except Exception:
            upload.status = "FAILED"
            upload.error_message = "OSS 初始化分片上传失败"
            upload.updated_at = _now()
            db.commit()
            raise

        upload.object_metadata = {
            "upload_mode": "multipart",
            "multipart_upload_id": oss_upload_id,
            "part_size": resolved_part_size,
            "max_parts": MAX_MULTIPART_PARTS,
            "max_parts_per_sign": MAX_PARTS_PER_SIGN,
            "headers": headers,
            "description": description,
        }
        db.commit()
        db.refresh(upload)
        return {
            **self.upload_response_base(upload),
            "upload_mode": "multipart",
            "multipart": {
                "oss_upload_id": oss_upload_id,
                "part_size": resolved_part_size,
                "max_parts": MAX_MULTIPART_PARTS,
                "max_parts_per_sign": MAX_PARTS_PER_SIGN,
                "headers": {},
                "sign_parts_endpoint": (
                    f"/api/model-management/uploads/{upload.upload_uuid}"
                    "/multipart/parts/sign"
                ),
                "complete_endpoint": (
                    f"/api/model-management/uploads/{upload.upload_uuid}"
                    "/multipart/complete"
                ),
                "abort_endpoint": (
                    f"/api/model-management/uploads/{upload.upload_uuid}"
                    "/multipart/abort"
                ),
            },
        }

    def get_upload(self, db: Session, user_id: int, upload_uuid: str) -> dict[str, Any]:
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        return self.serialize_upload(upload)

    def sign_multipart_part_urls(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
        part_numbers: list[int],
        expires_seconds: int | None = None,
    ) -> dict[str, Any]:
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        metadata = self._require_multipart_metadata(upload)
        self._ensure_upload_can_accept_parts(upload)
        target = self._multipart_target(upload, metadata)
        signed_parts = self.multipart_uploads.sign_parts(
            target,
            part_numbers,
            expires_seconds=expires_seconds,
        )
        if upload.status == "INITIATED":
            upload.status = "UPLOADING"
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return {
            **self.upload_response_base(upload),
            "upload_mode": "multipart",
            "oss_upload_id": target.oss_upload_id,
            "part_size": metadata["part_size"],
            "parts": [part.to_payload() for part in signed_parts],
        }

    def complete_multipart_upload(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        if upload.status == "UPLOADED" and upload.model_version_id:
            model = self._get_model(db, upload.model_version_id)
            return {
                "message": "模型已上传，已登记为可管理模型",
                "upload": self.serialize_upload(upload),
                "model": self.serialize_model(model),
            }
        metadata = self._require_multipart_metadata(upload)
        self._ensure_upload_can_accept_parts(upload)
        self._ensure_version_available(
            db,
            upload.scene_id,
            upload.model_name,
            upload.version,
        )
        target = self._multipart_target(upload, metadata)
        try:
            confirmed = self.multipart_uploads.complete_and_confirm(target, parts)
        except OssObjectSizeMismatchError as exc:
            upload.status = "FAILED"
            upload.error_message = str(exc)
            upload.updated_at = _now()
            db.commit()
            raise

        upload.actual_size = confirmed.content_length
        upload.etag = confirmed.etag or upload.etag
        upload.object_metadata = {
            **(upload.object_metadata or {}),
            "multipart_completed_at": _now().isoformat(),
            "completed_part_count": confirmed.part_count,
            "complete_request_id": confirmed.complete_result.get("request_id"),
        }

        model = self._create_uploaded_model_version(
            db,
            upload,
            {
                "content_length": confirmed.content_length,
                "etag": confirmed.etag,
                "content_type": confirmed.content_type,
                "headers": confirmed.headers,
            },
        )
        upload.status = "UPLOADED"
        upload.model_version_id = model.id
        upload.server_verified_at = _now()
        upload.error_message = None
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        db.refresh(model)
        return {
            "message": "模型上传完成，已登记为可管理模型",
            "upload": self.serialize_upload(upload),
            "model": self.serialize_model(model),
        }

    def abort_multipart_upload(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
    ) -> dict[str, Any]:
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        metadata = self._require_multipart_metadata(upload)
        if upload.status in {"UPLOADED"}:
            raise ModelManagementError("模型已登记，不能取消上传会话")
        if upload.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            return self.serialize_upload(upload)

        self.multipart_uploads.abort(self._multipart_target(upload, metadata))
        upload.status = "CANCELLED"
        upload.object_metadata = {
            **(upload.object_metadata or {}),
            "multipart_aborted_at": _now().isoformat(),
        }
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return self.serialize_upload(upload)

    def get_download_url(
        self,
        db: Session,
        model_version_id: int,
    ) -> dict[str, Any]:
        model = self._get_model(db, model_version_id)
        artifact = self._primary_oss_artifact(db, model)
        if not artifact:
            raise ModelManagementError("模型权重 OSS 对象不存在", 404)
        expires = self.settings.upload_url_expires_seconds
        filename = f"{model.model_name}-{model.version}.pt"
        return {
            "model_version_id": model.id,
            "method": "GET",
            "url": self.storage.sign_get_url(artifact.object_key, expires),
            "headers": {},
            "expires_seconds": expires,
            "filename": filename,
            "file_size": artifact.file_size or model.file_size,
            "etag": artifact.etag,
            "supports_range": True,
        }

    def set_default_model(
        self,
        db: Session,
        model_version_id: int,
        scene_id: int | None = None,
    ) -> dict[str, Any]:
        model = self._get_model(db, model_version_id)
        if scene_id is not None and model.scene_id != scene_id:
            raise ModelManagementError("模型不属于指定场景")

        artifact = self._primary_oss_artifact(db, model)
        local_source = model.model_path if model.model_path and os.path.exists(model.model_path) else None
        if not artifact and not local_source:
            raise ModelManagementError("模型缺少可同步的 OSS 权重或本地权重文件", 404)

        cache_path = self.default_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
        backup_path = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.old")
        moved_old_cache = False

        try:
            if artifact:
                self.storage.download_to_file(artifact.object_key, str(tmp_path))
            else:
                shutil.copy2(local_source, tmp_path)
            if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
                raise ModelManagementError("模型同步到本地缓存失败：文件为空")

            if cache_path.exists():
                os.replace(cache_path, backup_path)
                moved_old_cache = True
            os.replace(tmp_path, cache_path)

            previous_defaults = (
                db.query(ModelVersion)
                .filter(
                    ModelVersion.scene_id == model.scene_id,
                    ModelVersion.id != model.id,
                    ModelVersion.is_default.is_(True),
                )
                .all()
            )
            for previous in previous_defaults:
                previous.is_default = False
                self._mark_default_cache_deleted(db, previous)
            model.is_default = True
            self._upsert_default_cache_artifact(db, model, cache_path)
            db.flush()

            from app.services.detection_service import detection_service

            detection_service.reload_model(str(cache_path))
            db.commit()
            if moved_old_cache and backup_path.exists():
                backup_path.unlink()
            db.refresh(model)
            return {
                "message": "默认推理模型已更新并完成本地缓存同步",
                "model": self.serialize_model(model),
                "cache": self.default_cache_payload(db, model),
            }
        except Exception:
            db.rollback()
            self._restore_cache_after_failure(cache_path, tmp_path, backup_path)
            raise

    def delete_model(
        self,
        db: Session,
        model_version_id: int,
        cascade: bool = False,
        replacement_model_version_id: int | None = None,
    ) -> dict[str, Any]:
        model = self._get_model(db, model_version_id)
        if model.is_default:
            if not replacement_model_version_id:
                raise ModelManagementError(
                    "当前模型是默认推理模型，请先指定 replacement_model_version_id",
                    409,
                )
            if replacement_model_version_id == model.id:
                raise ModelManagementError("替换默认模型不能是待删除模型")
            self.set_default_model(db, replacement_model_version_id, scene_id=model.scene_id)
            model = self._get_model(db, model_version_id)

        task = model.training_task
        if task and not cascade:
            raise ModelManagementError(
                "该模型来源于训练任务，删除需要 cascade=true 确认联动删除",
                409,
            )

        artifact_rows = list(model.artifact_locations)
        if task:
            artifact_rows.extend(
                db.query(ModelArtifactLocation)
                .filter(ModelArtifactLocation.training_task_id == task.id)
                .all()
            )
        oss_keys = {
            row.object_key
            for row in artifact_rows
            if row.storage_backend == "oss" and row.object_key
        }
        local_paths = {
            row.local_path
            for row in artifact_rows
            if row.storage_backend == "local" and row.local_path
        }
        remote_job = task.remote_training_job if task else None
        deleted_oss_keys = self._delete_oss_artifacts(oss_keys, remote_job)

        db.query(DetectionTask).filter(
            DetectionTask.model_version_id == model.id
        ).update({"model_version_id": None})
        for upload in list(model.upload_sessions):
            upload.model_version_id = None
            upload.status = "CANCELLED"
            upload.updated_at = _now()

        deleted_training_task_id = task.id if task and cascade else None
        db.delete(model)
        db.flush()
        if task and cascade:
            db.delete(task)
        db.commit()
        deleted_local_paths = self._delete_local_artifacts(local_paths)
        return {
            "message": "模型已删除",
            "model_version_id": model_version_id,
            "deleted_training_task_id": deleted_training_task_id,
            "deleted_oss_keys": deleted_oss_keys,
            "deleted_local_paths": deleted_local_paths,
        }

    def register_trained_model(
        self,
        db: Session,
        task: TrainingTask,
        remote_job: RemoteTrainingJob,
    ) -> ModelVersion:
        """把远程训练完成后的 best.pt 登记为模型版本。

        调用者负责提交事务；这样训练任务状态、artifact 和模型登记可以同批提交。
        """
        if not remote_job.best_weight_key:
            raise ModelManagementError("远程训练任务缺少 best.pt OSS key")
        artifact = (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.training_task_id == task.id,
                ModelArtifactLocation.artifact_type == "best_weight",
                ModelArtifactLocation.storage_backend == "oss",
            )
            .first()
        )
        if not artifact:
            head = self.storage.head_object(remote_job.best_weight_key)
            artifact = ModelArtifactLocation(
                training_task_id=task.id,
                artifact_type="best_weight",
                storage_backend="oss",
                bucket=self.settings.oss_bucket,
                object_key=remote_job.best_weight_key,
                url=f"oss://{self.settings.oss_bucket}/{remote_job.best_weight_key}",
                content_type="application/octet-stream",
                file_size=head.get("content_length"),
                etag=head.get("etag"),
                is_primary=True,
                lifecycle_state="active",
                object_metadata=head.get("headers"),
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(artifact)
            db.flush()

        model = (
            db.query(ModelVersion)
            .filter(ModelVersion.training_task_id == task.id)
            .first()
        )
        metrics = self._latest_training_metric(db, task.id)
        if not model:
            model = ModelVersion(
                scene_id=task.scene_id,
                training_task_id=task.id,
                version=f"train-{task.task_uuid}",
                model_name=f"{task.model_name}-{task.task_uuid}",
                model_type=task.model_name,
                status="active",
                model_path=artifact.url,
                file_size=artifact.file_size,
                description=f"远程训练任务 {task.task_uuid} 自动登记",
                is_default=False,
                created_at=_now(),
            )
            db.add(model)
            db.flush()
        else:
            model.scene_id = task.scene_id
            model.model_type = task.model_name
            model.status = "active"
            model.model_path = artifact.url
            model.file_size = artifact.file_size

        if metrics:
            model.map50 = metrics.map50
            model.map50_95 = metrics.map50_95
            model.precision = metrics.precision
            model.recall = metrics.recall

        (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.training_task_id == task.id,
                ModelArtifactLocation.model_version_id.is_(None),
            )
            .update({"model_version_id": model.id})
        )
        artifact.model_version_id = model.id
        artifact.is_primary = True
        artifact.lifecycle_state = "active"
        artifact.updated_at = _now()
        return model

    def upload_response_base(self, upload: ModelUpload) -> dict[str, Any]:
        return {
            "upload_id": upload.upload_uuid,
            "status": upload.status,
            "bucket": upload.bucket,
            "object_key": upload.object_key,
            "model_version_id": upload.model_version_id,
            "expires_at": upload.expires_at,
        }

    def serialize_scene(self, scene: DetectionScene) -> dict[str, Any]:
        return {
            "id": scene.id,
            "name": scene.name,
            "display_name": scene.display_name,
        }

    def serialize_model(self, model: ModelVersion) -> dict[str, Any]:
        scene = model.scene
        task = model.training_task
        source_type = "trained" if model.training_task_id else "uploaded"
        primary = self._primary_artifact_from_model(model)
        return {
            "model_version_id": model.id,
            "id": model.id,
            "model_name": model.model_name,
            "version": model.version,
            "scene_id": model.scene_id,
            "scene_name": scene.name if scene else None,
            "scene_display_name": scene.display_name if scene else None,
            "model_type": model.model_type,
            "status": model.status,
            "source_type": source_type,
            "source_training": (
                {
                    "id": task.id,
                    "task_uuid": task.task_uuid,
                    "url": f"/training?task_uuid={task.task_uuid}",
                }
                if task
                else None
            ),
            "source_label": task.task_uuid if task else "上传",
            "file_size": model.file_size or (primary.file_size if primary else None),
            "is_default": model.is_default,
            "created_at": model.created_at,
            "downloadable": bool(primary and primary.storage_backend == "oss"),
        }

    def serialize_upload(self, upload: ModelUpload) -> dict[str, Any]:
        metadata = upload.object_metadata or {}
        return {
            "upload_id": upload.upload_uuid,
            "status": upload.status,
            "model_version_id": upload.model_version_id,
            "model_name": upload.model_name,
            "version": upload.version,
            "model_type": upload.model_type,
            "scene_id": upload.scene_id,
            "bucket": upload.bucket,
            "object_key": upload.object_key,
            "original_filename": upload.original_filename,
            "content_type": upload.content_type,
            "expected_size": upload.expected_size,
            "actual_size": upload.actual_size,
            "etag": upload.etag,
            "error_message": upload.error_message,
            "part_size": metadata.get("part_size"),
            "max_parts": metadata.get("max_parts"),
            "max_parts_per_sign": metadata.get("max_parts_per_sign"),
            "client_completed_at": upload.client_completed_at,
            "server_verified_at": upload.server_verified_at,
            "expires_at": upload.expires_at,
            "created_at": upload.created_at,
            "updated_at": upload.updated_at,
        }

    def default_cache_payload(
        self,
        db: Session,
        model: ModelVersion | None,
    ) -> dict[str, Any]:
        cache_path = self.default_cache_path()
        if not model:
            return {
                "cache_status": "unknown",
                "cache_path": str(cache_path),
                "cache_updated_at": None,
                "cache_error": None,
            }
        artifact = (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.model_version_id == model.id,
                ModelArtifactLocation.artifact_type == "default_cache",
                ModelArtifactLocation.storage_backend == "local",
            )
            .order_by(ModelArtifactLocation.updated_at.desc(), ModelArtifactLocation.id.desc())
            .first()
        )
        local_path = Path(artifact.local_path) if artifact and artifact.local_path else cache_path
        exists = local_path.exists()
        cache_error = None
        metadata = artifact.object_metadata or {} if artifact else {}
        if artifact and artifact.lifecycle_state == "failed":
            cache_status = "failed"
            cache_error = metadata.get("cache_error")
        elif exists:
            cache_status = "synced"
        else:
            cache_status = "failed" if model.is_default else "unknown"
            cache_error = "本地默认模型缓存文件不存在" if model.is_default else None
        return {
            "cache_status": cache_status,
            "cache_path": str(local_path),
            "cache_updated_at": artifact.updated_at if artifact else None,
            "cache_error": cache_error,
        }

    def _create_uploaded_model_version(
        self,
        db: Session,
        upload: ModelUpload,
        head: dict[str, Any],
    ) -> ModelVersion:
        description = (upload.object_metadata or {}).get("description")
        uri = f"oss://{upload.bucket}/{upload.object_key}"
        model = ModelVersion(
            scene_id=upload.scene_id,
            training_task_id=None,
            version=upload.version,
            model_name=upload.model_name,
            model_type=upload.model_type,
            status="active",
            model_path=uri,
            file_size=head.get("content_length"),
            description=description,
            is_default=False,
            created_at=_now(),
        )
        db.add(model)
        db.flush()
        artifact = ModelArtifactLocation(
            model_version_id=model.id,
            artifact_type="best_weight",
            storage_backend="oss",
            bucket=upload.bucket,
            object_key=upload.object_key,
            url=uri,
            content_type=head.get("content_type") or MODEL_CONTENT_TYPE,
            file_size=head.get("content_length"),
            etag=head.get("etag"),
            is_primary=True,
            lifecycle_state="active",
            object_metadata=head.get("headers"),
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(artifact)
        return model

    def _get_model(self, db: Session, model_version_id: int) -> ModelVersion:
        model = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.id == model_version_id,
                ModelVersion.status == "active",
            )
            .first()
        )
        if not model:
            raise ModelManagementError("模型不存在", 404)
        return model

    def _primary_oss_artifact(
        self,
        db: Session,
        model: ModelVersion,
    ) -> ModelArtifactLocation | None:
        return (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.model_version_id == model.id,
                ModelArtifactLocation.artifact_type == "best_weight",
                ModelArtifactLocation.storage_backend == "oss",
                ModelArtifactLocation.lifecycle_state == "active",
                ModelArtifactLocation.object_key.isnot(None),
                or_(
                    ModelArtifactLocation.is_primary.is_(True),
                    ModelArtifactLocation.is_primary.is_(False),
                ),
            )
            .order_by(ModelArtifactLocation.is_primary.desc(), ModelArtifactLocation.id.asc())
            .first()
        )

    @staticmethod
    def _primary_artifact_from_model(
        model: ModelVersion,
    ) -> ModelArtifactLocation | None:
        rows = [
            row
            for row in model.artifact_locations
            if row.artifact_type == "best_weight" and row.lifecycle_state == "active"
        ]
        rows.sort(key=lambda row: (not row.is_primary, row.id))
        return rows[0] if rows else None

    def _upsert_default_cache_artifact(
        self,
        db: Session,
        model: ModelVersion,
        cache_path: Path,
    ) -> ModelArtifactLocation:
        row = (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.model_version_id == model.id,
                ModelArtifactLocation.artifact_type == "default_cache",
                ModelArtifactLocation.storage_backend == "local",
            )
            .first()
        )
        if not row:
            row = ModelArtifactLocation(
                model_version_id=model.id,
                artifact_type="default_cache",
                storage_backend="local",
                created_at=_now(),
            )
            db.add(row)
        row.local_path = str(cache_path)
        row.content_type = MODEL_CONTENT_TYPE
        row.file_size = cache_path.stat().st_size
        row.is_primary = False
        row.lifecycle_state = "cache"
        row.object_metadata = {"cache_status": "synced"}
        row.updated_at = _now()
        return row

    def _mark_default_cache_deleted(self, db: Session, model: ModelVersion) -> None:
        rows = (
            db.query(ModelArtifactLocation)
            .filter(
                ModelArtifactLocation.model_version_id == model.id,
                ModelArtifactLocation.artifact_type == "default_cache",
                ModelArtifactLocation.storage_backend == "local",
            )
            .all()
        )
        for row in rows:
            row.lifecycle_state = "deleted"
            row.object_metadata = {
                **(row.object_metadata or {}),
                "cache_status": "deleted",
                "cache_deleted_at": _now().isoformat(),
            }
            row.updated_at = _now()

    def _delete_oss_artifacts(
        self,
        oss_keys: set[str],
        remote_job: RemoteTrainingJob | None,
    ) -> list[str]:
        deleted: list[str] = []
        for key in sorted(oss_keys):
            try:
                self.storage.delete_object(key)
                deleted.append(key)
            except Exception as exc:
                logger.error("删除模型 OSS 对象失败: key=%s error=%s", key, exc, exc_info=True)
                raise
        if remote_job and remote_job.output_prefix:
            try:
                for key in self.storage.delete_prefix(remote_job.output_prefix):
                    if key not in deleted:
                        deleted.append(key)
            except Exception as exc:
                logger.error(
                    "删除训练输出 OSS 前缀失败: prefix=%s error=%s",
                    remote_job.output_prefix,
                    exc,
                    exc_info=True,
                )
                raise
        return deleted

    def _delete_local_artifacts(self, paths: set[str]) -> list[str]:
        default_cache = self.default_cache_path().resolve()
        deleted: list[str] = []
        for value in sorted(paths):
            path = Path(value)
            try:
                if path.resolve() == default_cache:
                    continue
                if path.exists():
                    path.unlink()
                    deleted.append(str(path))
            except Exception as exc:
                logger.warning("删除模型本地缓存失败: path=%s error=%s", path, exc)
        return deleted

    @staticmethod
    def _restore_cache_after_failure(
        cache_path: Path,
        tmp_path: Path,
        backup_path: Path,
    ) -> None:
        for path in [tmp_path]:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
        try:
            if backup_path.exists():
                if cache_path.exists():
                    cache_path.unlink()
                os.replace(backup_path, cache_path)
        except Exception as exc:
            logger.error("恢复旧默认模型缓存失败: %s", exc, exc_info=True)

    def _get_upload_for_user(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
    ) -> ModelUpload:
        upload = (
            db.query(ModelUpload)
            .filter(ModelUpload.upload_uuid == upload_uuid)
            .first()
        )
        if not upload or upload.user_id != user_id:
            raise ModelManagementError("模型上传会话不存在", 404)
        return upload

    @staticmethod
    def _require_multipart_metadata(upload: ModelUpload) -> dict[str, Any]:
        metadata = upload.object_metadata or {}
        if metadata.get("upload_mode") != "multipart":
            raise ModelManagementError("上传会话不是 multipart 模式")
        if not metadata.get("multipart_upload_id"):
            raise ModelManagementError("上传会话缺少 OSS multipart_upload_id")
        if not metadata.get("part_size"):
            raise ModelManagementError("上传会话缺少 part_size")
        return metadata

    @staticmethod
    def _ensure_upload_can_accept_parts(upload: ModelUpload) -> None:
        if upload.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            raise ModelManagementError("上传会话已结束，不能继续分片上传")
        if upload.status == "UPLOADED":
            raise ModelManagementError("上传会话已完成，不能继续分片上传")

    def _multipart_target(
        self,
        upload: ModelUpload,
        metadata: dict[str, Any],
    ) -> MultipartUploadTarget:
        return MultipartUploadTarget(
            upload_id=upload.upload_uuid,
            status=upload.status,
            bucket=upload.bucket,
            object_key=upload.object_key,
            oss_upload_id=metadata["multipart_upload_id"],
            part_size=int(metadata["part_size"]),
            expected_size=upload.expected_size,
            expires_seconds=self.settings.upload_url_expires_seconds,
        )

    def _ensure_version_available(
        self,
        db: Session,
        scene_id: int,
        model_name: str,
        version: str,
    ) -> None:
        exists = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.scene_id == scene_id,
                ModelVersion.model_name == model_name,
                ModelVersion.version == version,
                ModelVersion.status == "active",
            )
            .first()
        )
        if exists:
            raise ModelManagementError("同场景下模型名称和版本已存在")

    @staticmethod
    def _safe_model_name(value: str) -> str:
        name = (value or "").strip()
        if not MODEL_TEXT_RE.fullmatch(name):
            raise ModelManagementError("模型名称不能为空，且不能包含路径分隔符或控制字符")
        return name

    @staticmethod
    def _safe_version(value: str) -> str:
        version = (value or "").strip()
        if not VERSION_TEXT_RE.fullmatch(version):
            raise ModelManagementError("模型版本不能为空，且不能包含路径分隔符或控制字符")
        return version

    @staticmethod
    def _validate_model_type(value: str) -> None:
        if value not in SUPPORTED_MODEL_TYPES:
            raise ModelManagementError("model_type 仅支持 yolo11n/s/m/l/x")

    @staticmethod
    def _latest_training_metric(
        db: Session,
        task_id: int,
    ) -> TrainingMetric | None:
        return (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.desc())
            .first()
        )


model_management_service = ModelManagementService()
