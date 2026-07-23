"""远程训练配置。

为减少对旧 settings.py 的侵入，本模块直接读取环境变量。后续配置稳定后，
可以再把这些字段收敛进统一 Settings。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values


class RemoteTrainConfigError(RuntimeError):
    """远程训练配置错误。"""


OSS_REQUIRED_CONFIG = [
    ("oss_access_key_id", "OSS_ACCESS_KEY_ID 或 ALIBABA_CLOUD_ACCESS_KEY_ID"),
    (
        "oss_access_key_secret",
        "OSS_ACCESS_KEY_SECRET 或 ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    ),
    ("oss_endpoint", "OSS_ENDPOINT"),
    ("oss_region", "OSS_REGION 或 PAI_REGION_ID"),
    ("oss_bucket", "OSS_BUCKET"),
]

PAI_REQUIRED_CONFIG = [
    ("pai_access_key_id", "PAI_ACCESS_KEY_ID 或 ALIBABA_CLOUD_ACCESS_KEY_ID"),
    (
        "pai_access_key_secret",
        "PAI_ACCESS_KEY_SECRET 或 ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    ),
    ("pai_region_id", "PAI_REGION_ID"),
    ("pai_workspace_id", "PAI_WORKSPACE_ID"),
    ("pai_image_uri", "PAI_IMAGE_URI"),
]

CALLBACK_REQUIRED_CONFIG = [
    ("remote_callback_secret", "REMOTE_TRAINING_CALLBACK_SECRET"),
    (
        "remote_metrics_callback_url",
        "REMOTE_TRAINING_METRICS_CALLBACK_URL 或 REMOTE_TRAINING_CALLBACK_URL/CALLBACK_URL",
    ),
]


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    """读取项目 .env 中的远程训练配置。

    旧 Settings 会读取 .env，但不会把未声明字段写回 os.environ。
    远程训练第一阶段直接读取环境变量，因此这里显式补读 .env，
    保持“系统环境变量 > .env > 默认值”的优先级。
    """
    backend_dir = Path(__file__).resolve().parents[2]
    candidates = [Path.cwd() / ".env", backend_dir / ".env"]
    values: dict[str, str] = {}
    for path in candidates:
        if not path.exists():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                values[key] = value
    return values


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        value = _dotenv_values().get(name, default)
    return str(value).strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


def _derive_metrics_callback_url(value: str) -> str:
    """从旧 CALLBACK_URL 兼容推导 metrics 回调地址。"""
    url = value.strip()
    if not url:
        return ""
    if url.endswith("/callbacks/metrics"):
        return url
    if url.endswith("/callbacks/dlc"):
        return url[: -len("/callbacks/dlc")] + "/callbacks/metrics"
    return url.rstrip("/") + "/callbacks/metrics"


def _derive_error_callback_url(value: str) -> str:
    """从 metrics/dlc 回调地址兼容推导 error 回调地址。"""
    url = value.strip()
    if not url:
        return ""
    if url.endswith("/callbacks/error"):
        return url
    if url.endswith("/callbacks/metrics"):
        return url[: -len("/callbacks/metrics")] + "/callbacks/error"
    if url.endswith("/callbacks/dlc"):
        return url[: -len("/callbacks/dlc")] + "/callbacks/error"
    return url.rstrip("/") + "/callbacks/error"


@dataclass(frozen=True)
class RemoteTrainSettings:
    """远程训练运行配置。

    字段分为三组：
    - OSS：负责数据集压缩包、处理后数据集、训练输出与模型产物。
    - PAI-DLC：负责提交、轮询和停止远程训练任务。
    - ACR：负责 PAI-DLC 拉取私有或受限 VPC 镜像时的登录信息。

    这里不把配置写进旧 Settings，是为了让远程训练模块在第一阶段保持旁路接入。
    """

    # OSS 后端凭证。只在服务端使用，用于签发预签名 URL、HeadObject 校验和产物读取。
    oss_access_key_id: str
    oss_access_key_secret: str
    oss_security_token: str
    # OSS 访问入口、地域和 bucket。bucket 是远程训练数据与产物的权威存储位置。
    oss_endpoint: str
    oss_region: str
    oss_bucket: str
    # PAI-DLC DataSources 使用的 OSS endpoint。默认沿用 OSS_ENDPOINT，可按需改为 internal endpoint。
    pai_oss_endpoint: str
    # PAI-DLC DataSources 使用的完整 OSS URI host，可覆盖 bucket + endpoint 自动拼接。
    pai_oss_uri_host: str
    # 远程训练对象前缀，用于隔离本功能产生的 raw dataset、processed dataset 和 training output。
    oss_prefix: str
    # 浏览器直传 URL 有效期。URL 泄露后在过期前可上传到指定 object key，因此不应设置过长。
    upload_url_expires_seconds: int
    # OSS/EventBridge/FC 调用后端内部接口时使用的机器密钥，不下发给浏览器。
    remote_callback_secret: str
    # PAI-DLC 容器向后端上报 epoch 指标的公网可访问地址。
    remote_metrics_callback_url: str
    # PAI-DLC 容器向后端上报训练异常诊断的公网可访问地址。
    remote_error_callback_url: str

    # PAI-DLC 控制面凭证。用于 CreateJob/GetJob/StopJob，不会下发给浏览器。
    pai_access_key_id: str
    pai_access_key_secret: str
    pai_security_token: str
    # PAI-DLC 工作空间与地域。必须与可用 ECS 规格、镜像和 OSS 访问策略匹配。
    pai_region_id: str
    pai_endpoint: str
    pai_workspace_id: str
    pai_resource_id: str
    # 训练镜像完整地址。这里填 registry address，不填 PAI 自定义镜像 ID。
    pai_image_uri: str
    pai_job_type: str
    pai_ecs_spec: str
    pai_pod_count: int
    pai_job_max_running_minutes: int
    # PAI-DLC DataSources 挂载目录。训练命令只访问挂载路径，不直接处理 OSS SDK 细节。
    pai_dataset_mount_path: str
    pai_output_mount_path: str
    # ACR 镜像认证配置。公开镜像可留空；VPC endpoint 认证失败时需要填写。
    pai_acr_registry: str
    pai_acr_username: str
    pai_acr_password: str

    @classmethod
    def from_env(cls) -> "RemoteTrainSettings":
        image_uri = _env("PAI_IMAGE_URI")
        registry_from_image = image_uri.split("/", 1)[0] if "/" in image_uri else ""
        base_callback_url = _env("REMOTE_TRAINING_CALLBACK_URL", _env("CALLBACK_URL"))
        metrics_callback_url = _env(
            "REMOTE_TRAINING_METRICS_CALLBACK_URL",
            _derive_metrics_callback_url(base_callback_url),
        )
        return cls(
            oss_access_key_id=_env(
                "OSS_ACCESS_KEY_ID", _env("ALIBABA_CLOUD_ACCESS_KEY_ID")
            ),
            oss_access_key_secret=_env(
                "OSS_ACCESS_KEY_SECRET", _env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
            ),
            oss_security_token=_env(
                "OSS_SECURITY_TOKEN", _env("ALIBABA_CLOUD_SECURITY_TOKEN")
            ),
            oss_endpoint=_env("OSS_ENDPOINT"),
            oss_region=_env("OSS_REGION", _env("PAI_REGION_ID")),
            oss_bucket=_env("OSS_BUCKET"),
            pai_oss_endpoint=_env(
                "PAI_DLC_OSS_ENDPOINT",
                _env("PAI_OSS_ENDPOINT", _env("OSS_ENDPOINT")),
            ),
            pai_oss_uri_host=_env("PAI_DLC_OSS_URI_HOST"),
            oss_prefix=_env("REMOTE_TRAIN_OSS_PREFIX", "remote-training"),
            upload_url_expires_seconds=_env_int("OSS_UPLOAD_URL_EXPIRES_SECONDS", 900),
            remote_callback_secret=_env("REMOTE_TRAINING_CALLBACK_SECRET"),
            remote_metrics_callback_url=metrics_callback_url,
            remote_error_callback_url=_env(
                "REMOTE_TRAINING_ERROR_CALLBACK_URL",
                _derive_error_callback_url(metrics_callback_url or base_callback_url),
            ),
            pai_access_key_id=_env(
                "PAI_ACCESS_KEY_ID", _env("ALIBABA_CLOUD_ACCESS_KEY_ID")
            ),
            pai_access_key_secret=_env(
                "PAI_ACCESS_KEY_SECRET", _env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
            ),
            pai_security_token=_env(
                "PAI_SECURITY_TOKEN", _env("ALIBABA_CLOUD_SECURITY_TOKEN")
            ),
            pai_region_id=_env("PAI_REGION_ID"),
            pai_endpoint=_env("PAI_DLC_ENDPOINT"),
            pai_workspace_id=_env("PAI_WORKSPACE_ID"),
            pai_resource_id=_env("PAI_RESOURCE_ID"),
            pai_image_uri=image_uri,
            pai_job_type=_env("PAI_JOB_TYPE", "PyTorchJob"),
            pai_ecs_spec=_env("PAI_ECS_SPEC"),
            pai_pod_count=_env_int("PAI_POD_COUNT", 1),
            pai_job_max_running_minutes=_env_int("PAI_JOB_MAX_RUNNING_MINUTES", 180),
            pai_dataset_mount_path=_env("PAI_DATASET_MOUNT_PATH", "/mnt/dataset"),
            pai_output_mount_path=_env("PAI_OUTPUT_MOUNT_PATH", "/mnt/output"),
            pai_acr_registry=_env("ACR_DOCKER_REGISTRY", registry_from_image),
            pai_acr_username=_env("ACR_USERNAME"),
            pai_acr_password=_env("ACR_PASSWORD"),
        )

    def missing_oss_config(self) -> list[str]:
        """返回缺失的 OSS 环境变量名，不访问 OSS 网络。"""
        return [
            env_name
            for attr, env_name in OSS_REQUIRED_CONFIG
            if not getattr(self, attr)
        ]

    def missing_pai_config(self) -> list[str]:
        """返回缺失的 PAI-DLC 环境变量名，不访问 PAI-DLC 网络。"""
        missing = [
            env_name
            for attr, env_name in PAI_REQUIRED_CONFIG
            if not getattr(self, attr)
        ]
        if not self.pai_resource_id and not self.pai_ecs_spec:
            missing.append("PAI_RESOURCE_ID 或 PAI_ECS_SPEC")
        return missing

    def missing_callback_config(self) -> list[str]:
        """返回缺失的内部回调环境变量名，不访问外部网络。"""
        return [
            env_name
            for attr, env_name in CALLBACK_REQUIRED_CONFIG
            if not getattr(self, attr)
        ]

    def require_oss(self) -> None:
        # 对外报错使用真实环境变量名，避免用户看到内部 dataclass 字段名。
        missing = self.missing_oss_config()
        if missing:
            raise RemoteTrainConfigError("缺少 OSS 配置：" + ", ".join(missing))

    def require_pai(self) -> None:
        # 对外报错使用真实环境变量名，便于直接复制到 .env 中补齐。
        missing = self.missing_pai_config()
        if missing:
            raise RemoteTrainConfigError("缺少 PAI-DLC 配置：" + ", ".join(missing))

    def require_callback(self) -> None:
        missing = self.missing_callback_config()
        if missing:
            raise RemoteTrainConfigError("缺少远程训练回调配置：" + ", ".join(missing))


def load_remote_train_settings() -> RemoteTrainSettings:
    return RemoteTrainSettings.from_env()


def check_remote_train_environment() -> dict[str, list[str] | bool]:
    """启动时使用的本地环境变量检查。

    该函数只检查变量是否填写，不创建 OSS/PAI 客户端，也不访问云服务。
    返回结果用于日志告警；真正接口调用时仍由 require_oss/require_pai 做强校验。
    """
    settings = load_remote_train_settings()
    oss_missing = settings.missing_oss_config()
    pai_missing = settings.missing_pai_config()
    callback_missing = settings.missing_callback_config()
    return {
        "ready": not oss_missing and not pai_missing and not callback_missing,
        "oss_missing": oss_missing,
        "pai_missing": pai_missing,
        "callback_missing": callback_missing,
    }
