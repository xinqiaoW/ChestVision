"""远程训练业务编排服务 facade。"""

from __future__ import annotations

from app.train.remote_train_artifact_service import RemoteTrainingArtifactMixin
from app.train.remote_train_command_service import RemoteTrainingCommandMixin
from app.train.remote_train_config import RemoteTrainSettings, load_remote_train_settings
from app.train.remote_train_dataset_service import RemoteDatasetServiceMixin
from app.train.remote_train_dlc import PaiDlcGateway
from app.train.remote_train_error_service import RemoteTrainingErrorMixin
from app.train.remote_train_errors import RemoteTrainingValidationError
from app.train.remote_train_job_service import RemoteTrainingJobServiceMixin
from app.train.remote_train_metrics_service import RemoteTrainingMetricsMixin
from app.train.remote_train_serializers import RemoteTrainingSerializerMixin
from app.train.remote_train_storage import OssStorageGateway
from app.train.remote_train_validators import RemoteTrainingValidationMixin


__all__ = [
    "RemoteTrainingService",
    "RemoteTrainingValidationError",
    "remote_training_service",
]


class RemoteTrainingService(
    RemoteDatasetServiceMixin,
    RemoteTrainingJobServiceMixin,
    RemoteTrainingCommandMixin,
    RemoteTrainingArtifactMixin,
    RemoteTrainingMetricsMixin,
    RemoteTrainingErrorMixin,
    RemoteTrainingValidationMixin,
    RemoteTrainingSerializerMixin,
):
    """远程训练主服务。

    该服务不在 Web 进程中执行训练，只负责 OSS 与 PAI-DLC 编排。
    具体职责拆分在同目录下的 dataset/job/artifact/metrics/error 模块中。
    """

    def __init__(
        self,
        settings: RemoteTrainSettings | None = None,
        storage: OssStorageGateway | None = None,
        dlc: PaiDlcGateway | None = None,
    ):
        self.settings = settings or load_remote_train_settings()
        self._storage = storage
        self._dlc = dlc

    @property
    def storage(self) -> OssStorageGateway:
        """延迟创建 OSS 客户端。

        这样导入模块不会立即要求 OSS 环境变量完整，只有真实调用远程接口时才检查配置。
        """
        if self._storage is None:
            self._storage = OssStorageGateway(self.settings)
        return self._storage

    @property
    def dlc(self) -> PaiDlcGateway:
        """延迟创建 PAI-DLC 客户端，避免应用启动时访问云 SDK。"""
        if self._dlc is None:
            self._dlc = PaiDlcGateway(self.settings)
        return self._dlc


remote_training_service = RemoteTrainingService()
