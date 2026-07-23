"""远程训练 API 请求模型。"""

from __future__ import annotations

from typing import Any

from app.entity.schemas import Yolo11ModelName
from pydantic import BaseModel, ConfigDict, Field


class RemoteDatasetUploadCreate(BaseModel):
    """创建远程数据集上传会话的请求。

    该接口只创建数据库记录和 OSS 上传凭据，不接收文件内容。
    数据集统一使用 OSS multipart 上传。
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
        description="客户端声明的文件大小；完成分片合并后用于 HeadObject 校验",
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
class RemoteTrainingErrorCallbackRequest(BaseModel):
    """PAI-DLC 训练容器上报的异常诊断信息。"""

    model_config = ConfigDict(extra="allow")

    task_uuid: str = Field(..., description="后端创建的训练任务 UUID")
    token: str = Field(..., description="每个任务独立的 callback token")
    stage: str | None = Field(None, description="失败阶段")
    error_type: str | None = Field(None, description="异常类型")
    error: str | None = Field(None, description="异常摘要")
    traceback: str | None = Field(None, description="异常堆栈")
