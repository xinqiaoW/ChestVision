# -*- coding: utf-8 -*-
"""OSS CompleteMultipartUpload 事件转发到后端的 FC Python 3.10 函数。

部署方式：
1. 在函数计算 FC 中创建 Python 3.10 事件函数。
2. 如果文件名保持为 fc_oss_multipart_complete_handler.py，请把请求处理程序配置为：
   fc_oss_multipart_complete_handler.handler
   如果上传时改名为 main.py，请配置为 main.handler。
3. 使用 EventBridge 触发器，只订阅 OSS 事件：
   oss:ObjectCreated:CompleteMultipartUpload

必填环境变量：
- BACKEND_OSS_MULTIPART_CALLBACK_URL:
  后端内部回调完整地址，例如：
  https://api.example.com/api/training/remote/callbacks/oss/multipart-complete
- REMOTE_TRAINING_CALLBACK_SECRET:
  与后端 REMOTE_TRAINING_CALLBACK_SECRET 完全相同的随机长字符串。

可选环境变量：
- BACKEND_BUCKET_NAME_OVERRIDE:
  发送给后端的 bucket 值。若后端数据库保存的是 OSS 接入点 alias，而 EventBridge
  事件里的 bucket.name 是真实 bucket 名，则这里应填写后端 OSS_BUCKET 使用的 alias。
- ALLOWED_SOURCE_BUCKETS:
  允许处理的事件源 bucket，逗号分隔；为空表示不限制。
- ALLOWED_OBJECT_PREFIXES:
  允许处理的 object key 前缀，逗号分隔；为空表示不限制。
- HTTP_TIMEOUT_SECONDS:
  调用后端接口超时时间，默认 10 秒。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


OSS_COMPLETE_EVENT_TYPE = "oss:ObjectCreated:CompleteMultipartUpload"
OSS_COMPLETE_EVENT_NAME = "ObjectCreated:CompleteMultipartUpload"
DEFAULT_TIMEOUT_SECONDS = 10


class FunctionConfigError(RuntimeError):
    """FC 环境变量配置错误。"""


class BackendRetryableError(RuntimeError):
    """调用后端出现可重试错误。

    抛出该异常会让 FC 本次执行失败，从而交给 EventBridge/FC 的重试策略处理。
    """


def handler(event: bytes, context: Any) -> str:
    """FC 事件函数入口。

    Python 3 运行时传入的 event 是 bytes。函数可能收到单个 EventBridge 事件，
    也可能收到一组事件；这里统一转换成列表逐条处理。
    """
    try:
        payload = _load_event(event)
        events = _normalize_event_list(payload)
        results = [_handle_one_event(item) for item in events]
        retryable_errors = [item for item in results if item.get("retryable")]
        response = {
            "ok": not retryable_errors,
            "event_count": len(events),
            "results": results,
        }
        print(json.dumps(response, ensure_ascii=False))
        if retryable_errors:
            raise BackendRetryableError("存在可重试错误，触发 FC/EventBridge 重试")
        return json.dumps(response, ensure_ascii=False)
    except FunctionConfigError:
        traceback.print_exc()
        raise
    except Exception:
        traceback.print_exc()
        raise


def _handle_one_event(event: dict[str, Any]) -> dict[str, Any]:
    parsed = _extract_oss_event(event)
    event_type = parsed["event_type"]
    source_bucket = parsed["source_bucket"]
    object_key = parsed["object_key"]

    if event_type != OSS_COMPLETE_EVENT_TYPE:
        return {
            "ignored": True,
            "reason": "unsupported_event_type",
            "event_type": event_type,
            "object_key": object_key,
        }

    if not _is_allowed_bucket(source_bucket):
        return {
            "ignored": True,
            "reason": "bucket_not_allowed",
            "bucket": source_bucket,
            "object_key": object_key,
        }

    if not _is_allowed_prefix(object_key):
        return {
            "ignored": True,
            "reason": "prefix_not_allowed",
            "bucket": source_bucket,
            "object_key": object_key,
        }

    backend_payload = {
        "event_id": parsed["event_id"],
        "event_type": OSS_COMPLETE_EVENT_TYPE,
        "bucket": _backend_bucket_value(source_bucket),
        "object_key": object_key,
        "size": parsed["size"],
        "etag": parsed["etag"],
        "event_time": parsed["event_time"],
    }
    backend_result = _post_backend(backend_payload)
    return {
        "ignored": False,
        "bucket": backend_payload["bucket"],
        "object_key": object_key,
        "backend": backend_result,
    }


def _load_event(event: bytes | str | dict[str, Any] | list[Any]) -> Any:
    """把 FC 传入的 event 转成 Python 对象。"""
    if isinstance(event, (dict, list)):
        return event
    if isinstance(event, bytes):
        text = event.decode("utf-8")
    else:
        text = str(event)
    return json.loads(text)


def _normalize_event_list(payload: Any) -> list[dict[str, Any]]:
    """兼容单事件、events 数组和 Records 数组。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("事件 payload 必须是 JSON object 或 array")
    for key in ("events", "Records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload]


def _extract_oss_event(event: dict[str, Any]) -> dict[str, Any]:
    """从 EventBridge OSS 事件中提取后端需要的字段。

    官方 EventBridge OSS 事件主要字段：
    - type: oss:ObjectCreated:CompleteMultipartUpload
    - id: 事件 ID
    - time: 事件时间
    - data.eventName: ObjectCreated:CompleteMultipartUpload
    - data.oss.bucket.name: bucket 名称
    - data.oss.object.key: object key
    - data.oss.object.size: object 大小
    - data.oss.object.eTag: object ETag
    """
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    oss = data.get("oss") if isinstance(data.get("oss"), dict) else {}
    bucket_info = oss.get("bucket") if isinstance(oss.get("bucket"), dict) else {}
    object_info = oss.get("object") if isinstance(oss.get("object"), dict) else {}

    event_name = data.get("eventName") or event.get("eventName")
    event_type = event.get("type") or data.get("type")
    if not event_type and event_name:
        event_type = f"oss:{event_name}"

    bucket = (
        bucket_info.get("name")
        or data.get("bucket")
        or event.get("bucket")
        or ""
    )
    object_key = (
        object_info.get("key")
        or data.get("object_key")
        or data.get("objectKey")
        or event.get("object_key")
        or ""
    )

    if not bucket:
        raise ValueError("OSS 事件缺少 bucket")
    if not object_key:
        raise ValueError("OSS 事件缺少 object key")

    # EventBridge 通常直接给原始 key。这里做一次 unquote，兼容 URL 编码形式。
    object_key = urllib.parse.unquote(str(object_key))

    return {
        "event_id": event.get("id") or _nested_get(data, "responseElements", "requestId"),
        "event_type": event_type,
        "event_time": data.get("eventTime") or event.get("time"),
        "source_bucket": str(bucket),
        "object_key": object_key,
        "size": _to_int_or_none(object_info.get("size")),
        "etag": object_info.get("eTag") or object_info.get("etag"),
    }


def _post_backend(payload: dict[str, Any]) -> dict[str, Any]:
    """调用后端内部回调接口。"""
    callback_url = _required_env("BACKEND_OSS_MULTIPART_CALLBACK_URL")
    callback_secret = _required_env("REMOTE_TRAINING_CALLBACK_SECRET")
    timeout = _timeout_seconds()

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        callback_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {callback_secret}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return {
                "status": response.status,
                "body": _json_or_text(response_body),
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        result = {
            "status": exc.code,
            "body": _json_or_text(error_body),
        }
        # 5xx 通常是后端临时故障，应触发 FC/EventBridge 重试。
        # 4xx 通常是配置、权限或非本系统对象问题，不默认重试，避免毒消息反复执行。
        if exc.code >= 500:
            raise BackendRetryableError(json.dumps(result, ensure_ascii=False)) from exc
        return result
    except urllib.error.URLError as exc:
        raise BackendRetryableError(f"调用后端失败: {exc}") from exc


def _backend_bucket_value(source_bucket: str) -> str:
    """确定发送给后端的 bucket 字段。

    如果后端数据库保存的是接入点 alias，而 EventBridge 事件中是 bucket 原名，
    需要通过 BACKEND_BUCKET_NAME_OVERRIDE 做转换。
    """
    return os.getenv("BACKEND_BUCKET_NAME_OVERRIDE", "").strip() or source_bucket


def _is_allowed_bucket(bucket: str) -> bool:
    allowed = _csv_env("ALLOWED_SOURCE_BUCKETS")
    return not allowed or bucket in allowed


def _is_allowed_prefix(object_key: str) -> bool:
    prefixes = _csv_env("ALLOWED_OBJECT_PREFIXES")
    return not prefixes or any(object_key.startswith(prefix) for prefix in prefixes)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise FunctionConfigError(f"缺少环境变量: {name}")
    return value


def _csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _timeout_seconds() -> int:
    raw = os.getenv("HTTP_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise FunctionConfigError("HTTP_TIMEOUT_SECONDS 必须是整数") from exc
    if value <= 0:
        raise FunctionConfigError("HTTP_TIMEOUT_SECONDS 必须大于 0")
    return value


def _to_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested_get(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _json_or_text(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


if __name__ == "__main__":
    # 本地调试用法：
    # BACKEND_OSS_MULTIPART_CALLBACK_URL=... REMOTE_TRAINING_CALLBACK_SECRET=... \
    # python backend/tools/fc_oss_multipart_complete_handler.py /tmp/event.json
    event_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not event_path:
        raise SystemExit("用法: python fc_oss_multipart_complete_handler.py event.json")
    with open(event_path, "rb") as file:
        print(handler(file.read(), None))
