"""Validation helpers for OSS multipart uploads."""

from __future__ import annotations

from typing import Any

from app.oss_multipart.errors import OssMultipartError, OssObjectSizeMismatchError


DEFAULT_MULTIPART_PART_SIZE = 32 * 1024 * 1024
MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10000
MAX_PARTS_PER_SIGN = 50


def normalize_part_size(part_size: int | None) -> int:
    size = part_size or DEFAULT_MULTIPART_PART_SIZE
    if size < MIN_MULTIPART_PART_SIZE:
        raise OssMultipartError("part_size 不能小于 5MiB")
    return size


def total_part_count(file_size: int, part_size: int) -> int:
    return (file_size + part_size - 1) // part_size


def validate_total_part_count(expected_size: int | None, part_size: int) -> int | None:
    if expected_size is None:
        return None
    total_parts = total_part_count(expected_size, part_size)
    if total_parts > MAX_MULTIPART_PARTS:
        raise OssMultipartError("文件分片数量超过 OSS 上限，请增大 part_size")
    return total_parts


def normalize_part_numbers(part_numbers: list[int]) -> list[int]:
    if not part_numbers:
        raise OssMultipartError("part_numbers 不能为空")
    if len(part_numbers) > MAX_PARTS_PER_SIGN:
        raise OssMultipartError(f"单次最多签名 {MAX_PARTS_PER_SIGN} 个 part")
    normalized: list[int] = []
    seen: set[int] = set()
    for value in part_numbers:
        if not isinstance(value, int):
            raise OssMultipartError("part_number 必须是整数")
        if value < 1 or value > MAX_MULTIPART_PARTS:
            raise OssMultipartError("part_number 必须在 1 到 10000 之间")
        if value in seen:
            raise OssMultipartError("part_numbers 不能包含重复值")
        seen.add(value)
        normalized.append(value)
    return sorted(normalized)


def normalize_completed_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not parts:
        raise OssMultipartError("parts 不能为空")
    if len(parts) > MAX_MULTIPART_PARTS:
        raise OssMultipartError("parts 数量超过 OSS 上限")

    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for part in parts:
        part_number = part.get("part_number")
        etag = str(part.get("etag") or "").strip()
        if not isinstance(part_number, int):
            raise OssMultipartError("part_number 必须是整数")
        if part_number < 1 or part_number > MAX_MULTIPART_PARTS:
            raise OssMultipartError("part_number 必须在 1 到 10000 之间")
        if part_number in seen:
            raise OssMultipartError("parts 不能包含重复 part_number")
        if not etag or len(etag) > 256:
            raise OssMultipartError("part etag 无效")
        seen.add(part_number)
        normalized.append({"part_number": part_number, "etag": etag})
    return sorted(normalized, key=lambda item: item["part_number"])


def validate_expected_part_count(
    expected_size: int | None,
    part_size: int,
    actual_part_count: int,
) -> None:
    if expected_size is None:
        return
    expected_parts = total_part_count(expected_size, part_size)
    if actual_part_count != expected_parts:
        raise OssMultipartError("分片数量与 expected_size 不匹配")


def validate_confirmed_object_size(
    expected_size: int | None,
    actual_size: int | None,
) -> None:
    if expected_size is None or actual_size is None:
        return
    if int(actual_size) != expected_size:
        raise OssObjectSizeMismatchError("OSS 对象大小与 expected_size 不一致")
