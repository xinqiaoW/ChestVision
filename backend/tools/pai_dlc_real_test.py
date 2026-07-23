#!/usr/bin/env python3
"""
真实阿里云 PAI-DLC SDK 测试程序。

先在 backend/.env 填写 PAI_*、ALIBABA_CLOUD_* 和可选 ACR_* 环境变量，再运行：

  python backend/tools/pai_dlc_real_test.py --case config-check
  # 含义：只检查本文件中的全局变量和环境变量是否填写完整，不访问 PAI-DLC。

  python backend/tools/pai_dlc_real_test.py --case list-only
  # 含义：调用真实 PAI-DLC 查询资源规格和最近任务列表，不创建任务，适合先验证凭证和 workspace。

  python backend/tools/pai_dlc_real_test.py --case list-specs
  # 含义：只查询当前地域可用的 ECS 规格。这个用例通常不代表 workspace 权限已经正确。

  python backend/tools/pai_dlc_real_test.py --case list-jobs
  # 含义：只查询 PAI_WORKSPACE_ID 下的最近任务，用于验证当前 AK 是否已经加入该 workspace，
  #       以及是否具备 PaiDLC:ListJobs 权限。

  python backend/tools/pai_dlc_real_test.py --case create-stop
  # 含义：创建一个真实 PAI-DLC 测试任务，轮询到可停止状态后调用 StopJob，可能产生费用。

依赖：
  pip install alibabacloud_credentials alibabacloud_tea_openapi \
    alibabacloud_pai_dlc20201203==1.4.17

注意：
  这个脚本会访问真实 PAI-DLC。
  create-* 用例会创建真实任务，可能产生费用。
  stop-* 用例会停止真实任务，请只对你确认可以停止的任务使用。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any


def load_backend_env() -> None:
    """Load backend/.env so the test script can use local runtime settings."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


load_backend_env()

# ══════════════════════════════════════════════════════════════
# 运行真实 PAI-DLC 测试前，请填写这些全局变量
# ══════════════════════════════════════════════════════════════

# 安全开关。
# 获取方式：不需要从控制台获取，这是脚本自己的保护开关。
# 使用方式：所有变量都填好，并且确认要访问真实 PAI-DLC 后，才改为 True。
# 保持 False 时，除了 config-check 外的真实 PAI-DLC 操作都会被拦截。
CONFIRM_REAL_CLOUD_CALLS = env_bool("CONFIRM_REAL_CLOUD_CALLS", False)

# 阿里云 AccessKey ID。
# 获取方式：
#   1. 阿里云 RAM 控制台 -> 用户 -> 选择或创建 RAM 用户 -> 认证管理 -> 创建 AccessKey。
#   2. 给该 RAM 用户授予 PAI-DLC 权限，例如 ListJobs、GetJob、CreateJob、
#      StopJob、ListEcsSpecs，以及后续可能需要的 AIWorkspace 相关权限。
#   3. 也可以不写在文件里，改用环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID。
# 注意：
#   不要使用主账号 AccessKey，不要把真实密钥提交到代码仓库。
#   阿里云 Credentials SDK 默认读取环境变量；本脚本会把这里填写的值复制到环境变量。
ALIYUN_ACCESS_KEY_ID = os.getenv("PAI_ACCESS_KEY_ID") or os.getenv(
    "ALIBABA_CLOUD_ACCESS_KEY_ID", ""
)

# 与 ALIYUN_ACCESS_KEY_ID 配套的 AccessKey Secret。
# 获取方式：
#   创建 AccessKey 时只显示一次；如果忘记，只能重新创建新的 AccessKey，
#   并禁用或删除旧 AccessKey。
# 环境变量兜底：ALIBABA_CLOUD_ACCESS_KEY_SECRET。
ALIYUN_ACCESS_KEY_SECRET = os.getenv("PAI_ACCESS_KEY_SECRET") or os.getenv(
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""
)

# STS SecurityToken，可选。
# 获取方式：
#   对有 PAI-DLC 权限的 RAM Role 调用 STS AssumeRole，将返回的 SecurityToken 填到这里。
#   同时把返回的临时 AccessKeyId/AccessKeySecret 填到上面两个变量。
# 使用长期 RAM 用户 AK 时留空。
# 环境变量兜底：ALIBABA_CLOUD_SECURITY_TOKEN。
ALIYUN_SECURITY_TOKEN = os.getenv("PAI_SECURITY_TOKEN") or os.getenv(
    "ALIBABA_CLOUD_SECURITY_TOKEN", ""
)

# PAI 工作空间和 DLC 资源所在地域 ID。
# 获取方式：
#   PAI 控制台左上角地域选择器，或工作空间详情页。
# 示例：
#   cn-hangzhou、cn-shanghai、cn-beijing、ap-southeast-1。
PAI_REGION_ID = os.getenv("PAI_REGION_ID", "cn-shanghai")

# PAI-DLC Endpoint。
# 获取方式：
#   通常是 pai-dlc.{region}.aliyuncs.com。
#   一般留空即可，脚本会根据 PAI_REGION_ID 自动拼出 endpoint。
#   只有使用特殊或私有 endpoint 时才手动填写。
PAI_DLC_ENDPOINT = os.getenv("PAI_DLC_ENDPOINT", "")

# PAI 工作空间 ID，查询任务和创建任务都需要。
# 获取方式：
#   PAI 控制台 -> 工作空间列表 -> 点击目标工作空间 -> 复制 Workspace ID。
#   有些页面也会在工作空间信息卡片里显示该 ID。
PAI_WORKSPACE_ID = os.getenv("PAI_WORKSPACE_ID", "")

# 资源配额 ID，可选。
# 获取方式：
#   PAI 控制台 -> AI 计算资源 / 资源配额 -> 复制目标通用计算资源或灵骏资源的配额 ID。
# 使用公共后付费资源并填写 PAI_ECS_SPEC 时可以留空。
PAI_RESOURCE_ID = os.getenv("PAI_RESOURCE_ID", "")

# 创建任务时使用的镜像 URI。
# 获取方式：
#   方式 A：PAI 控制台 -> DLC 创建任务页 -> 镜像配置 -> 选择并复制公共镜像 URI。
#   方式 B：使用自己的 ACR 镜像，例如：
#           registry-vpc.cn-hangzhou.aliyuncs.com/<namespace>/<repo>:<tag>
#   方式 C：先运行 --case list-only，确认 region/workspace 可用后，再在控制台选择镜像。
# 要求：
#   PAI-DLC 必须能在 PAI_REGION_ID 所在地域拉取这个镜像。
#   私有 ACR 镜像可能需要在控制台配置授权或账号密码。
PAI_IMAGE_URI = os.getenv("PAI_IMAGE_URI", "")

# ACR 镜像仓库登录地址，可选。
# 获取方式：
#   ACR 控制台 -> 实例 -> 访问凭证/登录信息，或从镜像地址中截取域名部分。
# 示例：
#   crpi-xxxxxxxx.cn-shanghai.personal.cr.aliyuncs.com
# 使用说明：
#   只有私有仓库、或公开仓库的 VPC endpoint 仍要求认证时才需要填写。
#   留空时，脚本会尝试从 PAI_IMAGE_URI 自动截取 registry 域名。
# 环境变量兜底：ACR_DOCKER_REGISTRY。
ACR_DOCKER_REGISTRY = os.getenv("ACR_DOCKER_REGISTRY", "")

# ACR 登录用户名，可选。
# 获取方式：
#   ACR 控制台 -> 访问凭证 -> 固定密码/临时密码对应的登录用户名。
#   个人版通常是阿里云账号或 RAM 用户相关登录名，以控制台显示为准。
# 环境变量兜底：ACR_USERNAME。
ACR_USERNAME = os.getenv("ACR_USERNAME", "")

# ACR 登录密码，可选。
# 获取方式：
#   ACR 控制台 -> 访问凭证 -> 设置固定密码，或生成临时登录密码。
# 注意：
#   不要提交真实密码；生产环境应使用环境变量或密钥管理。
# 环境变量兜底：ACR_PASSWORD。
ACR_PASSWORD = os.getenv("ACR_PASSWORD", "")

# 公共资源创建任务时使用的 ECS 规格。
# 获取方式：
#   1. 填好 AK、region、workspace 后运行：
#        python backend/tools/pai_dlc_real_test.py --case list-only
#   2. 从输出的 instance_type 里选择一个填到这里。
#   3. 也可以从 DLC 创建任务控制台页面选择资源规格。
# 如果 PAI_RESOURCE_ID 指向专有资源配额，并且你的 SDK payload 使用 ResourceConfig，
# 这里可以留空。
PAI_ECS_SPEC = os.getenv("PAI_ECS_SPEC", "ecs.gn6v-c8g1.2xlarge")

# DLC 任务类型。
# 选择方式：
#   YOLO/PyTorch 训练测试使用 PyTorchJob。
#   其他可用值取决于当前地域 PAI-DLC 支持情况，例如 TFJob、MPIJob、RayJob、CustomJob 等。
PAI_JOB_TYPE = os.getenv("PAI_JOB_TYPE", "PyTorchJob")

# Worker Pod 数量。
# 选择方式：
#   SDK 控制面冒烟测试用 1。
#   多机任务需要额外设计分布式训练命令，并确认资源配额足够。
PAI_POD_COUNT = env_int("PAI_POD_COUNT", 1)

# 任务最大运行时间，单位分钟。
# 选择方式：
#   冒烟测试保持较低，避免任务失控产生费用。
#   真实训练时再按需要调大。
PAI_JOB_MAX_RUNNING_MINUTES = env_int("PAI_JOB_MAX_RUNNING_MINUTES", 30)

# DLC 容器内执行的命令。
# 选择方式：
#   SDK 控制面冒烟测试尽量便宜简单，例如：
#     echo ...; sleep 60
#   真实训练时替换成训练命令，例如：
#     python /workspace/train_yolo_remote.py --data /mnt/data/data.yaml
# 注意：
#   该命令必须和所选镜像、挂载数据、代码路径匹配。
#   create-stop 用例需要任务有时间进入可停止状态，所以这里默认 sleep 60。
PAI_USER_COMMAND = "echo 'hello from pai-dlc sdk test'; date; sleep 60; echo 'DONE'"

# 传给 DLC 任务的自定义环境变量，可选。
# 使用方式：
#   放入命令需要的小型字符串配置，例如 OSS 前缀、任务 ID、回调地址、调试标记。
#   不建议放长期密钥，因为它们可能在任务配置中可见。
PAI_EXTRA_ENVS = {
    "SDK_TEST": "true",
}

# 已有任务辅助配置。
# EXISTING_JOB_ID：
#   获取方式：
#     PAI 控制台 -> DLC 任务列表 -> 打开任务并复制 Job ID；
#     或者从之前 create-* 用例的输出里复制。
#   用途：
#     --case get-existing
EXISTING_JOB_ID = ""

# JOB_ID_TO_STOP：
#   获取方式：
#     与 EXISTING_JOB_ID 相同，但必须选择你确认要停止的任务。
#   用途：
#     --case stop-existing
#   注意：
#     StopJob 会影响真实运行中的任务。
JOB_ID_TO_STOP = ""

# GetJob 轮询间隔，单位秒。
# 选择方式：
#   冒烟测试 5-15 秒通常足够；太低可能触发 API 限流。
POLL_INTERVAL_SECONDS = 10

# 等待任务进入终态的总时长，单位秒。
# 选择方式：
#   冒烟测试可用 10-15 分钟。
#   真实训练应设置更长超时，或交给后端 Worker 持续轮询。
POLL_TIMEOUT_SECONDS = 900


class ConfigError(RuntimeError):
    pass


class CloudApiError(RuntimeError):
    pass


TERMINAL_STATUSES = {"Succeeded", "Failed", "Stopped"}
RUNNING_STATUSES = {"Running"}
ENV_PREPARING_STATUSES = {
    "EnvPreparing",
    "EnvironmentPreparing",
    "Environment Preparing",
}

# 任务长期停在环境准备阶段时，通常要去任务详情页看实例操作日志。
# 这里先在命令行提示一次，避免用户只看到轮询状态而不知道下一步查哪里。
ENV_PREPARING_WARN_SECONDS = 5 * 60


def first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def load_config() -> dict[str, Any]:
    image_uri = PAI_IMAGE_URI
    registry_from_image = image_uri.split("/", 1)[0] if "/" in image_uri else ""
    return {
        "access_key_id": first_non_empty(
            ALIYUN_ACCESS_KEY_ID, os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        ),
        "access_key_secret": first_non_empty(
            ALIYUN_ACCESS_KEY_SECRET, os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        ),
        "security_token": first_non_empty(
            ALIYUN_SECURITY_TOKEN, os.getenv("ALIBABA_CLOUD_SECURITY_TOKEN", "")
        ),
        "region_id": PAI_REGION_ID,
        "endpoint": PAI_DLC_ENDPOINT or f"pai-dlc.{PAI_REGION_ID}.aliyuncs.com",
        "workspace_id": PAI_WORKSPACE_ID,
        "resource_id": PAI_RESOURCE_ID,
        "image_uri": image_uri,
        "acr_docker_registry": first_non_empty(
            ACR_DOCKER_REGISTRY,
            os.getenv("ACR_DOCKER_REGISTRY", ""),
            registry_from_image,
        ),
        "acr_username": first_non_empty(ACR_USERNAME, os.getenv("ACR_USERNAME", "")),
        "acr_password": first_non_empty(ACR_PASSWORD, os.getenv("ACR_PASSWORD", "")),
        "ecs_spec": PAI_ECS_SPEC,
        "job_type": PAI_JOB_TYPE,
    }


def require_config(
    config: dict[str, Any],
    require_confirm: bool = True,
    require_create_fields: bool = False,
) -> None:
    missing = [
        key
        for key in [
            "access_key_id",
            "access_key_secret",
            "region_id",
            "endpoint",
            "workspace_id",
        ]
        if not config.get(key)
    ]
    if require_create_fields:
        for key in ["image_uri"]:
            if not config.get(key):
                missing.append(key)
        if not config.get("resource_id") and not config.get("ecs_spec"):
            missing.append("ecs_spec or resource_id")
    if missing:
        raise ConfigError(f"缺少配置项：{', '.join(missing)}")
    if require_confirm and not CONFIRM_REAL_CLOUD_CALLS:
        raise ConfigError("真实调用前请先将 CONFIRM_REAL_CLOUD_CALLS 设置为 True")


def setup_credential_env(config: dict[str, Any]) -> None:
    os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"] = config["access_key_id"]
    os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"] = config["access_key_secret"]
    if config.get("security_token"):
        os.environ["ALIBABA_CLOUD_SECURITY_TOKEN"] = config["security_token"]


def import_dlc_sdk():
    try:
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_pai_dlc20201203 import models as dlc_models
        from alibabacloud_pai_dlc20201203.client import Client as DlcClient
        from alibabacloud_tea_openapi.models import Config
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖，请先安装：pip install alibabacloud_credentials "
            "alibabacloud_tea_openapi alibabacloud_pai_dlc20201203==1.4.17"
        ) from exc
    return CredClient, DlcClient, dlc_models, Config


def create_client():
    config = load_config()
    require_config(config)
    setup_credential_env(config)
    CredClient, DlcClient, _, TeaConfig = import_dlc_sdk()
    cred = CredClient()
    return DlcClient(
        TeaConfig(
            credential=cred,
            region_id=config["region_id"],
            endpoint=config["endpoint"],
        )
    )


def model_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_map"):
        return value.to_map()
    if isinstance(value, list):
        return [model_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: model_to_dict(item) for key, item in value.items()}
    return value


def print_json(title: str, value: Any) -> None:
    print(title)
    print(json.dumps(model_to_dict(value), indent=2, ensure_ascii=False, default=str))


def raise_cloud_api_error(action: str, exc: Exception) -> None:
    message = str(exc)
    details = [f"{action} 调用失败：{message}"]
    if "No permission" in message and "workspace" in message.lower():
        details.extend(
            [
                "",
                "判断：当前 AK 可以访问 PAI-DLC 控制面，但没有目标工作空间的任务权限。",
                "请检查：",
                f"  1. PAI_WORKSPACE_ID 是否填对，当前值为 {PAI_WORKSPACE_ID!r}。",
                f"  2. PAI_REGION_ID 是否和该工作空间所在地域一致，当前值为 {PAI_REGION_ID!r}。",
                "  3. 这个 RAM 用户或 RAM 角色是否已经被加入该 PAI 工作空间。",
                "  4. RAM 侧是否允许 PaiDLC:ListJobs；创建/停止任务还需要 CreateJob、GetJob、StopJob 等权限。",
                "",
                "说明：list_ecs_specs 成功只能证明地域和 DLC 服务可访问，"
                "不能证明当前身份已经拥有某个 workspace 的任务权限。",
            ]
        )
    raise CloudApiError("\n".join(details)) from exc


def build_create_job_request():
    config = load_config()
    require_config(config, require_create_fields=True)
    _, _, dlc_models, _ = import_dlc_sdk()

    envs = {
        "TASK_UUID": "sdk-test-" + uuid.uuid4().hex[:8],
        "SDK_TEST": "true",
        **PAI_EXTRA_ENVS,
    }
    job_spec = {
        "Type": "Worker",
        "Image": config["image_uri"],
        "PodCount": PAI_POD_COUNT,
    }
    if config.get("acr_username") and config.get("acr_password"):
        job_spec["ImageConfig"] = {
            "DockerRegistry": config["acr_docker_registry"],
            "Username": config["acr_username"],
            "Password": config["acr_password"],
        }
    if config.get("ecs_spec"):
        job_spec["EcsSpec"] = config["ecs_spec"]

    payload = {
        "WorkspaceId": config["workspace_id"],
        "DisplayName": "chestx-sdk-test-" + uuid.uuid4().hex[:8],
        "JobType": config["job_type"],
        "JobSpecs": [job_spec],
        "UserCommand": PAI_USER_COMMAND,
        "Envs": envs,
        "JobMaxRunningTimeMinutes": PAI_JOB_MAX_RUNNING_MINUTES,
    }
    if config.get("resource_id"):
        payload["ResourceId"] = config["resource_id"]

    return dlc_models.CreateJobRequest().from_map(payload), payload


def get_job(client: Any, job_id: str):
    _, _, dlc_models, _ = import_dlc_sdk()
    return client.get_job(job_id, dlc_models.GetJobRequest()).body


def stop_job(client: Any, job_id: str) -> Any:
    _, _, dlc_models, _ = import_dlc_sdk()
    stop_request_cls = getattr(dlc_models, "StopJobRequest", None)
    errors = []
    for args in (
        (job_id, stop_request_cls()) if stop_request_cls else None,
        (job_id,),
    ):
        if args is None:
            continue
        try:
            return client.stop_job(*args)
        except TypeError as exc:
            errors.append(str(exc))
    raise RuntimeError("无法用已知 SDK 调用签名执行 stop_job：" + "; ".join(errors))


def list_job_events_if_supported(client: Any, job_id: str) -> None:
    if not hasattr(client, "get_job_events"):
        print("[信息] 当前 SDK 版本没有 get_job_events 方法")
        return
    _, _, dlc_models, _ = import_dlc_sdk()
    request_cls = getattr(dlc_models, "GetJobEventsRequest", None)
    if request_cls is None:
        print("[信息] 当前 SDK models 中没有 GetJobEventsRequest")
        return
    try:
        response = client.get_job_events(job_id, request_cls())
        print_json("[任务事件]", response.body)
    except Exception as exc:
        print("[警告] get_job_events 调用失败：", exc)


def print_job_diagnostics(client: Any, job_id: str) -> None:
    print("[诊断] 查询最后一次任务详情")
    try:
        job = get_job(client, job_id)
        print_json("[诊断] 任务详情", job)
    except Exception as exc:
        print("[警告] 查询任务详情失败：", exc)
    list_job_events_if_supported(client, job_id)


def wait_job(client: Any, job_id: str, stop_when_running: bool = False) -> str:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    started_at = time.time()
    last_status = ""
    env_preparing_warned = False
    while time.time() < deadline:
        job = get_job(client, job_id)
        status = getattr(job, "status", None)
        last_status = status or "UNKNOWN"
        print(f"[轮询] job={job_id} status={last_status}")
        if (
            not env_preparing_warned
            and last_status in ENV_PREPARING_STATUSES
            and time.time() - started_at >= ENV_PREPARING_WARN_SECONDS
        ):
            print(
                "[提示] 任务长时间处于环境准备阶段。常见原因包括镜像拉取慢或失败、"
                "资源等待、数据源/OSS/NAS/CPFS 挂载异常、服务角色缺少存储权限。"
            )
            env_preparing_warned = True
        if (
            stop_when_running
            and last_status not in TERMINAL_STATUSES
            and last_status not in {"Pending", "Submitted"}
        ):
            print("[停止] 任务已进入可停止状态，调用 StopJob")
            stop_job(client, job_id)
            stop_when_running = False
        if last_status in TERMINAL_STATUSES:
            print_json("[最终任务状态]", job)
            return last_status
        time.sleep(POLL_INTERVAL_SECONDS)
    print_job_diagnostics(client, job_id)
    raise TimeoutError(
        f"任务在 {POLL_TIMEOUT_SECONDS}s 内未进入终态，最后状态={last_status}"
    )


def case_config_check() -> None:
    config = load_config()
    require_config(config, require_confirm=False)
    printable = {
        k: (
            "***"
            if "access_key" in k or "secret" in k or "token" in k or "password" in k
            else v
        )
        for k, v in config.items()
    }
    printable["confirm_real_cloud_calls"] = CONFIRM_REAL_CLOUD_CALLS
    print_json("[通过] 配置检查", printable)


def case_list_specs() -> None:
    client = create_client()
    _, _, dlc_models, _ = import_dlc_sdk()

    print("[查询 ECS 规格]")
    try:
        specs = client.list_ecs_specs(dlc_models.ListEcsSpecsRequest(page_size=10))
    except Exception as exc:
        raise_cloud_api_error("查询 ECS 规格", exc)
    for item in getattr(specs.body, "ecs_specs", []) or []:
        print(
            "  ",
            getattr(item, "instance_type", None),
            "cpu=",
            getattr(item, "cpu", None),
            "gpu=",
            getattr(item, "gpu_type", None),
        )
    print("[通过] list-specs")


def case_list_jobs() -> None:
    client = create_client()
    _, _, dlc_models, _ = import_dlc_sdk()
    config = load_config()
    print("[查询任务列表]")
    try:
        jobs = client.list_jobs(
            dlc_models.ListJobsRequest(
                workspace_id=config["workspace_id"],
                page_number=1,
                page_size=10,
            )
        )
    except Exception as exc:
        raise_cloud_api_error("查询任务列表", exc)
    for job in getattr(jobs.body, "jobs", []) or []:
        print(
            "  ",
            getattr(job, "display_name", None),
            getattr(job, "job_id", None),
            getattr(job, "status", None),
            getattr(job, "job_type", None),
        )
    print("[通过] list-jobs")


def case_list_only() -> None:
    case_list_specs()
    case_list_jobs()
    print("[通过] list-only")


def case_get_existing() -> None:
    if not EXISTING_JOB_ID:
        raise ConfigError("运行 --case get-existing 前请先填写 EXISTING_JOB_ID")
    client = create_client()
    job = get_job(client, EXISTING_JOB_ID)
    print_json("[通过] 查询已有任务", job)
    list_job_events_if_supported(client, EXISTING_JOB_ID)


def case_create_wait() -> None:
    client = create_client()
    request, payload = build_create_job_request()
    print_json("[创建任务请求]", payload)
    response = client.create_job(request)
    job_id = response.body.job_id
    print("[已创建]", job_id)
    status = wait_job(client, job_id, stop_when_running=False)
    print("[通过] create-wait ->", status)


def case_create_stop() -> None:
    client = create_client()
    request, payload = build_create_job_request()
    print_json("[创建任务请求]", payload)
    response = client.create_job(request)
    job_id = response.body.job_id
    print("[已创建]", job_id)
    status = wait_job(client, job_id, stop_when_running=True)
    print("[通过] create-stop ->", status)


def case_stop_existing() -> None:
    if not JOB_ID_TO_STOP:
        raise ConfigError("运行 --case stop-existing 前请先填写 JOB_ID_TO_STOP")
    client = create_client()
    print("[停止已有任务]", JOB_ID_TO_STOP)
    response = stop_job(client, JOB_ID_TO_STOP)
    print_json("[停止响应]", getattr(response, "body", response))
    status = wait_job(client, JOB_ID_TO_STOP, stop_when_running=False)
    print("[通过] stop-existing ->", status)


CASES = {
    "config-check": case_config_check,
    "list-only": case_list_only,
    "list-specs": case_list_specs,
    "list-jobs": case_list_jobs,
    "get-existing": case_get_existing,
    "create-wait": case_create_wait,
    "create-stop": case_create_stop,
    "stop-existing": case_stop_existing,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES.keys(), default="config-check")
    args = parser.parse_args()
    print("\n========== PAI-DLC 真实测试用例:", args.case, "==========")
    try:
        CASES[args.case]()
    except ConfigError as exc:
        print("[配置错误]", exc)
        sys.exit(2)
    except CloudApiError as exc:
        print("[云服务调用错误]", exc)
        sys.exit(1)
    except TimeoutError as exc:
        print("[超时]", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
