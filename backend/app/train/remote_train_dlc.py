"""PAI-DLC 网关。"""

from __future__ import annotations

import os
from typing import Any

from app.train.remote_train_config import RemoteTrainSettings


class PaiDlcGateway:
    """PAI-DLC Python SDK 的薄封装。

    这里不承载业务状态机，只负责把后端 payload 发给 DLC 控制面。
    业务状态、幂等、产物校验都在 RemoteTrainingService 中完成。
    """

    def __init__(self, settings: RemoteTrainSettings):
        settings.require_pai()
        self.settings = settings
        self.client, self.models = self._create_client()

    @staticmethod
    def _import_sdk():
        try:
            from alibabacloud_credentials.client import Client as CredClient
            from alibabacloud_pai_dlc20201203 import models as dlc_models
            from alibabacloud_pai_dlc20201203.client import Client as DlcClient
            from alibabacloud_tea_openapi.models import Config
        except ImportError as exc:
            raise RuntimeError(
                "缺少 PAI-DLC SDK，请安装 alibabacloud_credentials "
                "alibabacloud_tea_openapi alibabacloud_pai_dlc20201203"
            ) from exc
        return CredClient, DlcClient, dlc_models, Config

    def _create_client(self):
        """创建 DLC SDK Client。

        阿里云 credentials SDK 默认读环境变量，因此这里把远程训练配置复制到环境变量。
        这些凭证只在服务端使用，不返回给浏览器或训练容器。
        """
        os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"] = self.settings.pai_access_key_id
        os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"] = (
            self.settings.pai_access_key_secret
        )
        if self.settings.pai_security_token:
            os.environ["ALIBABA_CLOUD_SECURITY_TOKEN"] = self.settings.pai_security_token
        CredClient, DlcClient, dlc_models, TeaConfig = self._import_sdk()
        endpoint = (
            self.settings.pai_endpoint
            or f"pai-dlc.{self.settings.pai_region_id}.aliyuncs.com"
        )
        client = DlcClient(
            TeaConfig(
                credential=CredClient(),
                region_id=self.settings.pai_region_id,
                endpoint=endpoint,
            )
        )
        return client, dlc_models

    def create_job(self, payload: dict[str, Any]) -> str:
        """提交 CreateJob 并返回 PAI-DLC Job ID。"""
        request = self.models.CreateJobRequest().from_map(payload)
        response = self.client.create_job(request)
        return response.body.job_id

    def get_job(self, job_id: str) -> Any:
        """查询远程 Job 详情，供轮询 Worker 和状态接口使用。"""
        return self.client.get_job(job_id, self.models.GetJobRequest()).body

    def stop_job(self, job_id: str) -> Any:
        """停止远程 Job。

        不同 SDK 小版本的 stop_job 签名不完全一致，因此兼容两种调用形式。
        """
        stop_request_cls = getattr(self.models, "StopJobRequest", None)
        if stop_request_cls:
            try:
                return self.client.stop_job(job_id, stop_request_cls())
            except TypeError:
                pass
        return self.client.stop_job(job_id)
