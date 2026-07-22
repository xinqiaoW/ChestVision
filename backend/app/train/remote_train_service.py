"""远程训练业务编排服务。"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from app.core.logger import get_logger
from app.entity.db_models import (
    DatasetUpload,
    DetectionScene,
    ModelArtifactLocation,
    RemoteTrainingJob,
    TrainingMetric,
    TrainingTask,
)
from app.train.remote_train_config import RemoteTrainSettings, load_remote_train_settings
from app.train.remote_train_dlc import PaiDlcGateway
from app.train.remote_train_storage import OssStorageGateway
from sqlalchemy.orm import Session


logger = get_logger(__name__)

# 数据集名称进入 OSS key 和场景名，必须严格限制，避免路径穿越和前缀污染。
DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

# PAI-DLC 原始状态到业务内部远程状态的映射。
# training_tasks.status 仍保持旧前端可识别的 pending/running/completed/failed/cancelled。
PAI_STATUS_MAP = {
    "Submitted": "SUBMITTED",
    "Pending": "QUEUED",
    "EnvPreparing": "QUEUED",
    "EnvironmentPreparing": "QUEUED",
    "Running": "RUNNING",
    "Succeeded": "SUCCEEDED",
    "Failed": "FAILED",
    "Stopped": "STOPPED",
}

OSS_MULTIPART_COMPLETE_EVENT = "oss:ObjectCreated:CompleteMultipartUpload"
SUPPORTED_UPLOAD_MODES = {"multipart", "presigned_put"}
DEFAULT_MULTIPART_PART_SIZE = 32 * 1024 * 1024
MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10000
MAX_PARTS_PER_SIGN = 50


class RemoteTrainingValidationError(ValueError):
    """远程训练业务校验错误。"""


def _now() -> datetime:
    return datetime.now()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_prefix(prefix: str) -> str:
    return prefix.strip("/") + "/" if prefix else ""


def _endpoint_host(endpoint: str) -> str:
    value = (endpoint or "").strip()
    if not value:
        raise RemoteTrainingValidationError("OSS endpoint 不能为空")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.strip().strip("/")
    if not host:
        raise RemoteTrainingValidationError("OSS endpoint 格式无效")
    return host


def _dlc_oss_uri(
    bucket: str,
    endpoint: str,
    prefix: str,
    uri_host: str = "",
) -> str:
    bucket_name = (bucket or "").strip().strip("/")
    object_prefix = _normalize_prefix(prefix)
    if not bucket_name and not uri_host:
        raise RemoteTrainingValidationError("OSS bucket 不能为空")
    if not object_prefix:
        raise RemoteTrainingValidationError("OSS 对象前缀不能为空")
    endpoint_host = _endpoint_host(uri_host or endpoint)
    authority = (
        endpoint_host
        if uri_host or endpoint_host.startswith(f"{bucket_name}.")
        else f"{bucket_name}.{endpoint_host}"
    )
    return f"oss://{authority}/{object_prefix}"


def _object_parent_prefix(key: str) -> str:
    """返回对象所在前缀，用于把 raw dataset.zip 所在目录挂载到 DLC。"""
    return key.rsplit("/", 1)[0] + "/" if "/" in key else ""


def _safe_dataset_name(name: str) -> str:
    if not DATASET_NAME_RE.fullmatch(name or ""):
        raise RemoteTrainingValidationError(
            "数据集名称仅支持字母、数字、下划线、连字符"
        )
    return name


def _total_part_count(file_size: int, part_size: int) -> int:
    """按文件大小和 part_size 计算分片总数。"""
    return (file_size + part_size - 1) // part_size


def _normalize_upload_mode(upload_mode: str | None) -> str:
    mode = (upload_mode or "multipart").strip().lower()
    if mode not in SUPPORTED_UPLOAD_MODES:
        raise RemoteTrainingValidationError("upload_mode 仅支持 multipart 或 presigned_put")
    return mode


def _normalize_part_size(part_size: int | None) -> int:
    size = part_size or DEFAULT_MULTIPART_PART_SIZE
    if size < MIN_MULTIPART_PART_SIZE:
        raise RemoteTrainingValidationError("part_size 不能小于 5MiB")
    return size


class RemoteTrainingService:
    """远程训练主服务。

    该服务不在 Web 进程中执行训练，只负责 OSS 与 PAI-DLC 编排。
    """

    def __init__(
        self,
        settings: RemoteTrainSettings | None = None,
        storage: OssStorageGateway | None = None,
        dlc: PaiDlcGateway | None = None,
    ):
        self.settings = settings or load_remote_train_settings()
        self._storage = storage
        self._dlc = dlc

    @property
    def storage(self) -> OssStorageGateway:
        """延迟创建 OSS 客户端。

        这样导入模块不会立即要求 OSS 环境变量完整，只有真实调用远程接口时才检查配置。
        """
        if self._storage is None:
            self._storage = OssStorageGateway(self.settings)
        return self._storage

    @property
    def dlc(self) -> PaiDlcGateway:
        """延迟创建 PAI-DLC 客户端，避免应用启动时访问云 SDK。"""
        if self._dlc is None:
            self._dlc = PaiDlcGateway(self.settings)
        return self._dlc

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
                self.storage.abort_multipart_upload(
                    upload.raw_object_key,
                    metadata["multipart_upload_id"],
                )
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
        upload_mode: str | None = "multipart",
        part_size: int | None = None,
    ) -> dict[str, Any]:
        """创建数据集上传会话。

        关键约束：
        - 后端生成 upload_id、dataset_id 和固定 OSS object key。
        - 客户端只拿到上传凭据，不拿 AccessKey 或 STS token。
        - 生产前端默认分片上传；每个 part 都需要独立签名。
        - 只有 OSS CompleteMultipartUpload 事件能推进到 UPLOADED。
        - presigned_put 只作为小文件和 SDK 连通性测试的兼容路径。
        """
        dataset_name = _safe_dataset_name(dataset_name)
        if not filename.endswith(".zip"):
            raise RemoteTrainingValidationError("远程训练数据集目前只接受 .zip 包")
        mode = _normalize_upload_mode(upload_mode)
        resolved_part_size = _normalize_part_size(part_size) if mode == "multipart" else None
        if expected_size and resolved_part_size:
            total_parts = _total_part_count(expected_size, resolved_part_size)
            if total_parts > MAX_MULTIPART_PARTS:
                raise RemoteTrainingValidationError(
                    "文件分片数量超过 OSS 上限，请增大 part_size"
                )
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

        if mode == "presigned_put":
            url = self.storage.sign_put_url(
                raw_key,
                self.settings.upload_url_expires_seconds,
                headers=headers,
            )
            upload.object_metadata = {
                "upload_mode": mode,
                "headers": headers,
            }
            db.commit()
            db.refresh(upload)
            return {
                **self._upload_response_base(upload),
                "upload_mode": mode,
                "server_confirm_event": OSS_MULTIPART_COMPLETE_EVENT,
                "upload": {
                    "method": "PUT",
                    "url": url,
                    "headers": headers,
                },
            }

        try:
            oss_upload_id = self.storage.init_multipart_upload(raw_key, headers=headers)
        except Exception:
            upload.status = "FAILED"
            upload.error_message = "OSS 初始化分片上传失败"
            upload.updated_at = _now()
            db.commit()
            raise

        upload.object_metadata = {
            "upload_mode": mode,
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
            "upload_mode": mode,
            "server_confirm_event": OSS_MULTIPART_COMPLETE_EVENT,
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
        normalized_part_numbers = self._normalize_part_numbers(part_numbers)
        expires = expires_seconds or self.settings.upload_url_expires_seconds
        if expires < 60 or expires > self.settings.upload_url_expires_seconds:
            raise RemoteTrainingValidationError("expires_seconds 超出允许范围")

        oss_upload_id = metadata["multipart_upload_id"]
        signed_parts = [
            {
                "part_number": part_number,
                "method": "PUT",
                "url": self.storage.sign_upload_part_url(
                    upload.raw_object_key,
                    oss_upload_id,
                    part_number,
                    expires,
                ),
                "headers": {},
                "expires_seconds": expires,
            }
            for part_number in normalized_part_numbers
        ]
        if upload.status == "INITIATED":
            upload.status = "UPLOADING"
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return {
            **self._upload_response_base(upload),
            "upload_mode": "multipart",
            "oss_upload_id": oss_upload_id,
            "part_size": metadata["part_size"],
            "parts": signed_parts,
        }

    def complete_multipart_upload(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """由后端调用 OSS CompleteMultipartUpload 合并分片。

        该接口成功只说明合并请求成功提交；业务状态仍先记为 CLIENT_COMPLETED，
        等 OSS CompleteMultipartUpload 事件回调后才进入 UPLOADED。
        """
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        metadata = self._require_multipart_metadata(upload)
        self._ensure_upload_can_accept_parts(upload)
        normalized_parts = self._normalize_completed_parts(parts)
        expected_size = upload.expected_size
        part_size = int(metadata["part_size"])
        if expected_size:
            expected_parts = _total_part_count(expected_size, part_size)
            if len(normalized_parts) != expected_parts:
                raise RemoteTrainingValidationError("分片数量与 expected_size 不匹配")

        result = self.storage.complete_multipart_upload(
            upload.raw_object_key,
            metadata["multipart_upload_id"],
            normalized_parts,
        )
        db.refresh(upload)
        if upload.status not in {"UPLOADED", "READY"}:
            upload.status = "CLIENT_COMPLETED"
        upload.client_completed_at = upload.client_completed_at or _now()
        upload.etag = result.get("etag") or upload.etag
        upload.object_metadata = {
            **(upload.object_metadata or {}),
            "multipart_completed_at": _now().isoformat(),
            "completed_part_count": len(normalized_parts),
            "complete_request_id": result.get("request_id"),
        }
        upload.error_message = None
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return {
            "message": "OSS 分片已合并，等待 OSS 完成事件确认",
            "oss_complete": result,
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
        if upload.status in {"UPLOADED", "READY"}:
            raise RemoteTrainingValidationError("上传已确认，不能取消分片上传")
        if upload.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            return self.serialize_upload(upload)

        self.storage.abort_multipart_upload(
            upload.raw_object_key,
            metadata["multipart_upload_id"],
        )
        upload.status = "CANCELLED"
        upload.object_metadata = {
            **(upload.object_metadata or {}),
            "multipart_aborted_at": _now().isoformat(),
        }
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return self.serialize_upload(upload)

    def complete_dataset_upload(
        self, db: Session, user_id: int, upload_uuid: str
    ) -> dict[str, Any]:
        """记录客户端上传 SDK 已完成。

        这个信号只用于 UI 展示，不能作为可信上传成功依据。
        真正的 UPLOADED 状态只能由 OSS CompleteMultipartUpload 事件推进。
        """
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        if upload.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            raise RemoteTrainingValidationError("上传会话已结束，不能确认上传")
        if upload.status not in {"UPLOADED", "READY"}:
            upload.status = "CLIENT_COMPLETED"
        upload.client_completed_at = upload.client_completed_at or _now()
        upload.error_message = None
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

    def handle_oss_multipart_upload_event(
        self,
        db: Session,
        event_type: str,
        bucket: str,
        object_key: str,
        size: int | None = None,
        etag: str | None = None,
        event_id: str | None = None,
        event_time: str | None = None,
    ) -> dict[str, Any]:
        """处理 OSS 分片上传完成事件。

        只接受 CompleteMultipartUpload。object_key 前缀不在这里写死，
        而是用数据库中创建上传会话时保存的 bucket/raw_object_key 做精确匹配。
        """
        if event_type != OSS_MULTIPART_COMPLETE_EVENT:
            return {
                "ignored": True,
                "reason": "unsupported_event_type",
                "event_type": event_type,
            }

        upload = (
            db.query(DatasetUpload)
            .filter(
                DatasetUpload.bucket == bucket,
                DatasetUpload.raw_object_key == object_key,
            )
            .first()
        )
        if not upload:
            raise RemoteTrainingValidationError("上传会话不存在")
        if upload.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            raise RemoteTrainingValidationError("上传会话已结束，不能更新状态")

        upload.status = "UPLOADED"
        if size is not None:
            upload.actual_size = size
        if etag:
            upload.etag = etag
        upload.server_verified_at = _now()
        upload.object_metadata = {
            **(upload.object_metadata or {}),
            "oss_event_id": event_id,
            "oss_event_type": event_type,
            "oss_event_time": event_time,
        }
        upload.error_message = None
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return {
            "ignored": False,
            "upload": self.serialize_upload(upload),
        }

    def mark_dataset_ready(
        self,
        db: Session,
        user_id: int,
        upload_uuid: str,
        processed_prefix: str | None = None,
        manifest_key: str | None = None,
        success_key: str | None = None,
        verify_objects: bool = True,
    ) -> dict[str, Any]:
        """把处理后的数据集标记为 READY。

        当前实现允许测试程序直接传入 processed_prefix。生产环境应由预处理
        PAI-DLC 任务在写出 manifest.json 与 _SUCCESS 后触发。
        """
        upload = self._get_upload_for_user(db, user_id, upload_uuid)
        prefix = processed_prefix or (
            f"{_normalize_prefix(self.settings.oss_prefix)}datasets/processed/"
            f"{upload.dataset_name}/{upload.dataset_uuid}/"
        )
        prefix = _normalize_prefix(prefix)
        manifest = manifest_key or prefix + "manifest.json"
        success = success_key or prefix + "_SUCCESS"
        if verify_objects:
            missing = [
                key for key in [manifest, success] if not self.storage.exists(key)
            ]
            if missing:
                logger.warning(
                    "处理后数据集缺少 OSS 对象: upload_uuid=%s missing=%s",
                    upload.upload_uuid,
                    missing,
                )
                raise RemoteTrainingValidationError(
                    "处理后数据集缺少 manifest.json 或 _SUCCESS"
                )
        upload.status = "READY"
        upload.processed_prefix = prefix
        upload.manifest_key = manifest
        upload.success_key = success
        upload.ready_at = _now()
        upload.updated_at = _now()
        db.commit()
        db.refresh(upload)
        return self.serialize_upload(upload)

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
        if upload.status not in {"UPLOADED", "READY"}:
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
            task.error_message = "远程训练失败，请联系管理员查看后端日志"
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
            task.error_message = "远程回调报告训练失败"
        elif remote_job.remote_status == "STOPPED":
            task.status = "cancelled"
        db.commit()
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._latest_metric_payload(db, task.id),
        )

    def handle_metric_callback(
        self,
        db: Session,
        task_uuid: str,
        token: str,
        epoch: int,
        total_epochs: int | None,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """接收 PAI-DLC 容器按 epoch 上报的训练监控指标。"""
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

        metric = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task.id, TrainingMetric.epoch == epoch)
            .first()
        )
        if not metric:
            metric = TrainingMetric(task_id=task.id, epoch=epoch)
            db.add(metric)
        for field in [
            "box_loss",
            "cls_loss",
            "dfl_loss",
            "precision",
            "recall",
            "map50",
            "map50_95",
            "lr",
        ]:
            value = metrics.get(field)
            if value is not None:
                setattr(metric, field, float(value))

        total = total_epochs or task.epochs or 1
        task.current_epoch = max(task.current_epoch or 0, epoch)
        task.progress = max(
            task.progress or 0,
            min(int((task.current_epoch / total) * 100), 99),
        )
        if task.status not in {"completed", "failed", "cancelled"}:
            task.status = "running"
        task.started_at = task.started_at or _now()
        task.updated_at = _now()
        if remote_job.remote_status not in {"SUCCEEDED", "FAILED", "STOPPED"}:
            remote_job.remote_status = "RUNNING"
        remote_job.last_synced_at = _now()
        remote_job.updated_at = _now()
        db.commit()
        db.refresh(task)
        db.refresh(remote_job)
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._metric_payload(metric),
        )

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

    def _build_job_envs(
        self,
        task: TrainingTask,
        upload: DatasetUpload,
        output_prefix: str,
        callback_token: str,
    ) -> dict[str, str]:
        """构造注入 DLC 容器的环境变量。

        这里只放任务 ID、OSS 前缀和训练参数等小型配置，不放长期 AccessKey。
        """
        return {
            "TASK_ID": str(task.id),
            "TASK_UUID": task.task_uuid,
            "DATASET_ID": upload.dataset_uuid or "",
            "DATASET_PREFIX": upload.processed_prefix
            or _object_parent_prefix(upload.raw_object_key),
            "RAW_OBJECT_KEY": upload.raw_object_key,
            "RAW_DATASET_FILENAME": upload.raw_object_key.rsplit("/", 1)[-1],
            "OUTPUT_PREFIX": output_prefix,
            "OSS_BUCKET": self.settings.oss_bucket,
            "CALLBACK_TOKEN": callback_token,
            "METRICS_CALLBACK_URL": self.settings.remote_metrics_callback_url,
            "CALLBACK_TIMEOUT_SECONDS": "5",
            "MODEL_NAME": task.model_name,
            "EPOCHS": str(task.epochs),
            "IMG_SIZE": str(task.img_size),
            "BATCH_SIZE": str(task.batch_size),
        }

    def _build_user_command(self, task: TrainingTask) -> str:
        """生成 Ultralytics 训练命令。

        DLC 会把数据集输入前缀和 output prefix 分别挂载到固定目录。
        当前支持两种输入：
        - processed 前缀下已有 data.yaml。
        - 数据集对象所在前缀下只有 dataset.zip，命令先安全解压到容器临时目录。
        """
        dataset = self.settings.pai_dataset_mount_path.rstrip("/")
        output = self.settings.pai_output_mount_path.rstrip("/")
        model = task.model_name
        if not model.endswith(".pt"):
            model = model + ".pt"
        optimizer = task.optimizer or "SGD"
        lr0 = task.lr0 if task.lr0 is not None else 0.01
        return (
            "set -e; "
            f"mkdir -p {output}/weights {output}/dataset; "
            f"python - <<'PY'\n"
            f"import json, os, platform, shutil, sys, time, traceback, zipfile\n"
            f"mount_dir = {dataset!r}\n"
            f"output_dir = {output!r}\n"
            f"work_dir = '/tmp/remote_train_dataset'\n"
            f"def write_error(stage, exc, extra=None):\n"
            f"    payload = {{\n"
            f"        'ok': False,\n"
            f"        'stage': stage,\n"
            f"        'error_type': type(exc).__name__,\n"
            f"        'error': str(exc),\n"
            f"        'task_uuid': os.environ.get('TASK_UUID'),\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'raw_object_key': os.environ.get('RAW_OBJECT_KEY'),\n"
            f"        'dataset_prefix': os.environ.get('DATASET_PREFIX'),\n"
            f"        'output_prefix': os.environ.get('OUTPUT_PREFIX'),\n"
            f"        'mount_dir': mount_dir,\n"
            f"        'output_dir': output_dir,\n"
            f"        'python': sys.version,\n"
            f"        'platform': platform.platform(),\n"
            f"        'traceback': traceback.format_exc(),\n"
            f"    }}\n"
            f"    if extra:\n"
            f"        payload.update(extra)\n"
            f"    text = json.dumps(payload, ensure_ascii=False, indent=2)\n"
            f"    print('REMOTE_TRAIN_ERROR ' + text, flush=True)\n"
            f"    try:\n"
            f"        os.makedirs(os.path.join(output_dir, 'dataset'), exist_ok=True)\n"
            f"        open(os.path.join(output_dir, 'dataset', 'train_error.json'), 'w').write(text)\n"
            f"    except Exception as write_exc:\n"
            f"        print('REMOTE_TRAIN_ERROR_WRITE_FAILED ' + repr(write_exc), flush=True)\n"
            f"def list_tree(root, max_entries=80, max_depth=3):\n"
            f"    entries = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return entries\n"
            f"    for current, dirs, files in os.walk(root):\n"
            f"        rel = os.path.relpath(current, root)\n"
            f"        depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
            f"        if depth >= max_depth:\n"
            f"            dirs[:] = []\n"
            f"        prefix = '' if rel == '.' else rel + '/'\n"
            f"        for name in sorted(dirs):\n"
            f"            entries.append(prefix + name + '/')\n"
            f"        for name in sorted(files):\n"
            f"            entries.append(prefix + name)\n"
            f"        if len(entries) >= max_entries:\n"
            f"            return entries[:max_entries]\n"
            f"    return entries\n"
            f"def find_named(root, names):\n"
            f"    wanted = {{name.lower() for name in names if name}}\n"
            f"    matches = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return matches\n"
            f"    for current, _, files in os.walk(root):\n"
            f"        for name in files:\n"
            f"            if name.lower() in wanted:\n"
            f"                matches.append(os.path.join(current, name))\n"
            f"    return sorted(matches, key=lambda path: (len(path), path))\n"
            f"def find_suffix(root, suffix):\n"
            f"    matches = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return matches\n"
            f"    for current, _, files in os.walk(root):\n"
            f"        for name in files:\n"
            f"            if name.lower().endswith(suffix):\n"
            f"                matches.append(os.path.join(current, name))\n"
            f"    return sorted(matches, key=lambda path: (len(path), path))\n"
            f"try:\n"
            f"    env_summary = {{key: os.environ.get(key) for key in ['TASK_ID', 'TASK_UUID', 'DATASET_ID', 'DATASET_PREFIX', 'RAW_OBJECT_KEY', 'RAW_DATASET_FILENAME', 'OUTPUT_PREFIX', 'MODEL_NAME', 'EPOCHS', 'IMG_SIZE', 'BATCH_SIZE']}}\n"
            f"    print('remote train env: ' + json.dumps(env_summary, ensure_ascii=False), flush=True)\n"
            f"    print('dataset mount dir: ' + mount_dir, flush=True)\n"
            f"    print('dataset mount exists: ' + str(os.path.exists(mount_dir)), flush=True)\n"
            f"    print('dataset mount isdir: ' + str(os.path.isdir(mount_dir)), flush=True)\n"
            f"    print('dataset mount entries: ' + json.dumps(list_tree(mount_dir), ensure_ascii=False), flush=True)\n"
            f"    print('output mount dir: ' + output_dir, flush=True)\n"
            f"    print('output mount exists: ' + str(os.path.exists(output_dir)), flush=True)\n"
            f"    print('output mount isdir: ' + str(os.path.isdir(output_dir)), flush=True)\n"
            f"    data_yaml_candidates = find_named(mount_dir, ['data.yaml'])\n"
            f"    if data_yaml_candidates:\n"
            f"        data_yaml = data_yaml_candidates[0]\n"
            f"    else:\n"
            f"        zip_name = os.environ.get('RAW_DATASET_FILENAME', 'dataset.zip')\n"
            f"        zip_candidates = find_named(mount_dir, [zip_name, 'dataset.zip']) or find_suffix(mount_dir, '.zip')\n"
            f"        print('dataset zip candidates: ' + json.dumps(zip_candidates[:20], ensure_ascii=False), flush=True)\n"
            f"        if not zip_candidates:\n"
            f"            raise FileNotFoundError('数据集 ZIP 不存在')\n"
            f"        zip_path = zip_candidates[0]\n"
            f"        print('dataset zip path: ' + zip_path, flush=True)\n"
            f"        if os.path.exists(work_dir):\n"
            f"            shutil.rmtree(work_dir)\n"
            f"        os.makedirs(work_dir, exist_ok=True)\n"
            f"        with zipfile.ZipFile(zip_path) as zf:\n"
            f"            zip_entries = zf.namelist()[:80]\n"
            f"            print('dataset zip entries: ' + json.dumps(zip_entries, ensure_ascii=False), flush=True)\n"
            f"            for member in zf.infolist():\n"
            f"                name = member.filename\n"
            f"                parts = [part for part in name.split('/') if part]\n"
            f"                if name.startswith('/') or '..' in parts:\n"
            f"                    raise ValueError('ZIP 包含不安全路径')\n"
            f"            zf.extractall(work_dir)\n"
            f"        print('extracted dataset entries: ' + json.dumps(list_tree(work_dir), ensure_ascii=False), flush=True)\n"
            f"        data_yaml_candidates = find_named(work_dir, ['data.yaml'])\n"
            f"        if not data_yaml_candidates:\n"
            f"            raise FileNotFoundError('data.yaml 不存在')\n"
            f"        data_yaml = data_yaml_candidates[0]\n"
            f"    print('resolved data.yaml: ' + data_yaml, flush=True)\n"
            f"    report = {{\n"
            f"        'ok': True,\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'upload_id': os.environ.get('RAW_OBJECT_KEY'),\n"
            f"        'data_yaml': data_yaml,\n"
            f"        'mount_dir': mount_dir,\n"
            f"        'mount_entries': list_tree(mount_dir),\n"
            f"        'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),\n"
            f"    }}\n"
            f"    open(os.path.join(output_dir, 'dataset', 'validation_report.json'), 'w').write(json.dumps(report, ensure_ascii=False, indent=2))\n"
            f"    open('/tmp/remote_data_yaml_path', 'w').write(data_yaml)\n"
            f"except Exception as exc:\n"
            f"    write_error('prepare_dataset', exc, {{'mount_entries': list_tree(mount_dir), 'output_entries': list_tree(output_dir), 'work_entries': list_tree(work_dir)}})\n"
            f"    raise\n"
            f"PY\n"
            f"python - <<'PY'\n"
            f"import json, os, platform, sys, traceback, urllib.request\n"
            f"data_yaml = open('/tmp/remote_data_yaml_path', encoding='utf-8').read().strip()\n"
            f"output_dir = {output!r}\n"
            f"model_name = {model!r}\n"
            f"epochs = {int(task.epochs)}\n"
            f"img_size = {int(task.img_size)}\n"
            f"batch_size = {int(task.batch_size)}\n"
            f"optimizer = {optimizer!r}\n"
            f"lr0 = {float(lr0)!r}\n"
            f"def write_error(stage, exc):\n"
            f"    payload = {{\n"
            f"        'ok': False,\n"
            f"        'stage': stage,\n"
            f"        'error_type': type(exc).__name__,\n"
            f"        'error': str(exc),\n"
            f"        'task_uuid': os.environ.get('TASK_UUID'),\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'data_yaml': data_yaml,\n"
            f"        'model_name': model_name,\n"
            f"        'epochs': epochs,\n"
            f"        'img_size': img_size,\n"
            f"        'batch_size': batch_size,\n"
            f"        'optimizer': optimizer,\n"
            f"        'lr0': lr0,\n"
            f"        'python': sys.version,\n"
            f"        'platform': platform.platform(),\n"
            f"        'traceback': traceback.format_exc(),\n"
            f"    }}\n"
            f"    text = json.dumps(payload, ensure_ascii=False, indent=2)\n"
            f"    print('REMOTE_TRAIN_ERROR ' + text, flush=True)\n"
            f"    try:\n"
            f"        os.makedirs(output_dir, exist_ok=True)\n"
            f"        open(os.path.join(output_dir, 'train_error.json'), 'w').write(text)\n"
            f"    except Exception as write_exc:\n"
            f"        print('REMOTE_TRAIN_ERROR_WRITE_FAILED ' + repr(write_exc), flush=True)\n"
            f"def to_float(value):\n"
            f"    if value is None:\n"
            f"        return None\n"
            f"    try:\n"
            f"        if hasattr(value, 'item'):\n"
            f"            value = value.item()\n"
            f"        return float(value)\n"
            f"    except Exception:\n"
            f"        return None\n"
            f"def first_float(*values):\n"
            f"    for value in values:\n"
            f"        parsed = to_float(value)\n"
            f"        if parsed is not None:\n"
            f"            return parsed\n"
            f"    return None\n"
            f"def collect_metrics(trainer):\n"
            f"    data = {{}}\n"
            f"    raw = getattr(trainer, 'metrics', {{}}) or {{}}\n"
            f"    if isinstance(raw, dict):\n"
            f"        data.update(raw)\n"
            f"    for attr in ('tloss', 'loss_items'):\n"
            f"        try:\n"
            f"            items = trainer.label_loss_items(getattr(trainer, attr), prefix='train')\n"
            f"            if isinstance(items, dict):\n"
            f"                data.update(items)\n"
            f"        except Exception:\n"
            f"            pass\n"
            f"    return data\n"
            f"def get_lr(trainer):\n"
            f"    lr = getattr(trainer, 'lr', None)\n"
            f"    if isinstance(lr, dict):\n"
            f"        values = [lr.get('lr/pg0'), lr.get('pg0')] + list(lr.values())\n"
            f"        return first_float(*values)\n"
            f"    return to_float(lr)\n"
            f"def post_metric(payload):\n"
            f"    url = os.environ.get('METRICS_CALLBACK_URL')\n"
            f"    token = os.environ.get('CALLBACK_TOKEN')\n"
            f"    task_uuid = os.environ.get('TASK_UUID')\n"
            f"    if not url or not token or not task_uuid:\n"
            f"        return\n"
            f"    payload.update({{'task_uuid': task_uuid, 'token': token, 'total_epochs': epochs}})\n"
            f"    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')\n"
            f"    request = urllib.request.Request(url, data=body, headers={{'Content-Type': 'application/json'}}, method='POST')\n"
            f"    timeout = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '5'))\n"
            f"    try:\n"
            f"        with urllib.request.urlopen(request, timeout=timeout) as response:\n"
            f"            response.read()\n"
            f"    except Exception as exc:\n"
            f"        print('metric callback failed: ' + type(exc).__name__, flush=True)\n"
            f"def on_train_epoch_end(trainer):\n"
            f"    epoch = int(getattr(trainer, 'epoch', 0)) + 1\n"
            f"    data = collect_metrics(trainer)\n"
            f"    post_metric({{\n"
            f"        'epoch': epoch,\n"
            f"        'box_loss': first_float(data.get('train/box_loss'), data.get('metrics/box_loss'), data.get('box_loss')),\n"
            f"        'cls_loss': first_float(data.get('train/cls_loss'), data.get('metrics/cls_loss'), data.get('cls_loss')),\n"
            f"        'dfl_loss': first_float(data.get('train/dfl_loss'), data.get('metrics/dfl_loss'), data.get('dfl_loss')),\n"
            f"        'precision': first_float(data.get('metrics/precision(B)'), data.get('precision')),\n"
            f"        'recall': first_float(data.get('metrics/recall(B)'), data.get('recall')),\n"
            f"        'map50': first_float(data.get('metrics/mAP50(B)'), data.get('map50')),\n"
            f"        'map50_95': first_float(data.get('metrics/mAP50-95(B)'), data.get('map50_95')),\n"
            f"        'lr': get_lr(trainer),\n"
            f"    }})\n"
            f"try:\n"
            f"    print('training config: ' + json.dumps({{'data_yaml': data_yaml, 'model_name': model_name, 'epochs': epochs, 'img_size': img_size, 'batch_size': batch_size, 'optimizer': optimizer, 'lr0': lr0}}, ensure_ascii=False), flush=True)\n"
            f"    from ultralytics import YOLO\n"
            f"    model = YOLO(model_name)\n"
            f"    model.add_callback('on_train_epoch_end', on_train_epoch_end)\n"
            f"    model.train(data=data_yaml, epochs=epochs, imgsz=img_size, batch=batch_size, optimizer=optimizer, lr0=lr0, project=output_dir, name='run', exist_ok=True, verbose=True, save=True, plots=False)\n"
            f"except Exception as exc:\n"
            f"    write_error('train_yolo', exc)\n"
            f"    raise\n"
            f"PY\n"
            f"python - <<'PY'\n"
            f"import json, os, shutil, time, traceback\n"
            f"output_dir = {output!r}\n"
            f"def list_tree(root, max_entries=80, max_depth=3):\n"
            f"    entries = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return entries\n"
            f"    for current, dirs, files in os.walk(root):\n"
            f"        rel = os.path.relpath(current, root)\n"
            f"        depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
            f"        if depth >= max_depth:\n"
            f"            dirs[:] = []\n"
            f"        prefix = '' if rel == '.' else rel + '/'\n"
            f"        for name in sorted(dirs):\n"
            f"            entries.append(prefix + name + '/')\n"
            f"        for name in sorted(files):\n"
            f"            entries.append(prefix + name)\n"
            f"        if len(entries) >= max_entries:\n"
            f"            return entries[:max_entries]\n"
            f"    return entries\n"
            f"def fail(message):\n"
            f"    payload = {{\n"
            f"        'ok': False,\n"
            f"        'stage': 'collect_artifacts',\n"
            f"        'error': message,\n"
            f"        'task_uuid': os.environ.get('TASK_UUID'),\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'output_dir': output_dir,\n"
            f"        'output_entries': list_tree(output_dir),\n"
            f"        'run_entries': list_tree(os.path.join(output_dir, 'run')),\n"
            f"        'traceback': traceback.format_exc(),\n"
            f"    }}\n"
            f"    text = json.dumps(payload, ensure_ascii=False, indent=2)\n"
            f"    print('REMOTE_TRAIN_ERROR ' + text, flush=True)\n"
            f"    try:\n"
            f"        open(os.path.join(output_dir, 'train_error.json'), 'w').write(text)\n"
            f"    except Exception as write_exc:\n"
            f"        print('REMOTE_TRAIN_ERROR_WRITE_FAILED ' + repr(write_exc), flush=True)\n"
            f"    raise FileNotFoundError(message)\n"
            f"print('artifact output entries: ' + json.dumps(list_tree(output_dir), ensure_ascii=False), flush=True)\n"
            f"results_src = os.path.join(output_dir, 'run', 'results.csv')\n"
            f"best_src = os.path.join(output_dir, 'run', 'weights', 'best.pt')\n"
            f"last_src = os.path.join(output_dir, 'run', 'weights', 'last.pt')\n"
            f"if not os.path.exists(results_src):\n"
            f"    fail('results.csv 不存在: ' + results_src)\n"
            f"if not os.path.exists(best_src):\n"
            f"    fail('best.pt 不存在: ' + best_src)\n"
            f"os.makedirs(os.path.join(output_dir, 'weights'), exist_ok=True)\n"
            f"shutil.copyfile(results_src, os.path.join(output_dir, 'results.csv'))\n"
            f"shutil.copyfile(best_src, os.path.join(output_dir, 'weights', 'best.pt'))\n"
            f"if os.path.exists(last_src):\n"
            f"    shutil.copyfile(last_src, os.path.join(output_dir, 'weights', 'last.pt'))\n"
            f"payload={{'task_uuid':os.environ.get('TASK_UUID'),"
            f"'dataset_id':os.environ.get('DATASET_ID'),"
            f"'finished_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}}\n"
            f"open(os.path.join(output_dir, '_SUCCESS'),'w').write(json.dumps(payload))\n"
            f"PY"
        )

    def _build_create_job_payload(self, remote_job: RemoteTrainingJob) -> dict[str, Any]:
        """构造 PAI-DLC CreateJob payload。

        重点字段：
        - JobSpecs.Image：完整镜像地址，不是 PAI 自定义镜像 ID。
        - DataSources：把 OSS 数据集和输出前缀挂载到容器目录。
        - ImageConfig：只有 ACR 需要认证时才传。
        """
        job_spec: dict[str, Any] = {
            "Type": "Worker",
            "Image": self.settings.pai_image_uri,
            "PodCount": self.settings.pai_pod_count,
        }
        if self.settings.pai_ecs_spec:
            job_spec["EcsSpec"] = self.settings.pai_ecs_spec
        if self.settings.pai_acr_username and self.settings.pai_acr_password:
            job_spec["ImageConfig"] = {
                "DockerRegistry": self.settings.pai_acr_registry,
                "Username": self.settings.pai_acr_username,
                "Password": self.settings.pai_acr_password,
            }

        data_sources = [
            {
                "Uri": _dlc_oss_uri(
                    self.settings.oss_bucket,
                    self.settings.pai_oss_endpoint,
                    remote_job.input_dataset_prefix,
                    self.settings.pai_oss_uri_host,
                ),
                "MountPath": self.settings.pai_dataset_mount_path,
            },
            {
                "Uri": _dlc_oss_uri(
                    self.settings.oss_bucket,
                    self.settings.pai_oss_endpoint,
                    remote_job.output_prefix,
                    self.settings.pai_oss_uri_host,
                ),
                "MountPath": self.settings.pai_output_mount_path,
            },
        ]

        payload: dict[str, Any] = {
            "WorkspaceId": self.settings.pai_workspace_id,
            "DisplayName": f"chestx-train-{remote_job.training_task.task_uuid}",
            "JobType": self.settings.pai_job_type,
            "JobSpecs": [job_spec],
            "UserCommand": remote_job.user_command,
            "Envs": remote_job.envs or {},
            "JobMaxRunningTimeMinutes": self.settings.pai_job_max_running_minutes,
            "DataSources": data_sources,
        }
        if self.settings.pai_resource_id:
            payload["ResourceId"] = self.settings.pai_resource_id
        logger.info("PAI-DLC CreateJob DataSources: %s", data_sources)
        return payload

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

    def _sync_results_csv_metrics(
        self, db: Session, task: TrainingTask, remote_job: RemoteTrainingJob
    ) -> None:
        """从远程 results.csv 增量同步训练指标到 training_metrics。

        以 task_id + epoch 做幂等控制，避免轮询和 callback 重复触发时重复写入。
        """
        if not remote_job.results_csv_key or not self.storage.exists(remote_job.results_csv_key):
            return
        try:
            text = self.storage.get_text(remote_job.results_csv_key)
        except Exception:
            return
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        if len(rows) < 2:
            return
        headers = [h.strip() for h in rows[0].split(",")]
        existing_epochs = {
            item.epoch
            for item in db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task.id)
            .all()
        }
        max_epoch = task.current_epoch or 0
        for line in rows[1:]:
            values = [value.strip() for value in line.split(",")]
            row = dict(zip(headers, values))
            epoch_raw = row.get("epoch") or row.get("                  epoch")
            if epoch_raw is None:
                continue
            try:
                epoch = int(float(epoch_raw))
            except ValueError:
                continue
            if epoch in existing_epochs:
                max_epoch = max(max_epoch, epoch)
                continue
            metric = TrainingMetric(
                task_id=task.id,
                epoch=epoch,
                box_loss=self._float(row.get("train/box_loss")),
                cls_loss=self._float(row.get("train/cls_loss")),
                dfl_loss=self._float(row.get("train/dfl_loss")),
                precision=self._float(row.get("metrics/precision(B)")),
                recall=self._float(row.get("metrics/recall(B)")),
                map50=self._float(row.get("metrics/mAP50(B)")),
                map50_95=self._float(row.get("metrics/mAP50-95(B)")),
                lr=self._float(row.get("lr/pg0")),
            )
            db.add(metric)
            existing_epochs.add(epoch)
            max_epoch = max(max_epoch, epoch)
        if max_epoch:
            task.current_epoch = max(task.current_epoch or 0, max_epoch)
            if task.status != "completed":
                task.progress = max(
                    task.progress or 0,
                    min(int((task.current_epoch / max(task.epochs or 1, 1)) * 100), 99),
                )

    @staticmethod
    def _float(value: str | None) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _metric_payload(metric: TrainingMetric | None) -> dict[str, Any] | None:
        if not metric:
            return None
        return {
            "epoch": metric.epoch,
            "box_loss": metric.box_loss,
            "cls_loss": metric.cls_loss,
            "dfl_loss": metric.dfl_loss,
            "precision": metric.precision,
            "recall": metric.recall,
            "map50": metric.map50,
            "map50_95": metric.map50_95,
            "lr": metric.lr,
        }

    def _latest_metric_payload(
        self, db: Session, task_id: int
    ) -> dict[str, Any] | None:
        metric = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.desc())
            .first()
        )
        return self._metric_payload(metric)

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
        if upload.status in {"CLIENT_COMPLETED", "UPLOADED", "READY"}:
            raise RemoteTrainingValidationError("上传会话已完成，不能继续分片上传")

    def _normalize_part_numbers(self, part_numbers: list[int]) -> list[int]:
        """校验并去重 partNumber。

        OSS multipart 的 partNumber 范围是 1 到 10000。这里不接受重复值，
        避免前端拿到两份同一 part 的签名后覆盖上传结果。
        """
        if not part_numbers:
            raise RemoteTrainingValidationError("part_numbers 不能为空")
        if len(part_numbers) > MAX_PARTS_PER_SIGN:
            raise RemoteTrainingValidationError(
                f"单次最多签名 {MAX_PARTS_PER_SIGN} 个 part"
            )
        normalized: list[int] = []
        seen: set[int] = set()
        for value in part_numbers:
            if not isinstance(value, int):
                raise RemoteTrainingValidationError("part_number 必须是整数")
            if value < 1 or value > MAX_MULTIPART_PARTS:
                raise RemoteTrainingValidationError("part_number 必须在 1 到 10000 之间")
            if value in seen:
                raise RemoteTrainingValidationError("part_numbers 不能包含重复值")
            seen.add(value)
            normalized.append(value)
        return sorted(normalized)

    def _normalize_completed_parts(
        self,
        parts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """校验客户端上报的已上传 part 信息。"""
        if not parts:
            raise RemoteTrainingValidationError("parts 不能为空")
        if len(parts) > MAX_MULTIPART_PARTS:
            raise RemoteTrainingValidationError("parts 数量超过 OSS 上限")

        normalized: list[dict[str, Any]] = []
        seen: set[int] = set()
        for part in parts:
            part_number = part.get("part_number")
            etag = str(part.get("etag") or "").strip()
            if not isinstance(part_number, int):
                raise RemoteTrainingValidationError("part_number 必须是整数")
            if part_number < 1 or part_number > MAX_MULTIPART_PARTS:
                raise RemoteTrainingValidationError("part_number 必须在 1 到 10000 之间")
            if part_number in seen:
                raise RemoteTrainingValidationError("parts 不能包含重复 part_number")
            if not etag or len(etag) > 256:
                raise RemoteTrainingValidationError("part etag 无效")
            seen.add(part_number)
            normalized.append({"part_number": part_number, "etag": etag})
        return sorted(normalized, key=lambda item: item["part_number"])

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

    def serialize_upload(self, upload: DatasetUpload) -> dict[str, Any]:
        return {
            "upload_id": upload.upload_uuid,
            "dataset_id": upload.dataset_uuid,
            "name": upload.dataset_name,
            "dataset_name": upload.dataset_name,
            "status": upload.status,
            "has_data": upload.status in {"UPLOADED", "READY"},
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


remote_training_service = RemoteTrainingService()
