"""Data structures for the shared OSS multipart upload workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MultipartUploadTarget:
    """A business-owned upload session mapped to one OSS object."""

    upload_id: str
    status: str
    bucket: str
    object_key: str
    oss_upload_id: str
    part_size: int
    expected_size: int | None
    expires_seconds: int


@dataclass(frozen=True)
class SignedPart:
    """A short-lived presigned URL for one OSS multipart part."""

    part_number: int
    method: str
    url: str
    headers: dict[str, str]
    expires_seconds: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "part_number": self.part_number,
            "method": self.method,
            "url": self.url,
            "headers": self.headers,
            "expires_seconds": self.expires_seconds,
        }


@dataclass(frozen=True)
class ConfirmedObject:
    """Result of backend-driven CompleteMultipartUpload plus HeadObject."""

    object_key: str
    content_length: int | None
    etag: str | None
    content_type: str | None
    headers: dict[str, Any]
    part_count: int
    complete_result: dict[str, Any]
