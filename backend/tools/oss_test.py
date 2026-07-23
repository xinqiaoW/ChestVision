#!/usr/bin/env python3
"""
Standalone simulation tests for the OSS upload and dataset-ready flow.

Run:
  python backend/tools/oss_test.py --case all

This file intentionally avoids real Aliyun dependencies. The simulated frontend
uses a backend-issued presigned PUT URL, so AccessKey never appears on the
frontend side. Replace MockOSS with a real OSS client later and keep the
service/state-machine assertions unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


VALID_DATASET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
BUCKET = "mock-chestx-remote-training"


class ObjectNotFound(Exception):
    pass


class ValidationError(Exception):
    pass


@dataclass
class ObjectRecord:
    bucket: str
    key: str
    content: bytes
    metadata: dict[str, str]
    etag: str
    created_at: datetime = field(default_factory=datetime.now)


class MockOSS:
    """In-memory OSS subset with multipart upload and HeadObject."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], ObjectRecord] = {}
        self.multipart: dict[str, dict[str, Any]] = {}

    def create_multipart_upload(
        self, bucket: str, key: str, metadata: dict[str, str] | None = None
    ) -> str:
        multipart_id = "mp-" + uuid.uuid4().hex[:12]
        self.multipart[multipart_id] = {
            "bucket": bucket,
            "key": key,
            "metadata": metadata or {},
            "parts": {},
        }
        return multipart_id

    def upload_part(self, multipart_id: str, part_number: int, data: bytes) -> None:
        if multipart_id not in self.multipart:
            raise ObjectNotFound(f"multipart upload not found: {multipart_id}")
        self.multipart[multipart_id]["parts"][part_number] = data

    def complete_multipart_upload(self, multipart_id: str) -> dict[str, Any]:
        session = self.multipart.pop(multipart_id)
        content = b"".join(
            session["parts"][part] for part in sorted(session["parts"].keys())
        )
        return self.put_object(
            bucket=session["bucket"],
            key=session["key"],
            content=content,
            metadata=session["metadata"],
        )

    def put_object(
        self,
        bucket: str,
        key: str,
        content: bytes,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        etag = hashlib.md5(content).hexdigest()
        self.objects[(bucket, key)] = ObjectRecord(
            bucket=bucket,
            key=key,
            content=content,
            metadata=metadata or {},
            etag=etag,
        )
        return {"bucket": bucket, "key": key, "etag": etag, "size": len(content)}

    def head_object(self, bucket: str, key: str) -> dict[str, Any]:
        obj = self.objects.get((bucket, key))
        if obj is None:
            raise ObjectNotFound(f"object not found: oss://{bucket}/{key}")
        return {
            "bucket": obj.bucket,
            "key": obj.key,
            "size": len(obj.content),
            "etag": obj.etag,
            "metadata": dict(obj.metadata),
            "created_at": obj.created_at,
        }

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    def get_json(self, bucket: str, key: str) -> dict[str, Any]:
        obj = self.objects.get((bucket, key))
        if obj is None:
            raise ObjectNotFound(f"object not found: oss://{bucket}/{key}")
        return json.loads(obj.content.decode("utf-8"))


@dataclass
class RemoteEvent:
    event_id: str
    event_type: str
    object_key: str
    status: str
    message: str
    received_at: datetime = field(default_factory=datetime.now)


class RemoteEventStore:
    """Small idempotency store for callback/EventBridge simulation."""

    def __init__(self) -> None:
        self.events: dict[str, RemoteEvent] = {}

    def seen(self, event_id: str) -> bool:
        return event_id in self.events

    def record(
        self, event_id: str, event_type: str, object_key: str, status: str, message: str
    ) -> RemoteEvent:
        event = RemoteEvent(event_id, event_type, object_key, status, message)
        self.events[event_id] = event
        return event


@dataclass
class UploadRecord:
    upload_id: str
    dataset_id: str
    user_id: int
    dataset_name: str
    bucket: str
    raw_object_key: str
    expected_size: int
    metadata: dict[str, str]
    expires_at: datetime
    status: str = "INITIATED"
    client_progress: int = 0
    actual_size: int | None = None
    etag: str | None = None
    processed_prefix: str | None = None
    manifest_key: str | None = None
    success_key: str | None = None
    processing_submitted: int = 0
    error_message: str | None = None
    history: list[str] = field(default_factory=list)

    def mark(self, status: str, message: str) -> None:
        self.status = status
        self.history.append(f"{status}: {message}")


class UploadService:
    """Backend-side upload state machine simulation."""

    def __init__(self, oss: MockOSS, event_store: RemoteEventStore) -> None:
        self.oss = oss
        self.events = event_store
        self.uploads: dict[str, UploadRecord] = {}

    def create_upload(
        self,
        user_id: int,
        dataset_name: str,
        filename: str,
        expected_size: int,
        expires_in_seconds: int = 3600,
    ) -> tuple[UploadRecord, dict[str, Any]]:
        if not VALID_DATASET_NAME.match(dataset_name):
            raise ValidationError(
                "dataset_name must contain only letters, digits, '_' or '-'"
            )
        if filename != "dataset.zip":
            raise ValidationError("remote upload currently accepts dataset.zip only")
        upload_id = "upl_" + uuid.uuid4().hex[:10]
        dataset_id = "ds_" + uuid.uuid4().hex[:10]
        raw_key = f"{user_id}/{upload_id}/dataset.zip"
        metadata = {
            "upload-id": upload_id,
            "user-id": str(user_id),
            "dataset-name": dataset_name,
            "expected-size": str(expected_size),
        }
        record = UploadRecord(
            upload_id=upload_id,
            dataset_id=dataset_id,
            user_id=user_id,
            dataset_name=dataset_name,
            bucket=BUCKET,
            raw_object_key=raw_key,
            expected_size=expected_size,
            metadata=metadata,
            expires_at=datetime.now() + timedelta(seconds=expires_in_seconds),
        )
        record.history.append("INITIATED: upload session created")
        self.uploads[upload_id] = record
        upload = {
            "method": "PUT",
            "url": f"https://{BUCKET}.mock-oss.local/{raw_key}?signature=mock",
            "object_key": raw_key,
            "headers": {
                "Content-Type": "application/zip",
                "x-oss-meta-upload-id": upload_id,
                "x-oss-meta-user-id": str(user_id),
                "x-oss-meta-dataset-name": dataset_name,
                "x-oss-meta-expected-size": str(expected_size),
            },
            "expire_seconds": expires_in_seconds,
        }
        return record, upload

    def heartbeat(self, upload_id: str, progress: int) -> UploadRecord:
        record = self._get(upload_id)
        record.client_progress = max(0, min(progress, 100))
        if record.status == "INITIATED":
            record.mark("UPLOADING", f"client progress {record.client_progress}%")
        else:
            record.history.append(
                f"{record.status}: client progress {record.client_progress}%"
            )
        return record

    def client_complete(self, upload_id: str) -> UploadRecord:
        record = self._get(upload_id)
        if record.status in {"READY", "FAILED", "EXPIRED", "CANCELLED"}:
            return record
        record.mark("CLIENT_COMPLETED", "client SDK reported upload success")
        try:
            self.verify_uploaded_object(record)
        except ObjectNotFound as exc:
            record.history.append(f"CLIENT_COMPLETED: waiting for object ({exc})")
        return record

    def handle_oss_event(
        self, event_id: str, event_type: str, bucket: str, object_key: str
    ) -> str:
        if self.events.seen(event_id):
            self.events.record(
                event_id + "#duplicate",
                event_type,
                object_key,
                "ignored",
                "duplicate event id",
            )
            return "ignored"

        record = self._find_by_object_key(object_key)
        if record is None:
            self.events.record(event_id, event_type, object_key, "ignored", "unknown key")
            return "ignored"

        try:
            if object_key == record.raw_object_key:
                self.verify_uploaded_object(record)
                self.events.record(event_id, event_type, object_key, "processed", "raw")
                return "processed"
            if object_key == record.success_key:
                self.verify_processed_dataset(record)
                self.events.record(
                    event_id, event_type, object_key, "processed", "success"
                )
                return "processed"
            self.events.record(event_id, event_type, object_key, "ignored", "not watched")
            return "ignored"
        except Exception as exc:
            self.events.record(event_id, event_type, object_key, "failed", str(exc))
            raise

    def verify_uploaded_object(self, record: UploadRecord) -> UploadRecord:
        if record.status in {"PROCESSING", "READY"}:
            record.history.append(f"{record.status}: raw object already verified")
            return record
        if record.status in {"FAILED", "EXPIRED", "CANCELLED"}:
            return record

        head = self.oss.head_object(record.bucket, record.raw_object_key)
        metadata = head["metadata"]
        if head["size"] != record.expected_size:
            self._fail(
                record,
                f"size mismatch: expected={record.expected_size}, actual={head['size']}",
            )
            return record
        for key, expected in record.metadata.items():
            actual = metadata.get(key)
            if actual != expected:
                self._fail(record, f"metadata mismatch: {key}={actual!r}")
                return record

        record.actual_size = head["size"]
        record.etag = head["etag"]
        record.mark("UPLOADED", "HeadObject validation passed")
        self.submit_processing_job(record)
        return record

    def submit_processing_job(self, record: UploadRecord) -> UploadRecord:
        if record.status != "UPLOADED":
            return record
        if record.processing_submitted:
            record.history.append("PROCESSING: preprocess job already submitted")
            return record
        prefix = f"datasets/processed/{record.dataset_name}/{record.dataset_id}/"
        record.processed_prefix = prefix
        record.manifest_key = prefix + "manifest.json"
        record.success_key = prefix + "_SUCCESS"
        record.processing_submitted += 1
        record.mark("PROCESSING", "mock preprocess job submitted")
        return record

    def simulate_preprocess_output(self, upload_id: str, include_manifest: bool = True) -> None:
        record = self._get(upload_id)
        if not record.manifest_key or not record.success_key:
            raise ValidationError("processing job has not been submitted")

        if include_manifest:
            manifest = {
                "dataset_id": record.dataset_id,
                "upload_id": record.upload_id,
                "source": {
                    "bucket": record.bucket,
                    "object_key": record.raw_object_key,
                    "etag": record.etag,
                },
                "format": "yolo",
                "splits": {
                    "train": {"images": 3, "labels": 3},
                    "val": {"images": 1, "labels": 1},
                },
                "classes": ["class_0", "class_1"],
            }
            self.oss.put_object(
                record.bucket,
                record.manifest_key,
                json.dumps(manifest).encode("utf-8"),
            )

        success = {
            "dataset_id": record.dataset_id,
            "upload_id": record.upload_id,
            "manifest_key": record.manifest_key,
            "finished_at": datetime.now().isoformat(),
        }
        self.oss.put_object(
            record.bucket,
            record.success_key,
            json.dumps(success).encode("utf-8"),
        )

    def verify_processed_dataset(self, record: UploadRecord) -> UploadRecord:
        if record.status == "READY":
            record.history.append("READY: processed output already verified")
            return record
        if record.status != "PROCESSING":
            return record
        if not record.manifest_key or not record.success_key:
            self._fail(record, "missing processed keys")
            return record
        if not self.oss.exists(record.bucket, record.success_key):
            return record
        if not self.oss.exists(record.bucket, record.manifest_key):
            self._fail(record, "success exists but manifest.json is missing")
            return record

        manifest = self.oss.get_json(record.bucket, record.manifest_key)
        success = self.oss.get_json(record.bucket, record.success_key)
        if manifest.get("upload_id") != record.upload_id:
            self._fail(record, "manifest upload_id mismatch")
            return record
        if success.get("dataset_id") != record.dataset_id:
            self._fail(record, "_SUCCESS dataset_id mismatch")
            return record

        record.mark("READY", "manifest.json and _SUCCESS verified")
        return record

    def reconcile(self) -> None:
        now = datetime.now()
        for record in self.uploads.values():
            if record.status in {"INITIATED", "UPLOADING"} and record.expires_at < now:
                record.mark("EXPIRED", "upload session expired")
                continue
            if record.status == "CLIENT_COMPLETED":
                try:
                    self.verify_uploaded_object(record)
                except ObjectNotFound:
                    record.history.append("CLIENT_COMPLETED: reconcile found no object")
            if record.status == "UPLOADED":
                self.submit_processing_job(record)
            if record.status == "PROCESSING" and record.success_key:
                self.verify_processed_dataset(record)

    def _get(self, upload_id: str) -> UploadRecord:
        if upload_id not in self.uploads:
            raise KeyError(upload_id)
        return self.uploads[upload_id]

    def _find_by_object_key(self, object_key: str) -> UploadRecord | None:
        for record in self.uploads.values():
            if object_key in {record.raw_object_key, record.success_key}:
                return record
        return None

    def _fail(self, record: UploadRecord, message: str) -> None:
        record.error_message = message
        record.mark("FAILED", message)


def make_dataset_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.yaml", "path: .\ntrain: images/train\nval: images/val\n")
        zf.writestr("images/train/a.jpg", b"fake-image-a")
        zf.writestr("labels/train/a.txt", "0 0.5 0.5 0.2 0.2\n")
        zf.writestr("images/val/b.jpg", b"fake-image-b")
        zf.writestr("labels/val/b.txt", "1 0.4 0.4 0.3 0.3\n")
    return buffer.getvalue()


def upload_with_presigned_put(
    oss: MockOSS, record: UploadRecord, upload: dict[str, Any], content: bytes
) -> None:
    assert upload["method"] == "PUT"
    assert upload["object_key"] == record.raw_object_key
    assert record.raw_object_key in upload["url"]
    # Simulate the browser sending the required signed headers. No AccessKey is
    # available in this function, by design.
    metadata = {
        "upload-id": upload["headers"]["x-oss-meta-upload-id"],
        "user-id": upload["headers"]["x-oss-meta-user-id"],
        "dataset-name": upload["headers"]["x-oss-meta-dataset-name"],
        "expected-size": upload["headers"]["x-oss-meta-expected-size"],
    }
    oss.put_object(record.bucket, record.raw_object_key, content, metadata=metadata)


def new_service() -> tuple[MockOSS, UploadService]:
    oss = MockOSS()
    events = RemoteEventStore()
    return oss, UploadService(oss, events)


def assert_status(record: UploadRecord, expected: str) -> None:
    assert record.status == expected, (
        f"expected status {expected}, got {record.status}; history={record.history}"
    )


def test_happy() -> UploadRecord:
    oss, service = new_service()
    content = make_dataset_zip()
    record, upload = service.create_upload(1, "chest_xray_v2", "dataset.zip", len(content))
    assert record.raw_object_key == f"1/{record.upload_id}/dataset.zip"
    assert record.raw_object_key in upload["url"]

    service.heartbeat(record.upload_id, 33)
    assert_status(record, "UPLOADING")
    upload_with_presigned_put(oss, record, upload, content)

    service.client_complete(record.upload_id)
    assert_status(record, "PROCESSING")
    assert record.processing_submitted == 1

    service.handle_oss_event(
        "evt-raw-1", "oss:ObjectCreated:CompleteMultipartUpload", record.bucket, record.raw_object_key
    )
    assert record.processing_submitted == 1

    service.simulate_preprocess_output(record.upload_id)
    service.handle_oss_event(
        "evt-success-1", "oss:ObjectCreated:PutObject", record.bucket, record.success_key
    )
    assert_status(record, "READY")
    return record


def test_duplicate_event() -> UploadRecord:
    oss, service = new_service()
    content = make_dataset_zip()
    record, upload = service.create_upload(1, "dup_case", "dataset.zip", len(content))
    upload_with_presigned_put(oss, record, upload, content)

    first = service.handle_oss_event(
        "evt-dup-raw", "oss:ObjectCreated:CompleteMultipartUpload", record.bucket, record.raw_object_key
    )
    second = service.handle_oss_event(
        "evt-dup-raw", "oss:ObjectCreated:CompleteMultipartUpload", record.bucket, record.raw_object_key
    )
    assert first == "processed"
    assert second == "ignored"
    assert record.processing_submitted == 1

    service.simulate_preprocess_output(record.upload_id)
    first_success = service.handle_oss_event(
        "evt-dup-success", "oss:ObjectCreated:PutObject", record.bucket, record.success_key
    )
    second_success = service.handle_oss_event(
        "evt-dup-success", "oss:ObjectCreated:PutObject", record.bucket, record.success_key
    )
    assert first_success == "processed"
    assert second_success == "ignored"
    assert_status(record, "READY")
    return record


def test_missing_event() -> UploadRecord:
    oss, service = new_service()
    content = make_dataset_zip()
    record, upload = service.create_upload(1, "missing_event", "dataset.zip", len(content))
    upload_with_presigned_put(oss, record, upload, content)

    service.client_complete(record.upload_id)
    assert_status(record, "PROCESSING")
    service.simulate_preprocess_output(record.upload_id)
    service.reconcile()
    assert_status(record, "READY")
    return record


def test_size_mismatch() -> UploadRecord:
    oss, service = new_service()
    content = make_dataset_zip()
    record, upload = service.create_upload(
        1, "size_mismatch", "dataset.zip", len(content) + 99
    )
    upload_with_presigned_put(oss, record, upload, content)
    service.client_complete(record.upload_id)
    assert_status(record, "FAILED")
    assert "size mismatch" in (record.error_message or "")
    return record


def test_object_missing() -> UploadRecord:
    _, service = new_service()
    record, _ = service.create_upload(1, "object_missing", "dataset.zip", 123)
    service.client_complete(record.upload_id)
    assert_status(record, "CLIENT_COMPLETED")
    assert "waiting for object" in record.history[-1]
    service.reconcile()
    assert_status(record, "CLIENT_COMPLETED")
    return record


def test_expired() -> UploadRecord:
    _, service = new_service()
    record, _ = service.create_upload(
        1, "expired_case", "dataset.zip", 123, expires_in_seconds=-1
    )
    time.sleep(0.01)
    service.reconcile()
    assert_status(record, "EXPIRED")
    return record


CASES = {
    "happy": test_happy,
    "duplicate-event": test_duplicate_event,
    "missing-event": test_missing_event,
    "size-mismatch": test_size_mismatch,
    "object-missing": test_object_missing,
    "expired": test_expired,
}


def run_case(name: str) -> None:
    record = CASES[name]()
    print(f"\n[PASS] {name} -> {record.status}")
    for item in record.history:
        print(f"  - {item}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=["all", *CASES.keys()],
        default="all",
        help="simulation case to run",
    )
    args = parser.parse_args()

    selected = CASES.keys() if args.case == "all" else [args.case]
    for name in selected:
        run_case(name)


if __name__ == "__main__":
    main()
