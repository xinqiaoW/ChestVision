"""远程训练 API 路由。"""

from __future__ import annotations

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import get_db
from app.oss_multipart.errors import OssMultipartError
from app.train.remote_train_config import RemoteTrainConfigError
from app.train.remote_train_errors import RemoteTrainingValidationError
from app.train.remote_train_schemas import (
    DlcCallbackRequest,
    MultipartCompleteRequest,
    MultipartPartsSignRequest,
    RemoteDatasetUploadCreate,
    RemoteTrainingErrorCallbackRequest,
    RemoteTrainingMetricCallbackRequest,
    RemoteTrainingStartRequest,
)
from app.train.remote_train_service import remote_training_service
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/training/remote", tags=["远程训练"])
logger = get_logger(__name__)


def _handle_error(exc: Exception, action: str) -> HTTPException:
    """把远程训练内部异常转换为 HTTP 错误。

    业务校验错误返回 400；配置错误说明服务端缺少环境变量，返回 500。
    未知异常必须只写日志，不能把数据库 SQL、云 SDK 响应、AccessKey 片段等细节返回给客户端。
    """
    if isinstance(exc, RemoteTrainingValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, OssMultipartError):
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


@router.post("/callbacks/error", summary="记录 PAI-DLC 训练异常")
async def handle_remote_training_error_callback(
    request: RemoteTrainingErrorCallbackRequest,
    db: Session = Depends(get_db),
):
    try:
        payload = request.model_dump(exclude={"token"}, exclude_none=True)
        return remote_training_service.handle_error_callback(
            db=db,
            task_uuid=request.task_uuid,
            token=request.token,
            error_detail=payload,
        )
    except Exception as exc:
        raise _handle_error(exc, "handle_remote_training_error_callback")


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
