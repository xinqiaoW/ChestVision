"""模型管理异常类型。"""

from __future__ import annotations


class ModelManagementError(Exception):
    """模型管理可预期业务错误。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
