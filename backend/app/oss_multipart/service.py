"""Shared OSS file multipart upload workflow."""

from __future__ import annotations

from app.oss_multipart.errors import OssMultipartError
from app.oss_multipart.schemas import ConfirmedObject, MultipartUploadTarget, SignedPart
from app.oss_multipart.validators import (
    normalize_completed_parts,
    normalize_part_numbers,
    validate_confirmed_object_size,
    validate_expected_part_count,
)
from app.train.remote_train_storage import OssStorageGateway


class OssFileMultipartUploadService:
    """Business-neutral OSS multipart upload operations.

    This layer owns the protocol-level sequence:
    init multipart upload, sign part URLs, backend complete, HeadObject confirm,
    and abort. Dataset/model services own database status and business records.
    """

    def __init__(self, storage: OssStorageGateway):
        self.storage = storage

    def init_upload(
        self,
        object_key: str,
        headers: dict[str, str] | None = None,
    ) -> str:
        return self.storage.init_multipart_upload(object_key, headers=headers)

    def sign_parts(
        self,
        target: MultipartUploadTarget,
        part_numbers: list[int],
        expires_seconds: int | None = None,
    ) -> list[SignedPart]:
        expires = expires_seconds or target.expires_seconds
        if expires < 60 or expires > target.expires_seconds:
            raise OssMultipartError("expires_seconds 超出允许范围")
        return [
            SignedPart(
                part_number=part_number,
                method="PUT",
                url=self.storage.sign_upload_part_url(
                    target.object_key,
                    target.oss_upload_id,
                    part_number,
                    expires,
                ),
                headers={},
                expires_seconds=expires,
            )
            for part_number in normalize_part_numbers(part_numbers)
        ]

    def complete_and_confirm(
        self,
        target: MultipartUploadTarget,
        parts: list[dict],
    ) -> ConfirmedObject:
        normalized_parts = normalize_completed_parts(parts)
        validate_expected_part_count(
            target.expected_size,
            target.part_size,
            len(normalized_parts),
        )
        complete_result = self.storage.complete_multipart_upload(
            target.object_key,
            target.oss_upload_id,
            normalized_parts,
        )
        head = self.storage.head_object(target.object_key)
        actual_size = head.get("content_length")
        validate_confirmed_object_size(target.expected_size, actual_size)
        return ConfirmedObject(
            object_key=target.object_key,
            content_length=actual_size,
            etag=complete_result.get("etag") or head.get("etag"),
            content_type=head.get("content_type"),
            headers=head.get("headers") or {},
            part_count=len(normalized_parts),
            complete_result=complete_result,
        )

    def abort(self, target: MultipartUploadTarget) -> None:
        self.storage.abort_multipart_upload(target.object_key, target.oss_upload_id)
