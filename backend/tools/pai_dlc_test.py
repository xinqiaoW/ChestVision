#!/usr/bin/env python3
"""
PAI-DLC 远程训练编排的单文件模拟测试。

运行：
  python backend/tools/pai_dlc_test.py --case all

这个文件故意不依赖真实阿里云 SDK。
后续接入真实云端时，可以把 MockDlcClient 和 MockArtifactStore
替换为真实 PAI-DLC/OSS 客户端，同时保留这里的状态机断言。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ValidationError(Exception):
    pass


class CallbackAuthError(Exception):
    pass


class ArtifactMissing(Exception):
    pass


@dataclass
class TrainingMetric:
    epoch: int
    box_loss: float
    cls_loss: float
    dfl_loss: float
    precision: float
    recall: float
    map50: float
    map50_95: float
    lr: float


@dataclass
class TrainingTask:
    id: int
    task_uuid: str
    dataset_id: str
    dataset_prefix: str
    status: str
    model_name: str
    epochs: int
    img_size: int
    batch_size: int
    optimizer: str
    lr0: float
    current_epoch: int = 0
    progress: int = 0
    error_message: str | None = None
    model_version: dict[str, Any] | None = None
    history: list[str] = field(default_factory=list)

    def mark(self, status: str, message: str) -> None:
        self.status = status
        self.history.append(f"{status}: {message}")


@dataclass
class RemoteTrainingJob:
    task_id: int
    dataset_id: str
    dlc_job_id: str | None
    remote_status: str
    output_prefix: str
    callback_token_hash: str
    payload: dict[str, Any]
    callback_events: set[str] = field(default_factory=set)
    error_message: str | None = None


class MockArtifactStore:
    """内存版训练产物存储，用来模拟 OSS 训练输出。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, content: bytes | str) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.objects[key] = content

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_text(self, key: str) -> str:
        if key not in self.objects:
            raise ArtifactMissing(key)
        return self.objects[key].decode("utf-8")

    def get_json(self, key: str) -> dict[str, Any]:
        return json.loads(self.get_text(key))


@dataclass
class MockDlcJob:
    job_id: str
    payload: dict[str, Any]
    scenario: str
    status: str = "SUBMITTED"
    tick: int = 0
    stopped: bool = False
    metrics: list[dict[str, Any]] = field(default_factory=list)
    artifacts_written: bool = False


class MockDlcClient:
    """内存版 PAI-DLC 客户端，模拟 create/get/stop 和确定性进度。"""

    def __init__(self, artifact_store: MockArtifactStore, scenario: str = "success"):
        self.artifacts = artifact_store
        self.scenario = scenario
        self.jobs: dict[str, MockDlcJob] = {}

    def create_job(self, payload: dict[str, Any]) -> str:
        required = [
            "WorkspaceId",
            "DisplayName",
            "JobType",
            "JobSpecs",
            "UserCommand",
            "Envs",
        ]
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise ValidationError(f"CreateJob 请求缺少字段：{', '.join(missing)}")

        job_id = "dlc_" + uuid.uuid4().hex[:10]
        self.jobs[job_id] = MockDlcJob(
            job_id=job_id, payload=payload, scenario=self.scenario
        )
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self._get(job_id)
        if job.stopped:
            job.status = "STOPPED"
            return self._snapshot(job)

        job.tick += 1
        if job.scenario == "failed":
            job.status = ["SUBMITTED", "QUEUED", "RUNNING", "FAILED"][
                min(job.tick - 1, 3)
            ]
        else:
            job.status = ["SUBMITTED", "QUEUED", "RUNNING", "RUNNING", "SUCCEEDED"][
                min(job.tick - 1, 4)
            ]

        if job.status == "RUNNING":
            self._append_metric(job)
        if job.status == "SUCCEEDED" and not job.artifacts_written:
            self._write_success_artifacts(job)
        return self._snapshot(job)

    def stop_job(self, job_id: str) -> None:
        job = self._get(job_id)
        job.stopped = True
        job.status = "STOPPED"

    def force_succeed(self, job_id: str) -> None:
        job = self._get(job_id)
        job.status = "SUCCEEDED"
        if not job.artifacts_written:
            self._append_metric(job)
            self._append_metric(job)
            self._write_success_artifacts(job)

    def _append_metric(self, job: MockDlcJob) -> None:
        epoch = len(job.metrics) + 1
        if epoch > int(job.payload["Envs"].get("EPOCHS", 5)):
            return
        job.metrics.append(
            {
                "epoch": epoch,
                "box_loss": round(1.0 / (epoch + 1), 4),
                "cls_loss": round(0.8 / (epoch + 1), 4),
                "dfl_loss": round(0.6 / (epoch + 1), 4),
                "precision": round(0.5 + 0.05 * epoch, 4),
                "recall": round(0.45 + 0.04 * epoch, 4),
                "map50": round(0.4 + 0.06 * epoch, 4),
                "map50_95": round(0.25 + 0.04 * epoch, 4),
                "lr": 0.01,
            }
        )

    def _write_success_artifacts(self, job: MockDlcJob) -> None:
        output_prefix = job.payload["Envs"]["OUTPUT_PREFIX"]
        results_key = output_prefix + "results.csv"
        best_key = output_prefix + "weights/best.pt"
        report_key = output_prefix + "eval_report.json"
        success_key = output_prefix + "_SUCCESS"

        rows = job.metrics or [
            {
                "epoch": 1,
                "box_loss": 0.5,
                "cls_loss": 0.4,
                "dfl_loss": 0.3,
                "precision": 0.55,
                "recall": 0.49,
                "map50": 0.46,
                "map50_95": 0.29,
                "lr": 0.01,
            }
        ]
        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        self.artifacts.put(results_key, csv_buf.getvalue())

        if job.scenario != "missing-artifact":
            self.artifacts.put(best_key, b"mock-best-weights")

        self.artifacts.put(
            report_key,
            json.dumps(
                {
                    "overall": {
                        "precision": rows[-1]["precision"],
                        "recall": rows[-1]["recall"],
                        "map50": rows[-1]["map50"],
                        "map50_95": rows[-1]["map50_95"],
                    }
                }
            ),
        )
        self.artifacts.put(
            success_key,
            json.dumps(
                {
                    "job_id": job.job_id,
                    "task_uuid": job.payload["Envs"]["TASK_UUID"],
                    "results_csv_key": results_key,
                    "best_weight_key": best_key,
                    "finished_at": datetime.now().isoformat(),
                }
            ),
        )
        job.artifacts_written = True

    def _snapshot(self, job: MockDlcJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "metrics": list(job.metrics),
            "error_message": "模拟远程训练失败"
            if job.status == "FAILED"
            else None,
        }

    def _get(self, job_id: str) -> MockDlcJob:
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.jobs[job_id]


class RemoteTrainingService:
    """后端远程训练编排逻辑的模拟实现。"""

    def __init__(self, dlc: MockDlcClient, artifacts: MockArtifactStore) -> None:
        self.dlc = dlc
        self.artifacts = artifacts
        self.tasks: dict[int, TrainingTask] = {}
        self.remote_jobs: dict[int, RemoteTrainingJob] = {}
        self.metrics: dict[int, list[TrainingMetric]] = {}
        self.next_task_id = 1

    def start_remote_training(
        self,
        dataset_id: str = "ds_ready",
        dataset_prefix: str = "datasets/processed/chest_xray/ds_ready/",
        dataset_ready: bool = True,
        model_name: str = "yolo11n",
        epochs: int = 5,
    ) -> tuple[TrainingTask, str]:
        if not dataset_ready:
            raise ValidationError("远程训练要求数据集状态为 READY")

        task_id = self.next_task_id
        self.next_task_id += 1
        task_uuid = uuid.uuid4().hex[:8]
        output_prefix = f"training/jobs/{task_uuid}/"
        callback_token = "cb_" + uuid.uuid4().hex
        callback_token_hash = self._hash(callback_token)

        task = TrainingTask(
            id=task_id,
            task_uuid=task_uuid,
            dataset_id=dataset_id,
            dataset_prefix=dataset_prefix,
            status="pending",
            model_name=model_name,
            epochs=epochs,
            img_size=640,
            batch_size=16,
            optimizer="SGD",
            lr0=0.01,
        )
        task.history.append("pending: 本地训练任务记录已创建")

        payload = self._build_payload(task, output_prefix, callback_token)
        remote_job = RemoteTrainingJob(
            task_id=task.id,
            dataset_id=dataset_id,
            dlc_job_id=None,
            remote_status="CREATED",
            output_prefix=output_prefix,
            callback_token_hash=callback_token_hash,
            payload=payload,
        )

        dlc_job_id = self.dlc.create_job(payload)
        remote_job.dlc_job_id = dlc_job_id
        remote_job.remote_status = "SUBMITTED"
        task.history.append(f"pending: PAI-DLC 任务已提交 {dlc_job_id}")

        self.tasks[task_id] = task
        self.remote_jobs[task_id] = remote_job
        self.metrics[task_id] = []
        return task, callback_token

    def poll(self, task_id: int) -> TrainingTask:
        task = self.tasks[task_id]
        remote = self.remote_jobs[task_id]
        if remote.dlc_job_id is None:
            raise ValidationError("远程任务尚未提交")

        info = self.dlc.get_job(remote.dlc_job_id)
        remote.remote_status = self._normalize_status(info["status"])

        if remote.remote_status in {"SUBMITTED", "QUEUED"}:
            task.mark("pending", f"远程状态 {remote.remote_status}")
        elif remote.remote_status == "RUNNING":
            task.mark("running", "远程任务运行中")
            self._sync_metrics(task, info["metrics"])
        elif remote.remote_status == "SUCCEEDED":
            self._sync_metrics(task, info["metrics"])
            self._complete_from_artifacts(task, remote)
        elif remote.remote_status == "FAILED":
            task.error_message = info.get("error_message") or "远程任务失败"
            task.mark("failed", task.error_message)
            remote.error_message = task.error_message
        elif remote.remote_status == "STOPPED":
            task.mark("cancelled", "远程任务已停止")

        return task

    def stop(self, task_id: int) -> TrainingTask:
        remote = self.remote_jobs[task_id]
        if remote.dlc_job_id is None:
            raise ValidationError("远程任务尚未提交")
        self.dlc.stop_job(remote.dlc_job_id)
        return self.poll(task_id)

    def handle_callback(
        self,
        task_uuid: str,
        dlc_job_id: str,
        status: str,
        output_prefix: str,
        token: str,
    ) -> str:
        task = self._find_task(task_uuid)
        remote = self.remote_jobs[task.id]
        if remote.dlc_job_id != dlc_job_id:
            raise ValidationError("回调中的 dlc_job_id 不匹配")
        if remote.callback_token_hash != self._hash(token):
            raise CallbackAuthError("回调 token 无效")

        event_key = f"{dlc_job_id}:{status}"
        if event_key in remote.callback_events:
            task.history.append(f"{task.status}: 重复回调已忽略")
            return "ignored"
        remote.callback_events.add(event_key)

        remote.remote_status = self._normalize_status(status)
        if remote.remote_status == "SUCCEEDED":
            self._complete_from_artifacts(task, remote)
        elif remote.remote_status == "FAILED":
            task.error_message = "远程回调报告任务失败"
            task.mark("failed", task.error_message)
        elif remote.remote_status == "STOPPED":
            task.mark("cancelled", "远程回调报告任务已停止")
        else:
            task.history.append(f"{task.status}: 回调状态 {remote.remote_status}")
        return "processed"

    def _build_payload(
        self, task: TrainingTask, output_prefix: str, callback_token: str
    ) -> dict[str, Any]:
        return {
            "WorkspaceId": "mock-workspace",
            "ResourceId": "mock-resource",
            "DisplayName": f"chestx-train-{task.task_uuid}",
            "JobType": "PyTorchJob",
            "JobSpecs": [
                {
                    "Type": "Worker",
                    "Image": "registry/mock/chestx-train:0.1.0",
                    "PodCount": 1,
                    "EcsSpec": "ecs.mock.gpu",
                }
            ],
            "UserCommand": "python /workspace/train_yolo_remote.py",
            "Envs": {
                "TASK_ID": str(task.id),
                "TASK_UUID": task.task_uuid,
                "DATASET_PREFIX": task.dataset_prefix,
                "OUTPUT_PREFIX": output_prefix,
                "MODEL_NAME": task.model_name,
                "EPOCHS": str(task.epochs),
                "IMG_SIZE": str(task.img_size),
                "BATCH_SIZE": str(task.batch_size),
                "OPTIMIZER": task.optimizer,
                "LR0": str(task.lr0),
                "CALLBACK_URL": "https://example.test/api/training/remote/callbacks/dlc",
                "CALLBACK_TOKEN": callback_token,
            },
            "JobMaxRunningTimeMinutes": 720,
        }

    def _sync_metrics(self, task: TrainingTask, raw_metrics: list[dict[str, Any]]) -> None:
        existing_epochs = {m.epoch for m in self.metrics[task.id]}
        for item in raw_metrics:
            epoch = int(item["epoch"])
            if epoch in existing_epochs:
                continue
            self.metrics[task.id].append(
                TrainingMetric(
                    epoch=epoch,
                    box_loss=float(item["box_loss"]),
                    cls_loss=float(item["cls_loss"]),
                    dfl_loss=float(item["dfl_loss"]),
                    precision=float(item["precision"]),
                    recall=float(item["recall"]),
                    map50=float(item["map50"]),
                    map50_95=float(item["map50_95"]),
                    lr=float(item["lr"]),
                )
            )
            existing_epochs.add(epoch)
        if self.metrics[task.id]:
            latest = self.metrics[task.id][-1]
            task.current_epoch = latest.epoch
            task.progress = min(99, int((latest.epoch / task.epochs) * 100))

    def _complete_from_artifacts(
        self, task: TrainingTask, remote: RemoteTrainingJob
    ) -> None:
        try:
            success_key = remote.output_prefix + "_SUCCESS"
            results_key = remote.output_prefix + "results.csv"
            best_key = remote.output_prefix + "weights/best.pt"
            report_key = remote.output_prefix + "eval_report.json"
            for key in [success_key, results_key, best_key]:
                if not self.artifacts.exists(key):
                    raise ArtifactMissing(key)

            success = self.artifacts.get_json(success_key)
            if success.get("task_uuid") != task.task_uuid:
                raise ValidationError("_SUCCESS 中的 task_uuid 不匹配")

            self._sync_metrics(task, self._read_results_csv(results_key))
            report = self.artifacts.get_json(report_key) if self.artifacts.exists(report_key) else {}
            task.model_version = {
                "model_name": f"{task.model_name}_{task.task_uuid}",
                "oss_model_key": best_key,
                "artifact_manifest": {
                    "success_key": success_key,
                    "results_csv_key": results_key,
                    "best_weight_key": best_key,
                    "eval_report": report,
                },
            }
            task.current_epoch = task.epochs
            task.progress = 100
            task.mark("completed", "远程产物校验通过")
        except Exception as exc:
            task.error_message = f"产物校验失败：{exc}"
            task.mark("failed", task.error_message)
            remote.error_message = task.error_message

    def _read_results_csv(self, key: str) -> list[dict[str, Any]]:
        text = self.artifacts.get_text(key)
        return list(csv.DictReader(io.StringIO(text)))

    def _find_task(self, task_uuid: str) -> TrainingTask:
        for task in self.tasks.values():
            if task.task_uuid == task_uuid:
                return task
        raise KeyError(task_uuid)

    @staticmethod
    def _normalize_status(status: str) -> str:
        value = status.upper()
        mapping = {
            "PENDING": "QUEUED",
            "SUBMITTED": "SUBMITTED",
            "QUEUED": "QUEUED",
            "RUNNING": "RUNNING",
            "SUCCEEDED": "SUCCEEDED",
            "SUCCESS": "SUCCEEDED",
            "FAILED": "FAILED",
            "STOPPED": "STOPPED",
            "CANCELLED": "STOPPED",
        }
        return mapping.get(value, "UNKNOWN")

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_service(scenario: str = "success") -> tuple[MockDlcClient, RemoteTrainingService]:
    artifacts = MockArtifactStore()
    dlc = MockDlcClient(artifacts, scenario=scenario)
    return dlc, RemoteTrainingService(dlc, artifacts)


def poll_until_terminal(service: RemoteTrainingService, task_id: int, limit: int = 10) -> TrainingTask:
    task = service.tasks[task_id]
    for _ in range(limit):
        task = service.poll(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return task
    raise AssertionError(f"任务未进入终态：{task.history}")


def test_happy() -> TrainingTask:
    _, service = new_service("success")
    task, _ = service.start_remote_training()
    task = poll_until_terminal(service, task.id)
    assert task.status == "completed", task.history
    assert task.progress == 100
    assert len(service.metrics[task.id]) >= 2
    assert task.model_version is not None
    assert task.model_version["oss_model_key"].endswith("weights/best.pt")
    return task


def test_callback() -> TrainingTask:
    dlc, service = new_service("success")
    task, token = service.start_remote_training()
    remote = service.remote_jobs[task.id]
    assert remote.dlc_job_id is not None

    dlc.force_succeed(remote.dlc_job_id)
    first = service.handle_callback(
        task.task_uuid,
        remote.dlc_job_id,
        "SUCCEEDED",
        remote.output_prefix,
        token,
    )
    model_version = task.model_version
    second = service.handle_callback(
        task.task_uuid,
        remote.dlc_job_id,
        "SUCCEEDED",
        remote.output_prefix,
        token,
    )
    assert first == "processed"
    assert second == "ignored"
    assert task.status == "completed", task.history
    assert task.model_version == model_version
    return task


def test_stop() -> TrainingTask:
    _, service = new_service("success")
    task, _ = service.start_remote_training()
    service.poll(task.id)
    service.poll(task.id)
    service.poll(task.id)
    assert task.status == "running", task.history
    task = service.stop(task.id)
    assert task.status == "cancelled", task.history
    assert task.model_version is None
    task = service.poll(task.id)
    assert task.status == "cancelled", task.history
    return task


def test_failed() -> TrainingTask:
    _, service = new_service("failed")
    task, _ = service.start_remote_training()
    task = poll_until_terminal(service, task.id)
    assert task.status == "failed", task.history
    assert task.error_message
    assert task.model_version is None
    return task


def test_bad_callback() -> TrainingTask:
    dlc, service = new_service("success")
    task, _ = service.start_remote_training()
    remote = service.remote_jobs[task.id]
    assert remote.dlc_job_id is not None
    dlc.force_succeed(remote.dlc_job_id)
    try:
        service.handle_callback(
            task.task_uuid,
            remote.dlc_job_id,
            "SUCCEEDED",
            remote.output_prefix,
            "bad-token",
        )
    except CallbackAuthError:
        task.history.append("pending: 错误回调 token 已拒绝")
    else:
        raise AssertionError("错误回调 token 应该被拒绝")
    assert task.status == "pending"
    assert task.model_version is None
    return task


def test_missing_artifact() -> TrainingTask:
    _, service = new_service("missing-artifact")
    task, _ = service.start_remote_training()
    task = poll_until_terminal(service, task.id)
    assert task.status == "failed", task.history
    assert "best.pt" in (task.error_message or "")
    assert task.model_version is None
    return task


def test_dataset_not_ready() -> TrainingTask:
    _, service = new_service("success")
    try:
        service.start_remote_training(dataset_ready=False)
    except ValidationError as exc:
        task = TrainingTask(
            id=0,
            task_uuid="not-created",
            dataset_id="ds_not_ready",
            dataset_prefix="",
            status="rejected",
            model_name="yolo11n",
            epochs=0,
            img_size=0,
            batch_size=0,
            optimizer="",
            lr0=0,
        )
        task.history.append(f"rejected: {exc}")
        return task
    raise AssertionError("非 READY 数据集应该被拒绝")


CASES = {
    "happy": test_happy,
    "callback": test_callback,
    "stop": test_stop,
    "failed": test_failed,
    "bad-callback": test_bad_callback,
    "missing-artifact": test_missing_artifact,
    "dataset-not-ready": test_dataset_not_ready,
}


def run_case(name: str) -> None:
    task = CASES[name]()
    print(f"\n[通过] {name} -> {task.status}")
    for item in task.history:
        print(f"  - {item}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=["all", *CASES.keys()],
        default="all",
        help="要运行的模拟测试用例",
    )
    args = parser.parse_args()

    selected = CASES.keys() if args.case == "all" else [args.case]
    for name in selected:
        run_case(name)


if __name__ == "__main__":
    main()
