"""模型管理 API 路由。"""

from __future__ import annotations

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import get_db
from app.model_management.errors import ModelManagementError
from app.model_management.schemas import (
    ModelSourceType,
    ModelUploadCreate,
    MultipartCompleteRequest,
    MultipartPartsSignRequest,
    SetDefaultModelRequest,
)
from app.model_management.service import model_management_service
from app.oss_multipart.errors import OssMultipartError
from app.train.remote_train_config import RemoteTrainConfigError
from app.train.remote_train_errors import RemoteTrainingValidationError
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/model-management", tags=["模型管理"])
logger = get_logger(__name__)


def _handle_error(exc: Exception, action: str) -> HTTPException:
    if isinstance(exc, ModelManagementError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    if isinstance(exc, RemoteTrainingValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, OssMultipartError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RemoteTrainConfigError):
        logger.error("模型管理配置错误 | action=%s | error=%s", action, exc)
        return HTTPException(status_code=500, detail="模型管理服务配置错误，请联系管理员")
    logger.error("模型管理接口异常 | action=%s | error=%s", action, exc, exc_info=True)
    return HTTPException(status_code=500, detail="服务器内部错误")


def _require_admin(current_user) -> None:
    if getattr(current_user, "is_superuser", False):
        return
    if getattr(current_user, "user_type", "") == "admin":
        return
    raise HTTPException(status_code=403, detail="仅管理员可操作模型管理")


@router.get("/models", summary="查询所有可管理模型")
async def list_models(
    model_name: str | None = Query(None, description="按模型名称模糊筛选"),
    version: str | None = Query(None, description="按版本模糊筛选"),
    scene_id: int | None = Query(None, description="按场景 ID 筛选"),
    model_type: str | None = Query(None, description="按 YOLO 模型类型筛选"),
    source_type: ModelSourceType = Query("all", description="all/trained/uploaded"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        items = model_management_service.list_models(
            db=db,
            model_name=model_name,
            version=version,
            scene_id=scene_id,
            model_type=model_type,
            source_type=source_type,
        )
        return {"total": len(items), "items": items, "models": items}
    except Exception as exc:
        raise _handle_error(exc, "list_models")


@router.get("/default-model", summary="查询当前默认推理模型")
async def get_default_model(
    scene_id: int | None = Query(None, description="场景 ID；为空时使用 chest_xray"),
    scene_name: str | None = Query(None, description="场景名称"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return model_management_service.get_default_model(
            db=db,
            scene_id=scene_id,
            scene_name=scene_name,
        )
    except Exception as exc:
        raise _handle_error(exc, "get_default_model")


@router.post("/uploads", summary="创建模型上传会话")
async def create_model_upload(
    request: ModelUploadCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.initiate_upload(
            db=db,
            user_id=current_user.id,
            scene_id=request.scene_id,
            scene_name=request.scene_name,
            model_name=request.model_name,
            version=request.version,
            model_type=request.model_type,
            filename=request.filename,
            content_type=request.content_type,
            expected_size=request.expected_size,
            part_size=request.part_size,
            description=request.description,
        )
    except Exception as exc:
        raise _handle_error(exc, "create_model_upload")


@router.get("/uploads/{upload_id}", summary="查询模型上传会话")
async def get_model_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.get_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "get_model_upload")


@router.post("/uploads/{upload_id}/multipart/parts/sign", summary="签发模型分片上传 URL")
async def sign_model_multipart_part_urls(
    upload_id: str,
    request: MultipartPartsSignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.sign_multipart_part_urls(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
            part_numbers=request.part_numbers,
            expires_seconds=request.expires_seconds,
        )
    except Exception as exc:
        raise _handle_error(exc, "sign_model_multipart_part_urls")


@router.post("/uploads/{upload_id}/multipart/complete", summary="合并并登记模型上传")
async def complete_model_multipart_upload(
    upload_id: str,
    request: MultipartCompleteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.complete_multipart_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
            parts=[part.model_dump() for part in request.parts],
        )
    except Exception as exc:
        raise _handle_error(exc, "complete_model_multipart_upload")


@router.post("/uploads/{upload_id}/multipart/abort", summary="取消模型分片上传")
async def abort_model_multipart_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.abort_multipart_upload(
            db=db,
            user_id=current_user.id,
            upload_uuid=upload_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "abort_model_multipart_upload")


@router.get("/models/{model_version_id}/download-url", summary="获取模型权重下载 URL")
async def get_model_download_url(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return model_management_service.get_download_url(
            db=db,
            model_version_id=model_version_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "get_model_download_url")


@router.post("/models/{model_version_id}/set-default", summary="设置默认推理模型")
async def set_default_model(
    model_version_id: int,
    request: SetDefaultModelRequest | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.set_default_model(
            db=db,
            model_version_id=model_version_id,
            scene_id=request.scene_id if request else None,
        )
    except Exception as exc:
        raise _handle_error(exc, "set_default_model")


@router.delete("/models/{model_version_id}", summary="删除模型")
async def delete_model(
    model_version_id: int,
    cascade: bool = Query(False, description="训练来源模型必须显式确认联动删除"),
    replacement_model_version_id: int | None = Query(
        None,
        description="删除默认模型时需要先切换到该模型",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        return model_management_service.delete_model(
            db=db,
            model_version_id=model_version_id,
            cascade=cascade,
            replacement_model_version_id=replacement_model_version_id,
        )
    except Exception as exc:
        raise _handle_error(exc, "delete_model")
