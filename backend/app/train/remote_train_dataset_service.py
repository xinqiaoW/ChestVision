"""远程训练数据集上传与场景解析。"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from app.core.logger import get_logger
from app.entity.db_models import DatasetUpload, DetectionScene, RemoteTrainingJob, TrainingTask
from app.oss_multipart.errors import OssObjectSizeMismatchError
from app.oss_multipart.schemas import MultipartUploadTarget
from app.oss_multipart.service import OssFileMultipartUploadService
from app.oss_multipart.validators import (
    MAX_MULTIPART_PARTS,
    MAX_PARTS_PER_SIGN,
    normalize_part_size,
    validate_total_part_count,
)
from app.train.remote_train_errors import RemoteTrainingValidationError
from app.train.remote_train_utils import (
    _normalize_prefix,
    _now,
    _safe_dataset_name,
)
from sqlalchemy.orm import Session


logger = get_logger(__name__)


class RemoteDatasetServiceMixin:
    """数据集上传、删除与场景解析。"""

    @property
    def multipart_uploads(self) -> OssFileMultipartUploadService:
        return OssFileMultipartUploadService(self.storage)

    def resolve_scene(
        self,
        db: Session,
        user_id: int,
        scene_id: int | None,
        scene_name: str | None,
    ) -> DetectionScene:
        """解析训练所属场景。

        远程训练不再检查本地 datasets/{scene}/yolo_dataset，因为数据集权威位置在 OSS。
        如果 scene_name 不存在，则创建一个自定义场景。DetectionScene.class_names 是
        非空字段，因此先写入占位类别；预处理 manifest 接入后应从数据集真实类别回填。
        """
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
                scene = DetectionScene(
                    name=scene_name,
                    display_name=scene_name,
                    category="custom",
                    class_names=["class_0"],
                    class_names_cn={"class_0": "类别0"},
                    is_active=True,
                    created_by=user_id,
                )
                db.add(scene)
                db.commit()
                db.refresh(scene)
        if not scene:
            raise RemoteTrainingValidationError(
                "检测场景不存在，请指定 scene_id 或 scene_name"
            )
        return scene

    def list_dataset_uploads(
        self,
        db: Session,
        user_id: int,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        """List dataset records managed by OSS, not local filesystem."""
        query = db.query(DatasetUpload).filter(DatasetUpload.status != "CANCELLED")
        if not include_all:
            query = query.filter(DatasetUpload.user_id == user_id)
        rows = (
            query.order_by(DatasetUpload.updated_at.desc(), DatasetUpload.id.desc())
            .all()
        )
        return [self.serialize_upload(row) for row in rows]

    def delete_dataset_upload(
        self,
        db: Session,
        user_id: int,
        dataset_ref: str,
        include_all: bool = False,
    ) -> dict[str, Any]:
        """Delete or cancel one OSS-managed dataset upload.

        dataset_ref may be upload_uuid, dataset_uuid, or dataset_name. When a
        name matches multiple rows, the latest row visible to the caller wins.
        """
        upload = self._get_upload_by_ref_for_user(
            db=db,
            user_id=user_id,
            dataset_ref=dataset_ref,
            include_all=include_all,
        )
        running_job = (
            db.query(RemoteTrainingJob)
            .join(TrainingTask, TrainingTask.id == RemoteTrainingJob.task_id)
            .filter(
                RemoteTrainingJob.dataset_upload_id == upload.id,
                TrainingTask.status.in_(["pending", "running"]),
            )
            .first()
        )
        if running_job:
            raise RemoteTrainingValidationError("数据集正在被远程训练任务使用，无法删除")

        metadata = upload.object_metadata or {}
        if (
            metadata.get("upload_mode") == "multipart"
            and metadata.get("multipart_upload_id")
            and upload.status in {"INITIATED", "UPLOADING"}
        ):
            try:
                self.multipart_uploads.abort(self._multipart_target(upload, metadata))
            except Exception as exc:
                logger.warning(
                    "取消 OSS multipart 上传失败: upload_uuid=%s error=%s",
                    upload.upload_uuid,
                    exc,
                    exc_info=True,
                )

        deleted_keys: list[str] = []
        for key in [upload.raw_object_key, upload.manifest_key, upload.success_key]:
            if not key:
                continue
            try:
                self.storage.delete_object(key)
                deleted_keys.append(key)
            except Exception as exc:
                logger.error(
                    "删除 OSS 数据集对象失败: upload_uuid=%s key=%s error=%s",
                    upload.upload_uuid,
                    key,
                    exc,
                    exc_info=True,
                )
                raise

        upload.status = "CANCELLED"
        upload.error_message = None
        upload.object_metadata = {
            **metadata,
            "deleted_at": _now().isoformat(),
            "deleted_keys": deleted_keys,
        }
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return self.serialize_upload(upload)

    def initiate_dataset_upload(
        self,
        db: Session,
        user_id: int,
        scene_id: int | None,
        scene_name: str | None,
        dataset_name: str,
        filename: str,
        content_type: str | None,
        expected_size: int | None,
        part_size: int | None = None,
    ) -> dict[str, Any]:
        """创建数据集上传会话。

        关键约束：
        - 后端生成 upload_id、dataset_id 和固定 OSS object key。
        - 客户端只拿到上传凭据，不拿 AccessKey 或 STS token。
        - 数据集统一走 OSS multipart 上传；每个 part 都需要独立签名。
        - 后端调用 CompleteMultipartUpload 并 HeadObject 校验后进入 UPLOADED。
        """
        dataset_name = _safe_dataset_name(dataset_name)
        if not filename.endswith(".zip"):
            raise RemoteTrainingValidationError("远程训练数据集目前只接受 .zip 包")
        resolved_part_size = normalize_part_size(part_size)
        validate_total_part_count(expected_size, resolved_part_size)
        scene = self.resolve_scene(db, user_id, scene_id, scene_name)
        upload_uuid = "upl_" + uuid.uuid4().hex
        dataset_uuid = "ds_" + uuid.uuid4().hex
        base_prefix = _normalize_prefix(self.settings.oss_prefix)
        raw_key = f"{base_prefix}{user_id}/{upload_uuid}/dataset.zip"
        expires_at = _now() + timedelta(seconds=self.settings.upload_url_expires_seconds)

        upload = DatasetUpload(
            upload_uuid=upload_uuid,
            dataset_uuid=dataset_uuid,
            user_id=user_id,
            scene_id=scene.id,
            dataset_name=dataset_name,
            status="INITIATED",
            bucket=self.settings.oss_bucket,
            raw_object_key=raw_key,
            original_filename=filename,
            content_type=content_type or "application/zip",
            expected_size=expected_size,
            expires_at=expires_at,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(upload)
        db.commit()
        db.refresh(upload)

        # 这些 header 被纳入签名，客户端 PUT 时必须原样携带。
        # x-oss-meta-* 后续恢复 HeadObject 校验时可用于反查上传会话和数据集身份。
        headers = {
            "Content-Type": upload.content_type or "application/zip",
            "x-oss-meta-upload-id": upload.upload_uuid,
            "x-oss-meta-dataset-id": upload.dataset_uuid,
            "x-oss-meta-dataset-name": upload.dataset_name,
        }
        if expected_size:
            headers["x-oss-meta-expected-size"] = str(expected_size)

        try:
            oss_upload_id = self.multipart_uploads.init_upload(raw_key, headers=headers)
        except Exception:
            upload.status = "FAILED"
            upload.error_message = "OSS 初始化分片上传失败"
            upload.updated_at = _now()
            db.commit()
            raise

        upload.object_metadata = {
            "upload_mode": "multipart",
            "headers": headers,
            "multipart_upload_id": oss_upload_id,
            "part_size": resolved_part_size,
            "max_parts": MAX_MULTIPART_PARTS,
            "max_parts_per_sign": MAX_PARTS_PER_SIGN,
        }
        db.commit()
        db.refresh(upload)
        return {
            **self._upload_response_base(upload),
            "upload_mode": "multipart",
            "multipart": {
                "oss_upload_id": oss_upload_id,
                "part_size": resolved_part_size,
                "max_parts": MAX_MULTIPART_PARTS,
                "max_parts_per_sign": MAX_PARTS_PER_SIGN,
                "headers": {},
                "sign_parts_endpoint": (
                    f"/api/training/remote/uploads/{upload.upload_uuid}"
                    "/multipart/parts/sign"
                ),
                "complete_endpoint": (
                    f"/api/training/remote/uploads/{upload.upload_uuid}"
                    "/multipart/complete"
                ),
                "abort_endpoint": (
                    f"/api/training/remote/uploads/{upload.upload_uuid}"
                    "/multipart/abort"
                ),
            },
        }

    def sign_multipart_part_urls(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
        part_numbers: list[int],
        expires_seconds: int | None = None,
    ) -> dict[str, Any]:
        """为一批 OSS multipart part 生成短期 PUT URL。"""
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
            **self._upload_response_base(upload),
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
        """由后端调用 OSS CompleteMultipartUpload，并用 HeadObject 确认对象。"""
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        if upload.status == "UPLOADED":
            return {
                "message": "数据集已上传完成",
                "upload": self.serialize_upload(upload),
            }
        metadata = self._require_multipart_metadata(upload)
        self._ensure_upload_can_accept_parts(upload)
        target = self._multipart_target(upload, metadata)
        try:
            confirmed = self.multipart_uploads.complete_and_confirm(target, parts)
        except OssObjectSizeMismatchError as exc:
            upload.status = "FAILED"
            upload.error_message = str(exc)
            upload.updated_at = _now()
            db.commit()
            raise

        upload.status = "UPLOADED"
        upload.actual_size = confirmed.content_length
        upload.etag = confirmed.etag or upload.etag
        upload.server_verified_at = _now()
        upload.object_metadata = {
            **(upload.object_metadata or {}),
            "multipart_completed_at": _now().isoformat(),
            "completed_part_count": confirmed.part_count,
            "complete_request_id": confirmed.complete_result.get("request_id"),
        }
        upload.error_message = None
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return {
            "message": "数据集上传完成",
            "oss_complete": confirmed.complete_result,
            "upload": self.serialize_upload(upload),
        }

    def abort_multipart_upload(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
    ) -> dict[str, Any]:
        """取消 OSS multipart 上传会话。"""
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        metadata = self._require_multipart_metadata(upload)
        if upload.status == "UPLOADED":
            raise RemoteTrainingValidationError("上传已确认，不能取消分片上传")
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

    def get_dataset_upload(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
    ) -> dict[str, Any]:
        """查询单个上传会话状态。"""
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        return self.serialize_upload(upload)

    def _multipart_target(
        self,
        upload: DatasetUpload,
        metadata: dict[str, Any],
    ) -> MultipartUploadTarget:
        return MultipartUploadTarget(
            upload_id=upload.upload_uuid,
            status=upload.status,
            bucket=upload.bucket,
            object_key=upload.raw_object_key,
            oss_upload_id=metadata["multipart_upload_id"],
            part_size=int(metadata["part_size"]),
            expected_size=upload.expected_size,
            expires_seconds=self.settings.upload_url_expires_seconds,
        )

    def _upload_response_base(self, upload: DatasetUpload) -> dict[str, Any]:
        """构造上传会话响应的公共字段。"""
        return {
            "upload_id": upload.upload_uuid,
            "dataset_id": upload.dataset_uuid,
            "status": upload.status,
            "bucket": upload.bucket,
            "object_key": upload.raw_object_key,
            "expires_at": upload.expires_at,
        }
