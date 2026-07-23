"""远程训练运行日志读取。"""

from __future__ import annotations

from typing import Any

from app.entity.db_models import RemoteTrainingJob
from app.train.remote_train_errors import RemoteTrainingValidationError
from sqlalchemy.orm import Session


class RemoteTrainingLogMixin:
    """从 OSS 训练输出目录读取 run.log。"""

    RUN_LOG_FILENAME = "run.log"

    def _run_log_key(self, remote_job: RemoteTrainingJob) -> str | None:
        output_prefix = (remote_job.output_prefix or "").strip("/")
        if not output_prefix:
            return None
        return f"{output_prefix}/{self.RUN_LOG_FILENAME}"

    def get_training_run_log(
        self,
        db: Session,
        task_id: int,
        user_id: int | None = None,
        start_line: int = 1,
        limit: int = 1000,
        tail: bool = False,
    ) -> dict[str, Any]:
        """返回训练运行日志。

        日志本体不落库。DLC 容器把 run.log 写入输出挂载目录，后端按
        remote_training_jobs.output_prefix 派生 OSS key 并读取对象内容。
        """
        task, remote_job = self._get_task_and_remote_job(db, task_id, user_id=user_id)
        key = self._run_log_key(remote_job)
        if not key or not self.storage.exists(key):
            return {
                "task_id": task.id,
                "task_uuid": task.task_uuid,
                "log_key": key,
                "exists": False,
                "total_lines": 0,
                "start_line": 1,
                "next_line": 1,
                "tail": tail,
                "truncated_head": False,
                "truncated_tail": False,
                "lines": [],
            }

        text = self.storage.get_text(key)
        all_lines = text.splitlines()
        total_lines = len(all_lines)
        limit = max(min(int(limit or 1000), 5000), 1)
        start_line = max(int(start_line or 1), 1)

        if tail:
            start_index = max(total_lines - limit, 0)
        else:
            start_index = min(start_line - 1, total_lines)
        selected = all_lines[start_index : start_index + limit]
        first_line_number = start_index + 1
        next_line = first_line_number + len(selected)

        return {
            "task_id": task.id,
            "task_uuid": task.task_uuid,
            "log_key": key,
            "uri": f"oss://{self.settings.oss_bucket}/{key}",
            "exists": True,
            "total_lines": total_lines,
            "start_line": first_line_number,
            "next_line": next_line,
            "tail": tail,
            "truncated_head": first_line_number > 1,
            "truncated_tail": next_line <= total_lines,
            "lines": [
                {"line_number": first_line_number + index, "content": content}
                for index, content in enumerate(selected)
            ],
        }

    def get_training_run_log_download_url(
        self,
        db: Session,
        task_id: int,
        user_id: int | None = None,
        expires_seconds: int = 600,
    ) -> dict[str, Any]:
        """签发 run.log 的短期 OSS 下载 URL。"""
        task, remote_job = self._get_task_and_remote_job(db, task_id, user_id=user_id)
        key = self._run_log_key(remote_job)
        if not key or not self.storage.exists(key):
            raise RemoteTrainingValidationError("运行日志尚未生成")
        expires_seconds = max(min(int(expires_seconds or 600), 3600), 60)
        return {
            "task_id": task.id,
            "task_uuid": task.task_uuid,
            "log_key": key,
            "uri": f"oss://{self.settings.oss_bucket}/{key}",
            "filename": f"training-run-log-{task.task_uuid}.log",
            "expires_seconds": expires_seconds,
            "download_url": self.storage.sign_get_url(key, expires_seconds),
        }
