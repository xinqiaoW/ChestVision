"""远程训练指标接收与同步。"""

from __future__ import annotations

from typing import Any

from app.entity.db_models import RemoteTrainingJob, TrainingMetric, TrainingTask
from app.train.remote_train_errors import RemoteTrainingValidationError
from app.train.remote_train_utils import _hash_token, _now
from sqlalchemy.orm import Session


class RemoteTrainingMetricsMixin:
    """训练指标 callback、results.csv 同步和指标序列化。"""

    def handle_metric_callback(
        self,
        db: Session,
        task_uuid: str,
        token: str,
        epoch: int,
        total_epochs: int | None,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """接收 PAI-DLC 容器按 epoch 上报的训练监控指标。"""
        task = db.query(TrainingTask).filter(TrainingTask.task_uuid == task_uuid).first()
        if not task:
            raise RemoteTrainingValidationError("远程训练任务不存在")
        remote_job = (
            db.query(RemoteTrainingJob)
            .filter(RemoteTrainingJob.task_id == task.id)
            .first()
        )
        if not remote_job:
            raise RemoteTrainingValidationError("远程训练任务不存在")
        if remote_job.callback_token_hash != _hash_token(token):
            raise RemoteTrainingValidationError("回调 token 无效")

        metric = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task.id, TrainingMetric.epoch == epoch)
            .first()
        )
        if not metric:
            metric = TrainingMetric(task_id=task.id, epoch=epoch)
            db.add(metric)
        for field in [
            "box_loss",
            "cls_loss",
            "dfl_loss",
            "precision",
            "recall",
            "map50",
            "map50_95",
            "lr",
        ]:
            value = metrics.get(field)
            if value is not None:
                setattr(metric, field, float(value))

        total = total_epochs or task.epochs or 1
        task.current_epoch = max(task.current_epoch or 0, epoch)
        task.progress = max(
            task.progress or 0,
            min(int((task.current_epoch / total) * 100), 99),
        )
        if task.status not in {"completed", "failed", "cancelled"}:
            task.status = "running"
        task.started_at = task.started_at or _now()
        task.updated_at = _now()
        if remote_job.remote_status not in {"SUCCEEDED", "FAILED", "STOPPED"}:
            remote_job.remote_status = "RUNNING"
        remote_job.last_synced_at = _now()
        remote_job.updated_at = _now()
        db.commit()
        db.refresh(task)
        db.refresh(remote_job)
        return self.serialize_training(
            task,
            remote_job,
            latest_metric=self._metric_payload(metric),
        )

    def _sync_results_csv_metrics(
        self, db: Session, task: TrainingTask, remote_job: RemoteTrainingJob
    ) -> None:
        """从远程 results.csv 增量同步训练指标到 training_metrics。

        以 task_id + epoch 做幂等控制，避免轮询和 callback 重复触发时重复写入。
        """
        if not remote_job.results_csv_key or not self.storage.exists(remote_job.results_csv_key):
            return
        try:
            text = self.storage.get_text(remote_job.results_csv_key)
        except Exception:
            return
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        if len(rows) < 2:
            return
        headers = [h.strip() for h in rows[0].split(",")]
        existing_epochs = {
            item.epoch
            for item in db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task.id)
            .all()
        }
        max_epoch = task.current_epoch or 0
        for line in rows[1:]:
            values = [value.strip() for value in line.split(",")]
            row = dict(zip(headers, values))
            epoch_raw = row.get("epoch") or row.get("                  epoch")
            if epoch_raw is None:
                continue
            try:
                epoch = int(float(epoch_raw))
            except ValueError:
                continue
            if epoch in existing_epochs:
                max_epoch = max(max_epoch, epoch)
                continue
            metric = TrainingMetric(
                task_id=task.id,
                epoch=epoch,
                box_loss=self._float(row.get("train/box_loss")),
                cls_loss=self._float(row.get("train/cls_loss")),
                dfl_loss=self._float(row.get("train/dfl_loss")),
                precision=self._float(row.get("metrics/precision(B)")),
                recall=self._float(row.get("metrics/recall(B)")),
                map50=self._float(row.get("metrics/mAP50(B)")),
                map50_95=self._float(row.get("metrics/mAP50-95(B)")),
                lr=self._float(row.get("lr/pg0")),
            )
            db.add(metric)
            existing_epochs.add(epoch)
            max_epoch = max(max_epoch, epoch)
        if max_epoch:
            task.current_epoch = max(task.current_epoch or 0, max_epoch)
            if task.status != "completed":
                task.progress = max(
                    task.progress or 0,
                    min(int((task.current_epoch / max(task.epochs or 1, 1)) * 100), 99),
                )

    @staticmethod
    def _float(value: str | None) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _metric_payload(metric: TrainingMetric | None) -> dict[str, Any] | None:
        if not metric:
            return None
        return {
            "epoch": metric.epoch,
            "box_loss": metric.box_loss,
            "cls_loss": metric.cls_loss,
            "dfl_loss": metric.dfl_loss,
            "precision": metric.precision,
            "recall": metric.recall,
            "map50": metric.map50,
            "map50_95": metric.map50_95,
            "lr": metric.lr,
        }

    def _latest_metric_payload(
        self, db: Session, task_id: int
    ) -> dict[str, Any] | None:
        metric = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.desc())
            .first()
        )
        return self._metric_payload(metric)
