"""远程训练通用常量与纯函数。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urlparse

from app.train.remote_train_errors import RemoteTrainingValidationError


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

REMOTE_ERROR_TEXT_LIMIT = 20000
REMOTE_ERROR_LIST_LIMIT = 80
REMOTE_ERROR_DEPTH_LIMIT = 6


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
