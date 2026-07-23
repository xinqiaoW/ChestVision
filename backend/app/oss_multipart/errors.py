"""Shared OSS multipart upload errors."""

from __future__ import annotations


class OssMultipartError(ValueError):
    """Expected OSS multipart workflow validation error."""


class OssObjectSizeMismatchError(OssMultipartError):
    """The merged OSS object size does not match the declared upload size."""
