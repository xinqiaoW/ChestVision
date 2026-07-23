"""远程训练对象存储封装。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.train.remote_train_config import RemoteTrainSettings


@dataclass(frozen=True)
class ObjectRef:
    """统一对象引用。

    业务层不要直接拼接下载地址或本地路径，而是先记录对象属于哪个存储后端。
    这样同一模型可以同时有 OSS 权威副本、本地推理缓存和 MinIO 兼容副本。
    """

    # storage 标识对象所在后端：oss/local/minio/http。
    storage: str
    # OSS/MinIO 对象定位字段；本地缓存不使用 bucket/key。
    bucket: str | None = None
    key: str | None = None
    # 服务端本地文件路径，只用于推理缓存或开发环境。
    local_path: str | None = None
    # 可访问 URL，可是预签名 URL、MinIO URL 或外部 HTTP 地址。
    url: str | None = None

    @property
    def uri(self) -> str:
        if self.storage == "oss" and self.bucket and self.key:
            return f"oss://{self.bucket}/{self.key}"
        if self.storage == "local" and self.local_path:
            return self.local_path
        if self.url:
            return self.url
        return ""


class OssStorageGateway:
    """OSS SDK 的薄封装。

    只保留远程训练编排需要的对象能力：签名上传、HeadObject、存在性校验。
    """

    def __init__(self, settings: RemoteTrainSettings):
        settings.require_oss()
        self.settings = settings
        self.oss2 = self._import_oss2()
        self.bucket = self._create_bucket()

    @staticmethod
    def _import_oss2():
        try:
            import oss2
        except ImportError as exc:
            raise RuntimeError("缺少 OSS SDK，请安装 oss2") from exc
        return oss2

    def _create_bucket(self):
        if self.settings.oss_security_token:
            auth = self.oss2.StsAuth(
                self.settings.oss_access_key_id,
                self.settings.oss_access_key_secret,
                self.settings.oss_security_token,
                auth_version="v4",
            )
        else:
            auth = self.oss2.Auth(
                self.settings.oss_access_key_id,
                self.settings.oss_access_key_secret,
            )
        return self.oss2.Bucket(
            auth,
            self.settings.oss_endpoint,
            self.settings.oss_bucket,
            region=self.settings.oss_region,
        )

    def ref(self, key: str) -> ObjectRef:
        return ObjectRef(storage="oss", bucket=self.settings.oss_bucket, key=key)

    def sign_put_url(
        self,
        key: str,
        expires_seconds: int,
        headers: dict[str, str] | None = None,
    ) -> str:
        """生成短期 PUT 预签名 URL。

        URL 只允许上传到指定 object key，不暴露 AccessKeySecret。
        headers 会参与签名，客户端必须原样携带。
        """
        return self.bucket.sign_url(
            "PUT",
            key,
            expires_seconds,
            headers=headers or {},
            slash_safe=True,
        )

    def init_multipart_upload(
        self,
        key: str,
        headers: dict[str, str] | None = None,
    ) -> str:
        """初始化 OSS 分片上传并返回 OSS upload_id。

        这里由后端调用 OSS SDK，因此浏览器不需要 AccessKey。
        headers 用于写入对象 metadata，最终对象合并后会保留这些元信息。
        """
        result = self.bucket.init_multipart_upload(key, headers=headers or {})
        upload_id = getattr(result, "upload_id", None)
        if not upload_id:
            raise RuntimeError("OSS 未返回 multipart upload_id")
        return upload_id

    def sign_upload_part_url(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int,
    ) -> str:
        """生成单个分片的短期 PUT 预签名 URL。

        每个 part 的签名必须绑定 partNumber 和 uploadId，因此不能复用。
        """
        return self.bucket.sign_url(
            "PUT",
            key,
            expires_seconds,
            params={
                "partNumber": str(part_number),
                "uploadId": upload_id,
            },
            slash_safe=True,
        )

    def sign_get_url(self, key: str, expires_seconds: int) -> str:
        """生成短期 GET 预签名 URL，用于浏览器直接下载 OSS 大文件。"""
        return self.bucket.sign_url("GET", key, expires_seconds, slash_safe=True)

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """合并 OSS 分片并返回 OSS 响应摘要。

        parts 必须按 part_number 升序传入，包含客户端上传每片后拿到的 ETag。
        """
        part_infos = [
            self.oss2.models.PartInfo(
                part["part_number"],
                part["etag"],
            )
            for part in parts
        ]
        result = self.bucket.complete_multipart_upload(key, upload_id, part_infos)
        return {
            "etag": getattr(result, "etag", None),
            "request_id": getattr(result, "request_id", None),
        }

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """取消未完成的 OSS 分片上传，释放 OSS 侧未合并分片。"""
        self.bucket.abort_multipart_upload(key, upload_id)

    def head_object(self, key: str) -> dict[str, Any]:
        """读取 OSS 对象元信息，用于可信校验上传与训练产物。"""
        head = self.bucket.head_object(key)
        headers = dict(getattr(head, "headers", {}) or {})
        return {
            "content_length": getattr(head, "content_length", None),
            "etag": getattr(head, "etag", None),
            "content_type": headers.get("Content-Type") or headers.get("content-type"),
            "headers": headers,
        }

    def exists(self, key: str) -> bool:
        try:
            self.bucket.head_object(key)
            return True
        except Exception:
            return False

    def delete_object(self, key: str) -> None:
        """Delete one OSS object by key."""
        self.bucket.delete_object(key)

    def delete_prefix(self, prefix: str) -> list[str]:
        """Delete all OSS objects under a prefix and return deleted keys."""
        normalized = (prefix or "").strip("/")
        if not normalized:
            raise ValueError("OSS prefix 不能为空")
        normalized += "/"
        deleted: list[str] = []
        for obj in self.oss2.ObjectIterator(self.bucket, prefix=normalized):
            self.bucket.delete_object(obj.key)
            deleted.append(obj.key)
        return deleted

    def download_to_file(self, key: str, local_path: str) -> None:
        """Download one OSS object to a local file path."""
        self.bucket.get_object_to_file(key, local_path)

    def get_text(self, key: str) -> str:
        result = self.bucket.get_object(key)
        return result.read().decode("utf-8")
