"""远程训练 API 路由。"""

from __future__ import annotations

import hmac
from typing import Any

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import get_db
from app.entity.schemas import Yolo11ModelName
from app.train.remote_train_config import RemoteTrainConfigError
from app.train.remote_train_service import (
    RemoteTrainingValidationError,
    remote_training_service,
)
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/training/remote", tags=["远程训练"])
logger = get_logger(__name__)


class RemoteDatasetUploadCreate(BaseModel):
    """创建远程数据集上传会话的请求。

    该接口只创建数据库记录和 OSS 上传凭据，不接收文件内容。
    默认使用 multipart。presigned_put 仅用于小文件和 OSS 连通性测试。
    """

    scene_id: int | None = Field(None, description="检测场景 ID；与 scene_name 二选一")
    scene_name: str | None = Field(
        None, description="检测场景名称；不存在时由后端创建自定义场景"
    )
    dataset_name: str = Field(
        ..., description="业务数据集名称，仅允许字母、数字、下划线和连字符"
    )
    filename: str = Field(..., description="客户端原始文件名，当前必须是 .zip")
    content_type: str | None = Field(
        default="application/zip", description="上传对象的 Content-Type"
    )
    expected_size: int | None = Field(
        default=None,
        ge=1,
        description="客户端声明的文件大小；当前只记录，后续恢复 HeadObject 校验时使用",
    )
    upload_mode: str = Field(
        default="multipart", description="上传模式：multipart 或 presigned_put"
    )
    part_size: int | None = Field(
        default=None,
        ge=5 * 1024 * 1024,
        description="multipart 分片大小，默认 32MiB；客户端按它计算 total_parts",
    )


class MultipartPartsSignRequest(BaseModel):
    """批量签发 multipart part 上传 URL。"""

    part_numbers: list[int] = Field(
        ..., description="需要签名的 partNumber 列表，范围 1-10000"
    )
    expires_seconds: int | None = Field(
        None,
        ge=60,
        description="本批 part URL 有效期；不能超过服务端配置",
    )


class MultipartUploadedPart(BaseModel):
    """客户端已经上传成功的单个 part。"""

    part_number: int = Field(..., ge=1, le=10000, description="OSS partNumber")
    etag: str = Field(..., description="上传该 part 后 OSS 返回的 ETag")


class MultipartCompleteRequest(BaseModel):
    """完成 multipart 上传的请求。"""

    parts: list[MultipartUploadedPart] = Field(
        ..., description="全部已上传 part 的 part_number + etag"
    )


class RemoteDatasetReadyRequest(BaseModel):
    """把处理后的数据集标记为 READY。

    第一阶段可以人工或测试程序调用该接口；后续应由预处理 PAI-DLC 任务、
    EventBridge 或后端对账 Worker 自动触发。
    """

    processed_prefix: str | None = Field(
        None, description="处理后 YOLO 数据集 OSS 前缀，默认由后端按 dataset_id 生成"
    )
    manifest_key: str | None = Field(
        None, description="manifest.json 对象 key；为空时使用 processed_prefix/manifest.json"
    )
    success_key: str | None = Field(
        None, description="_SUCCESS 对象 key；为空时使用 processed_prefix/_SUCCESS"
    )
    verify_objects: bool = Field(
        True, description="是否要求 manifest.json 和 _SUCCESS 已真实存在于 OSS"
    )


class RemoteTrainingStartRequest(BaseModel):
    """启动远程训练的请求。

    请求中不包含 device、本地路径、本地/远程选择。计算位置和 GPU 规格由后端策略决定。
    """

    dataset_id: str = Field(..., description="状态为 UPLOADED 的远程数据集 ID")
    model_name: Yolo11ModelName = Field(default="yolo11n", description="Ultralytics 基础模型")
    epochs: int = Field(default=100, ge=5, le=500, description="训练轮数")
    img_size: int = Field(default=640, description="训练图像尺寸")
    batch_size: int = Field(default=16, ge=1, le=128, description="batch size")
    optimizer: str = Field(default="SGD", description="优化器名称")
    lr0: float = Field(default=0.01, description="初始学习率")
    augment_config: dict[str, Any] | None = Field(
        None, description="数据增强配置；当前先记录，后续接入训练命令模板"
    )


class DlcCallbackRequest(BaseModel):
    """PAI-DLC 训练容器主动回调后端的请求。

    token 是每个远程任务生成的一次性随机值。后端只保存 hash，
    回调不能单独决定成功，仍需校验 OSS 产物。
    """

    dlc_job_id: str = Field(..., description="PAI-DLC Job ID")
    status: str = Field(..., description="DLC 上报状态，例如 Succeeded/Failed/Stopped")
    token: str = Field(..., description="每个任务独立的 callback token")


class RemoteTrainingMetricCallbackRequest(BaseModel):
    """PAI-DLC 训练容器按 epoch 上报的监控指标。"""

    task_uuid: str = Field(..., description="后端创建的训练任务 UUID")
    token: str = Field(..., description="每个任务独立的 callback token")
    epoch: int = Field(..., ge=1, description="当前 epoch，从 1 开始")
    total_epochs: int | None = Field(None, ge=1, description="总 epoch 数")
    box_loss: float | None = Field(None, description="train/box_loss")
    cls_loss: float | None = Field(None, description="train/cls_loss")
    dfl_loss: float | None = Field(None, description="train/dfl_loss")
    precision: float | None = Field(None, description="metrics/precision(B)")
    recall: float | None = Field(None, description="metrics/recall(B)")
    map50: float | None = Field(None, description="metrics/mAP50(B)")
    map50_95: float | None = Field(None, description="metrics/mAP50-95(B)")
    lr: float | None = Field(None, description="学习率")


class OssMultipartUploadEventRequest(BaseModel):
    """OSS 分片上传完成事件。

    FC/EventBridge 负责把阿里云原始事件转换为这个简化结构。
    后端不写死 object key 前缀，只用 bucket + object_key 精确匹配数据库上传记录。
    """

    event_id: str | None = Field(None, description="OSS/EventBridge 事件 ID，用于日志和幂等")
    event_type: str = Field(
        ..., description="必须是 oss:ObjectCreated:CompleteMultipartUpload"
    )
    bucket: str = Field(..., description="OSS bucket 或接入点 alias")
    object_key: str = Field(..., description="上传对象 key，应等于数据库 raw_object_key")
    size: int | None = Field(None, ge=0, description="OSS 事件中的对象大小")
    etag: str | None = Field(None, description="OSS 事件中的对象 ETag")
    event_time: str | None = Field(None, description="OSS/EventBridge 事件时间")


def _handle_error(exc: Exception, action: str) -> HTTPException:
    """把远程训练内部异常转换为 HTTP 错误。

    业务校验错误返回 400；配置错误说明服务端缺少环境变量，返回 500。
    未知异常必须只写日志，不能把数据库 SQL、云 SDK 响应、AccessKey 片段等细节返回给客户端。
    """
    if isinstance(exc, RemoteTrainingValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RemoteTrainConfigError):
        logger.error("远程训练配置错误 | action=%s | error=%s", action, exc)
        return HTTPException(status_code=500, detail="远程训练服务配置错误，请联系管理员")
    logger.error(
        "远程训练接口异常 | action=%s | error=%s",
        action,
        exc,
        exc_info=True,
    )
    return HTTPException(status_code=500, detail="服务器内部错误")


def _require_internal_callback_token(authorization: str | None) -> None:
    """校验 FC/EventBridge 调用后端内部接口的机器密钥。"""
    expected = remote_training_service.settings.remote_callback_secret
    if not expected:
        raise HTTPException(status_code=500, detail="远程训练回调密钥未配置")
    value = authorization or ""
    if value.startswith("Bearer "):
        value = value.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=401, detail="回调认证失败")


def _can_manage_all_datasets(user) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "user_type", "") == "admin")


@router.get("/datasets", summary="查询 OSS 数据集列表")
async def list_remote_datasets(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return {
            "datasets": remote_training_service.list_dataset_uploads(
                db=db,
                user_id=current_user.id,
                include_all=_can_manage_all_datasets(current_user),
            )
        }
    except Exception as exc:
        raise _handle_error(exc, "list_remote_datasets")


@router.delete("/datasets/{dataset_ref}", summary="删除 OSS 数据集")
async def delete_remote_dataset(
    dataset_ref: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.delete_dataset_upload(
            db=db,
            user_id=current_user.id,
            dataset_ref=dataset_ref,
            include_all=_can_manage_all_datasets(current_user),
        )
    except Exception as exc:
        raise _handle_error(exc, "delete_remote_dataset")


@router.post("/uploads", summary="创建远程数据集上传会话")
async def create_dataset_upload(
    request: RemoteDatasetUploadCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.initiate_dataset_upload(
            db=db,
            user_id=current_user.id,
            scene_id=request.scene_id,
            scene_name=request.scene_name,
            dataset_name=request.dataset_name,
            filename=request.filename,
            content_type=request.content_type,
            expected_size=request.expected_size,
            upload_mode=request.upload_mode,
            part_size=request.part_size,
        )
    except Exception as exc:
        raise _handle_error(exc, "create_dataset_upload")


@router.get("/uploads/{upload_id}", summary="查询远程数据集上传状态")
async def get_dataset_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.get_dataset_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "get_dataset_upload")


@router.post("/uploads/{upload_id}/multipart/parts/sign", summary="签发 OSS 分片上传 URL")
async def sign_multipart_part_urls(
    upload_id: str,
    request: MultipartPartsSignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.sign_multipart_part_urls(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
            part_numbers=request.part_numbers,
            expires_seconds=request.expires_seconds,
        )
    except Exception as exc:
        raise _handle_error(exc, "sign_multipart_part_urls")


@router.post("/uploads/{upload_id}/multipart/complete", summary="合并 OSS 分片上传")
async def complete_multipart_upload(
    upload_id: str,
    request: MultipartCompleteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.complete_multipart_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
            parts=[part.model_dump() for part in request.parts],
        )
    except Exception as exc:
        raise _handle_error(exc, "complete_multipart_upload")


@router.post("/uploads/{upload_id}/multipart/abort", summary="取消 OSS 分片上传")
async def abort_multipart_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.abort_multipart_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "abort_multipart_upload")


@router.post("/uploads/{upload_id}/complete", summary="记录客户端上传完成")
async def complete_dataset_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.complete_dataset_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "complete_dataset_upload")


@router.post("/callbacks/oss/multipart-complete", summary="OSS 分片上传完成回调")
async def handle_oss_multipart_complete_callback(
    request: OssMultipartUploadEventRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    try:
        _require_internal_callback_token(authorization)
        return remote_training_service.handle_oss_multipart_upload_event(
            db=db,
            event_type=request.event_type,
            bucket=request.bucket,
            object_key=request.object_key,
            size=request.size,
            etag=request.etag,
            event_id=request.event_id,
            event_time=request.event_time,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _handle_error(exc, "handle_oss_multipart_complete_callback")


@router.post("/uploads/{upload_id}/ready", summary="标记处理后数据集可训练")
async def mark_dataset_ready(
    upload_id: str,
    request: RemoteDatasetReadyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.mark_dataset_ready(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
            processed_prefix=request.processed_prefix,
            manifest_key=request.manifest_key,
            success_key=request.success_key,
            verify_objects=request.verify_objects,
        )
    except Exception as exc:
        raise _handle_error(exc, "mark_dataset_ready")


@router.post("/start", summary="启动远程训练")
async def start_remote_training(
    request: RemoteTrainingStartRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.start_remote_training(
            db=db,
            user_id=current_user.id,
            dataset_id=request.dataset_id,
            config=request.model_dump(),
        )
    except Exception as exc:
        raise _handle_error(exc, "start_remote_training")


@router.post("/jobs/{task_id}/sync", summary="同步远程训练状态与产物")
async def sync_remote_training_job(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.sync_training_job(
            db=db, task_id=task_id, user_id=current_user.id
        )
    except Exception as exc:
        raise _handle_error(exc, "sync_remote_training_job")


@router.get("/status/{task_id}", summary="查询远程训练状态")
async def get_remote_training_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.sync_training_job(
            db=db, task_id=task_id, user_id=current_user.id
        )
    except Exception as exc:
        raise _handle_error(exc, "get_remote_training_status")


@router.post("/stop/{task_id}", summary="停止远程训练")
async def stop_remote_training(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return remote_training_service.stop_training(
            db=db, task_id=task_id, user_id=current_user.id
        )
    except Exception as exc:
        raise _handle_error(exc, "stop_remote_training")


@router.post("/callbacks/dlc", summary="PAI-DLC 任务回调")
async def handle_dlc_callback(
    request: DlcCallbackRequest,
    db: Session = Depends(get_db),
):
    try:
        return remote_training_service.handle_dlc_callback(
            db=db,
            dlc_job_id=request.dlc_job_id,
            status=request.status,
            token=request.token,
        )
    except Exception as exc:
        raise _handle_error(exc, "handle_dlc_callback")


@router.post("/callbacks/metrics", summary="记录 PAI-DLC 训练指标")
async def handle_remote_training_metric_callback(
    request: RemoteTrainingMetricCallbackRequest,
    db: Session = Depends(get_db),
):
    try:
        return remote_training_service.handle_metric_callback(
            db=db,
            task_uuid=request.task_uuid,
            token=request.token,
            epoch=request.epoch,
            total_epochs=request.total_epochs,
            metrics={
                "box_loss": request.box_loss,
                "cls_loss": request.cls_loss,
                "dfl_loss": request.dfl_loss,
                "precision": request.precision,
                "recall": request.recall,
                "map50": request.map50,
                "map50_95": request.map50_95,
                "lr": request.lr,
            },
        )
    except Exception as exc:
        raise _handle_error(exc, "handle_remote_training_metric_callback")


@router.get("/artifacts/{task_id}", summary="查询训练产物存储位置")
async def list_remote_training_artifacts(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return {
            "task_id": task_id,
            "items": remote_training_service.list_artifact_locations(
                db, task_id, user_id=current_user.id
            ),
        }
    except Exception as exc:
        raise _handle_error(exc, "list_remote_training_artifacts")
