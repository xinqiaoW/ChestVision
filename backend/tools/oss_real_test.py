#!/usr/bin/env python3
"""
真实阿里云 OSS SDK 测试程序。

先在 backend/.env 填写 OSS_* 或 ALIBABA_CLOUD_* 环境变量，再运行：

  python backend/tools/oss_real_test.py --case config-check
  # 含义：只检查全局变量是否填写完整；不会访问 OSS，也不会产生对象或费用。

  python backend/tools/oss_real_test.py --case simple
  # 含义：测试服务端 OSS SDK 的基础对象操作：PutObject、HeadObject、GetObject、DeleteObject。
  #       这是确认 AccessKey、Bucket、Endpoint、Region 是否可用的最小连通性测试。

  python backend/tools/oss_real_test.py --case multipart
  # 含义：测试服务端 OSS SDK 的分片上传能力：初始化分片、上传分片、合并分片、HeadObject 校验。
  #       这是验证大文件/数据集上传所需底层能力是否正常。

  python backend/tools/oss_real_test.py --case flow
  # 含义：测试推荐业务流程：后端生成固定 object key 的短期 PUT 预签名 URL，
  #       模拟浏览器用 HTTP PUT 上传 ZIP，后端再 HeadObject 校验 metadata，
  #       然后写入 manifest.json 和 _SUCCESS，最后按配置清理本次测试对象。

  python backend/tools/oss_real_test.py --case signed-url
  # 含义：只测试最小化 PUT 预签名 URL 行为：签名一个文本对象 URL，
  #       用普通 HTTP PUT 上传，再用 HeadObject 校验。

依赖：
  pip install oss2

注意：
  这个脚本会访问真实 OSS。它会在指定 OSS_BUCKET 内创建、读取、列举和删除对象。
  它不会创建 bucket，也不会删除 bucket 本身。
  本文件同时模拟“后端”和“浏览器”：
    - 后端使用 AccessKey 生成预签名 URL、HeadObject 校验、写 manifest/_SUCCESS。
    - 浏览器只拿到短期 PUT 预签名 URL，用普通 HTTP PUT 上传，不接触 AccessKey。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError


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


load_backend_env()

# ══════════════════════════════════════════════════════════════
# 运行真实云测试前，请填写这些全局变量
# ══════════════════════════════════════════════════════════════

# 安全开关。
# 获取方式：不需要从控制台获取，这是脚本自己的保护开关。
# 使用方式：所有变量都填好，并且确认要访问真实 OSS 后，才改为 True。
# 保持 False 时，除了 config-check 外的真实 OSS 操作都会被拦截。
CONFIRM_REAL_CLOUD_CALLS = env_bool("CONFIRM_REAL_CLOUD_CALLS", False)

# 后端用于签发预签名 URL 的 OSS AccessKey ID。
# 这个 AccessKey 只应该存在于后端环境中，绝不能返回给浏览器。
# 可以是长期 RAM 用户 AK，也可以是后端通过 STS 获得的临时 AK。
# 获取方式：
#   1. 阿里云 RAM 控制台 -> 用户 -> 选择或创建 RAM 用户 -> 认证管理 -> 创建 AccessKey。
#   2. 给该 RAM 用户授予 OSS_BUCKET 下 OSS_TEST_PREFIX 前缀的最小权限。
#      对本脚本需要：
#        - 生成上传 URL/服务端写入：oss:PutObject
#        - 服务端校验：oss:GetObject 或 oss:HeadObject 相关权限
#        - 服务端列举处理结果：oss:ListObjects
#        - 测试清理：oss:DeleteObject
#   3. 如果使用 STS，则填写 AssumeRole 返回的临时 AccessKeyId，通常以 "STS." 开头。
#   4. 也可以不写在文件里，改用环境变量 OSS_ACCESS_KEY_ID 或 ALIBABA_CLOUD_ACCESS_KEY_ID。
# 注意：不要使用主账号 AccessKey，不要把真实密钥提交到代码仓库。
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID", "")

# 与 OSS_ACCESS_KEY_ID 配套的 AccessKey Secret。
# 获取方式：
#   创建 AccessKey 时只显示一次；如果忘记，只能重新创建新的 AccessKey，
#   并禁用或删除旧 AccessKey。
# 环境变量兜底：OSS_ACCESS_KEY_SECRET 或 ALIBABA_CLOUD_ACCESS_KEY_SECRET。
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "")

# 后端凭证使用 STS 时的 SecurityToken，可选。
# 获取方式：
#   1. 对有 OSS 权限的 RAM Role 调用 STS AssumeRole。
#   2. 将返回的 SecurityToken 填到这里。
#   3. 同时把返回的临时 AccessKeyId/AccessKeySecret 填到上面两个变量。
# 使用长期 RAM 用户 AK 时留空。
# 注意：
#   这个 token 也只属于后端凭证，不会返回给浏览器。
#   浏览器只拿预签名 URL。
# 环境变量兜底：OSS_SECURITY_TOKEN、OSS_SESSION_TOKEN 或 ALIBABA_CLOUD_SECURITY_TOKEN。
OSS_SECURITY_TOKEN = os.getenv("OSS_SECURITY_TOKEN", "")

# OSS Endpoint。
# 获取方式：
#   OSS 控制台 -> Bucket 列表 -> 选择目标 bucket -> 概览 -> Endpoint。
# 示例：
#   https://oss-cn-hangzhou.aliyuncs.com
# 说明：
#   - 本地电脑测试通常使用公网 endpoint。
#   - 脚本运行在阿里云同地域内网环境时，才使用 internal/VPC endpoint。
#   - 如果你的账号或 bucket 要求绑定自定义域名进行数据操作，就填自定义域名 endpoint。
#   - Endpoint 是访问入口；真正操作的对象仍然受 OSS_BUCKET 限定。
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "https://oss-cn-shanghai.aliyuncs.com")

# Bucket 所在地域 ID。
# 获取方式：
#   OSS 控制台 -> Bucket 列表 -> 选择目标 bucket -> 概览 -> 地域。
#   也可以从 endpoint 推断，例如 oss-cn-hangzhou 对应 cn-hangzhou。
# 示例：
#   cn-hangzhou、cn-shanghai、ap-southeast-1。
# OSS V4 签名需要这个字段。
OSS_REGION = os.getenv("OSS_REGION", os.getenv("PAI_REGION_ID", "cn-shanghai"))

# 已存在的 OSS Bucket 名称。
# 获取方式：
#   OSS 控制台 -> Bucket 列表 -> Bucket 名称。
# 重要说明：
#   - 脚本不会创建 bucket。
#   - 脚本不会删除 bucket。
#   - 脚本所有 put/head/get/list/delete 操作都发生在这个 bucket 内部。
#   - 后续 object key 都是 bucket 内部路径，而不是本地文件系统路径。
OSS_BUCKET = os.getenv("OSS_BUCKET", "")

# 测试对象在 bucket 内部使用的前缀。
# 获取方式：不需要从控制台获取，你自己指定一个专门用于测试的前缀即可。
# 示例：
#   remote-training-sdk-test/your-name
# 重要说明：
#   - 这是 OSS_BUCKET 内部的对象前缀，不是 bucket 外部路径。
#   - 脚本创建的对象 key 会类似：
#       {OSS_TEST_PREFIX}/20260717120000-abcd1234/dataset.zip
#   - 当 DELETE_OBJECTS_AFTER_TEST=True 时，脚本只删除本次生成的这些对象，
#     不会清空整个前缀，更不会删除 bucket。
OSS_TEST_PREFIX = os.getenv(
    "OSS_TEST_PREFIX",
    os.getenv("REMOTE_TRAIN_OSS_PREFIX", "upload/remote-training-sdk-test"),
)

# 是否在每个测试用例结束后删除本次创建的 OSS 对象。
# 是否测试删除操作：
#   - True：会测试删除指定 bucket 内“本次创建的对象”的操作，调用 bucket.delete_object(key)。
#   - False：不删除对象，方便你去 OSS 控制台检查，但对象会继续占用存储并可能产生费用。
# 删除范围：
#   - 只删除本脚本本次运行记录下来的 object key。
#   - 不删除 bucket。
#   - 不删除 OSS_TEST_PREFIX 下其他已有对象。
DELETE_OBJECTS_AFTER_TEST = True

# PUT 预签名 URL 的有效期，单位秒。
# 获取方式：不需要从控制台获取，是后端签名时指定的业务参数。
# 建议：
#   上传数据集时按文件大小设置，常见为 15 分钟到 1 小时。
#   有效期越短，URL 泄露后的可利用窗口越小。
PRESIGNED_PUT_EXPIRE_SECONDS = 15 * 60

# 分片上传测试的每片大小。
# 获取方式：不需要从控制台获取，是脚本参数。
# 说明：
#   这里用于小型 SDK 行为测试。真实大文件生产上传建议使用 OSS SDK 推荐的分片大小计算。
MULTIPART_PART_SIZE = 256 * 1024

# 当上面的 AK/token 变量为空时，脚本会尝试读取这些环境变量。
# 用途：
#   避免把密钥直接写进文件。
ENV_ACCESS_KEY_ID_KEYS = ("OSS_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_ID")
ENV_ACCESS_KEY_SECRET_KEYS = (
    "OSS_ACCESS_KEY_SECRET",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
)
ENV_SECURITY_TOKEN_KEYS = (
    "OSS_SECURITY_TOKEN",
    "OSS_SESSION_TOKEN",
    "ALIBABA_CLOUD_SECURITY_TOKEN",
)


class ConfigError(RuntimeError):
    pass


def first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def env_first(keys: tuple[str, ...]) -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return ""


def load_config() -> dict[str, str]:
    return {
        "access_key_id": first_non_empty(
            OSS_ACCESS_KEY_ID, env_first(ENV_ACCESS_KEY_ID_KEYS)
        ),
        "access_key_secret": first_non_empty(
            OSS_ACCESS_KEY_SECRET, env_first(ENV_ACCESS_KEY_SECRET_KEYS)
        ),
        "security_token": first_non_empty(
            OSS_SECURITY_TOKEN, env_first(ENV_SECURITY_TOKEN_KEYS)
        ),
        "endpoint": OSS_ENDPOINT,
        "region": OSS_REGION,
        "bucket": OSS_BUCKET,
        "prefix": OSS_TEST_PREFIX.strip("/"),
    }


def require_config(config: dict[str, str], require_confirm: bool = True) -> None:
    missing = [
        key
        for key in [
            "access_key_id",
            "access_key_secret",
            "endpoint",
            "region",
            "bucket",
        ]
        if not config.get(key)
    ]
    if missing:
        raise ConfigError(f"missing config: {', '.join(missing)}")
    prefix = config.get("prefix", "")
    if prefix.startswith("accesspoint/") or "/object/" in prefix:
        raise ConfigError(
            "OSS_TEST_PREFIX 看起来像接入点资源 ARN 片段。"
            "如果使用接入点，请把接入点别名填到 OSS_BUCKET；"
            "OSS_TEST_PREFIX 只填写 bucket/接入点内部的对象前缀，"
            "例如 remote-training-sdk-test，不要写 accesspoint/xxx/object/xxx。"
        )
    if require_confirm and not CONFIRM_REAL_CLOUD_CALLS:
        raise ConfigError("set CONFIRM_REAL_CLOUD_CALLS = True before real OSS calls")


def import_oss2():
    try:
        import oss2
    except ImportError as exc:
        raise RuntimeError("missing dependency: pip install oss2") from exc
    return oss2


def create_bucket():
    config = load_config()
    require_config(config)
    oss2 = import_oss2()
    if config["security_token"]:
        auth = oss2.StsAuth(
            config["access_key_id"],
            config["access_key_secret"],
            config["security_token"],
            auth_version="v4",
        )
    else:
        auth = oss2.Auth(config["access_key_id"], config["access_key_secret"])
    return oss2.Bucket(
        auth,
        config["endpoint"],
        config["bucket"],
        region=config["region"],
    )


def object_key(name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{OSS_TEST_PREFIX.strip('/')}/{ts}-{uuid.uuid4().hex[:8]}/{name}"


def make_dataset_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.yaml", "path: .\ntrain: images/train\nval: images/val\n")
        zf.writestr("images/train/a.jpg", b"fake-image-a")
        zf.writestr("labels/train/a.txt", "0 0.5 0.5 0.2 0.2\n")
        zf.writestr("images/val/b.jpg", b"fake-image-b")
        zf.writestr("labels/val/b.txt", "0 0.4 0.4 0.3 0.3\n")
    return buffer.getvalue()


def metadata_headers(
    upload_id: str, dataset_name: str, expected_size: int
) -> dict[str, str]:
    return {
        "x-oss-meta-upload-id": upload_id,
        "x-oss-meta-dataset-name": dataset_name,
        "x-oss-meta-expected-size": str(expected_size),
        "Content-Type": "application/zip",
    }


def print_head(head: Any) -> None:
    print("  status:", getattr(head, "status", None))
    print("  content_length:", getattr(head, "content_length", None))
    print("  etag:", getattr(head, "etag", None))
    headers = getattr(head, "headers", {}) or {}
    interesting = {
        key: value
        for key, value in headers.items()
        if key.lower().startswith("x-oss-meta-") or key.lower() in {"content-type"}
    }
    print("  interesting_headers:", interesting)


def cleanup(bucket: Any, keys: list[str]) -> None:
    """删除指定 bucket 内本次测试创建的对象。

    这里测试的是 Object 删除能力：bucket.delete_object(key)。
    key 是 OSS_BUCKET 内部的对象路径。
    这个函数不会删除 bucket，也不会操作 bucket 外部资源。
    """
    if not DELETE_OBJECTS_AFTER_TEST:
        print("[KEEP] objects were not deleted:")
        for key in keys:
            print("  oss://{}/{}".format(OSS_BUCKET, key))
        return
    for key in keys:
        try:
            bucket.delete_object(key)
            print("  deleted:", key)
        except Exception as exc:
            print("  delete failed:", key, exc)


def http_put_with_signed_url(url: str, content: bytes, headers: dict[str, str]) -> int:
    """使用预签名 URL 执行 HTTP PUT。

    如果 OSS 返回 403/4xx，这里会打印 OSS 错误响应体。
    排查时重点看 Code 字段：
      - SignatureDoesNotMatch：签名 URL 被改动、headers 不匹配、slash_safe 未设置等。
      - AccessDenied：RAM/Bucket Policy/接入点策略权限不足。
      - InvalidAccessKeyId：AK 错误或被禁用。
      - RequestTimeTooSkewed：本机时间和 OSS 服务端时间偏差过大。
    """
    request = urllib.request.Request(url, data=content, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print("  HTTP error:", exc.code)
        print("  OSS error body:")
        print(body)
        raise


def case_config_check() -> None:
    config = load_config()
    require_config(config, require_confirm=False)
    print("[PASS] config-check")
    printable = {
        k: ("***" if "access_key" in k or "secret" in k or "token" in k else v)
        for k, v in config.items()
    }
    printable["confirm_real_cloud_calls"] = CONFIRM_REAL_CLOUD_CALLS
    print(json.dumps(printable, indent=2, ensure_ascii=False))


def case_simple() -> None:
    bucket = create_bucket()
    key = object_key("simple.txt")
    content = f"hello oss sdk {time.time()}".encode("utf-8")
    print("[PUT]", key)
    result = bucket.put_object(key, content)
    print("  put status:", result.status, "etag:", result.etag)

    print("[HEAD]", key)
    head = bucket.head_object(key)
    print_head(head)
    assert head.content_length == len(content)

    print("[GET]", key)
    body = bucket.get_object(key).read()
    assert body == content
    print("  get bytes:", len(body))
    cleanup(bucket, [key])
    print("[PASS] simple")


def case_multipart() -> None:
    bucket = create_bucket()
    oss2 = import_oss2()
    key = object_key("multipart-dataset.zip")
    content = make_dataset_zip() * 2048
    upload_id_for_metadata = "upl_" + uuid.uuid4().hex[:10]
    headers = metadata_headers(upload_id_for_metadata, "sdk_multipart", len(content))

    print("[INIT MULTIPART]", key)
    init = bucket.init_multipart_upload(key, headers=headers)
    part_infos = []
    part_number = 1
    try:
        for offset in range(0, len(content), MULTIPART_PART_SIZE):
            part = content[offset : offset + MULTIPART_PART_SIZE]
            result = bucket.upload_part(key, init.upload_id, part_number, part)
            part_infos.append(oss2.models.PartInfo(part_number, result.etag))
            print(
                "  uploaded part:",
                part_number,
                "bytes:",
                len(part),
                "etag:",
                result.etag,
            )
            part_number += 1
        complete = bucket.complete_multipart_upload(key, init.upload_id, part_infos)
        print("  complete status:", complete.status, "etag:", complete.etag)
    except Exception:
        print("  abort multipart:", init.upload_id)
        bucket.abort_multipart_upload(key, init.upload_id)
        raise

    head = bucket.head_object(key)
    print("[HEAD]")
    print_head(head)
    assert head.content_length == len(content)
    cleanup(bucket, [key])
    print("[PASS] multipart")


def case_flow() -> None:
    bucket = create_bucket()
    dataset_zip = make_dataset_zip()
    upload_id = "upl_" + uuid.uuid4().hex[:10]
    dataset_id = "ds_" + uuid.uuid4().hex[:10]
    raw_key = object_key("dataset.zip")
    prefix = raw_key.rsplit("/", 1)[0] + "/processed/"
    manifest_key = prefix + "manifest.json"
    success_key = prefix + "_SUCCESS"
    headers = metadata_headers(upload_id, "sdk_flow", len(dataset_zip))
    keys = [raw_key, manifest_key, success_key]

    print("[BACKEND SIGN PUT URL]", raw_key)
    signed_put_url = bucket.sign_url(
        "PUT",
        raw_key,
        PRESIGNED_PUT_EXPIRE_SECONDS,
        headers=headers,
        # 关键：必须设置 slash_safe=True。
        # 否则 oss2 会把 object key 中的 "/" 编码成 "%2F"，
        # 例如 a/b.txt 变成 a%2Fb.txt，生成的预签名 URL 不能直接用于上传，
        # 常见表现就是访问/PUT 时返回 403 SignatureDoesNotMatch。
        slash_safe=True,
    )
    print("  signed url:", signed_put_url.split("?")[0] + "?...")
    print("  expire seconds:", PRESIGNED_PUT_EXPIRE_SECONDS)

    print("[BROWSER HTTP PUT RAW]", raw_key)
    # 这里模拟浏览器行为：只使用后端给的预签名 URL 和必须匹配的 headers。
    # 浏览器不需要、也不应该知道 OSS_ACCESS_KEY_ID/OSS_ACCESS_KEY_SECRET。
    status = http_put_with_signed_url(signed_put_url, dataset_zip, headers)
    print("  put response:", status)
    assert status in {200, 201}

    print("[HEAD RAW]")
    head = bucket.head_object(raw_key)
    print_head(head)
    assert head.content_length == len(dataset_zip)
    assert head.headers.get("x-oss-meta-upload-id") == upload_id
    assert head.headers.get("x-oss-meta-expected-size") == str(len(dataset_zip))

    manifest = {
        "dataset_id": dataset_id,
        "upload_id": upload_id,
        "source": {"bucket": OSS_BUCKET, "object_key": raw_key, "etag": head.etag},
        "format": "yolo",
        "splits": {
            "train": {"images": 1, "labels": 1},
            "val": {"images": 1, "labels": 1},
        },
        "classes": ["class_0"],
    }
    print("[PUT MANIFEST]", manifest_key)
    bucket.put_object(manifest_key, json.dumps(manifest).encode("utf-8"))

    success = {
        "dataset_id": dataset_id,
        "upload_id": upload_id,
        "manifest_key": manifest_key,
        "finished_at": datetime.now().isoformat(),
    }
    print("[PUT SUCCESS]", success_key)
    bucket.put_object(success_key, json.dumps(success).encode("utf-8"))

    print("[LIST PREFIX]", prefix)
    found = [obj.key for obj in bucket.list_objects(prefix=prefix).object_list]
    for key in found:
        print("  found:", key)
    assert manifest_key in found
    assert success_key in found

    loaded_manifest = json.loads(bucket.get_object(manifest_key).read().decode("utf-8"))
    assert loaded_manifest["upload_id"] == upload_id
    cleanup(bucket, keys)
    print("[PASS] flow")


def case_signed_url() -> None:
    bucket = create_bucket()
    key = object_key("signed-url.txt")
    content = b"hello via signed put url"
    headers = {"Content-Type": "text/plain"}
    print("[BACKEND SIGN PUT URL]", key)
    url = bucket.sign_url(
        "PUT",
        key,
        PRESIGNED_PUT_EXPIRE_SECONDS,
        headers=headers,
        # 关键：保持 object key 中的 "/" 不被编码成 "%2F"。
        # 阿里云 OSS 预签名 URL 文档明确要求带路径的 object key 使用 slash_safe=True。
        slash_safe=True,
    )
    print("  url:", url.split("?")[0] + "?...")

    print("[BROWSER HTTP PUT]", key)
    status = http_put_with_signed_url(url, content, headers)
    print("  put response:", status)
    assert status in {200, 201}

    head = bucket.head_object(key)
    print("[HEAD]")
    print_head(head)
    assert head.content_length == len(content)
    cleanup(bucket, [key])
    print("[PASS] signed-url")


CASES = {
    "config-check": case_config_check,
    "simple": case_simple,
    "multipart": case_multipart,
    "flow": case_flow,
    "signed-url": case_signed_url,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case", choices=["all", *CASES.keys()], default="config-check"
    )
    args = parser.parse_args()
    names = CASES.keys() if args.case == "all" else [args.case]
    try:
        for name in names:
            print("\n========== OSS REAL CASE:", name, "==========")
            CASES[name]()
    except ConfigError as exc:
        print("[CONFIG ERROR]", exc)
        sys.exit(2)


if __name__ == "__main__":
    main()
