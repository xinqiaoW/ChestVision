"""模型管理 API 请求模型。"""

from __future__ import annotations

from typing import Literal

from app.entity.schemas import Yolo11ModelName
from pydantic import BaseModel, ConfigDict, Field


ModelSourceType = Literal["all", "trained", "uploaded"]


class ModelUploadCreate(BaseModel):
    """创建模型权重上传会话。"""

    model_config = ConfigDict(protected_namespaces=())

    scene_id: int | None = Field(None, description="检测场景 ID；为空时使用 chest_xray")
    scene_name: str | None = Field(
        None, description="检测场景名称；scene_id 为空时可用名称定位"
    )
    model_name: str = Field(..., min_length=1, max_length=100, description="模型名称")
    version: str = Field(..., min_length=1, max_length=50, description="模型版本")
    model_type: Yolo11ModelName = Field(..., description="YOLO11 模型尺寸")
    filename: str = Field(..., description="客户端原始文件名，必须是 .pt")
    content_type: str | None = Field(
        default="application/octet-stream", description="上传对象 Content-Type"
    )
    expected_size: int | None = Field(None, ge=1, description="客户端声明文件大小")
    part_size: int | None = Field(
        None, ge=5 * 1024 * 1024, description="multipart 分片大小"
    )
    description: str | None = Field(None, max_length=1000, description="模型说明")


class MultipartPartsSignRequest(BaseModel):
    """批量签发模型 multipart part 上传 URL。"""

    part_numbers: list[int] = Field(..., description="需要签名的 partNumber 列表")
    expires_seconds: int | None = Field(None, ge=60, description="本批 URL 有效期")


class MultipartUploadedPart(BaseModel):
    """客户端已上传成功的单个 part。"""

    part_number: int = Field(..., ge=1, le=10000, description="OSS partNumber")
    etag: str = Field(..., description="上传该 part 后 OSS 返回的 ETag")


class MultipartCompleteRequest(BaseModel):
    """完成模型 multipart 上传。"""

    parts: list[MultipartUploadedPart] = Field(..., description="全部已上传分片")


class SetDefaultModelRequest(BaseModel):
    """设置默认推理模型。"""

    scene_id: int | None = Field(None, description="目标场景；为空时使用模型所属场景")
