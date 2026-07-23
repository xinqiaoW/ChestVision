"""
模型训练服务

职责：
  - 封装 YOLOv11 训练启动、监控、停止逻辑
  - 支持本地 CPU 训练和 GPU 训练
  - 训练在后台线程中执行，不阻塞 API 请求
  - 实时解析训练指标并写入数据库
  - 解析 Ultralytics 生成的 results.csv 获取训练日志
"""

import csv
import json
import os
import shutil
import threading
import uuid
from datetime import datetime

from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import TrainingMetric, TrainingTask

logger = get_logger(__name__)

_running_tasks: dict = {}
_running_lock = threading.Lock()


class TrainingService:
    """模型训练服务 — 封装 YOLOv11 训练全流程"""

    @staticmethod
    def start_training(db, user_id: int, scene_id: int, config: dict) -> TrainingTask:
        task_uuid = str(uuid.uuid4())[:8]

        data_yaml = config.get("data_yaml")
        dataset_path = config.get("dataset_path", "")
        if not data_yaml and dataset_path:
            yaml_candidate = os.path.join(dataset_path, "data.yaml")
            if os.path.exists(yaml_candidate):
                data_yaml = yaml_candidate

        task = TrainingTask(
            user_id=user_id,
            scene_id=scene_id,
            task_uuid=task_uuid,
            status="pending",
            model_name=config.get("model_name", "yolo11n"),
            epochs=config.get("epochs", 50),
            img_size=config.get("img_size", 640),
            batch_size=config.get("batch_size", 8),
            device=config.get("device", "cpu"),
            optimizer=config.get("optimizer", "SGD"),
            lr0=config.get("lr0", 0.01),
            augment_config=config.get("augment_config"),
            dataset_path=dataset_path,
            data_yaml=data_yaml,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        thread = threading.Thread(
            target=TrainingService._run_training,
            args=(task.id, task.task_uuid, config),
            daemon=True,
            name=f"train-{task_uuid}",
        )
        thread.start()
        logger.info("训练任务已启动：task_id=%d, uuid=%s", task.id, task_uuid)
        return task

    @staticmethod
    def _run_training(task_id: int, task_uuid: str, config: dict):
        db = SessionLocal()
        original_content = ""
        data_yaml = ""
        original_cwd = os.getcwd()
        try:
            task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
            if not task:
                return
            task.status = "running"
            task.started_at = datetime.now()
            db.commit()

            from ultralytics import YOLO

            model_name = config.get("model_name", "yolo11n")
            model = YOLO(model_name)

            with _running_lock:
                _running_tasks[task_uuid] = model

            data_yaml = config.get("data_yaml", "")
            if not data_yaml:
                data_yaml = os.path.join(config.get("dataset_path", ""), "data.yaml")
            if not os.path.exists(data_yaml):
                raise FileNotFoundError(f"data.yaml 不存在：{data_yaml}")

            data_yaml_dir = os.path.dirname(data_yaml)
            with open(data_yaml, "r", encoding="utf-8") as f:
                original_content = f.read()

            modified_content = original_content.replace(
                "path: .", f"path: {data_yaml_dir}"
            )
            with open(data_yaml, "w", encoding="utf-8") as f:
                f.write(modified_content)

            train_kwargs = {
                "data": data_yaml,
                "epochs": config.get("epochs", 50),
                "imgsz": config.get("img_size", 640),
                "batch": config.get("batch_size", 8),
                "device": config.get("device", "cpu"),
                "optimizer": config.get("optimizer", "SGD"),
                "lr0": config.get("lr0", 0.01),
                "project": os.path.join(original_cwd, settings.TRAIN_OUTPUT_DIR),
                "name": f"task_{task_uuid}",
                "exist_ok": True,
                "verbose": True,
                "save": True,
                "plots": False,
            }

            def on_train_epoch_end(trainer):
                try:
                    epoch = trainer.epoch + 1
                    m = trainer.metrics or {}
                    d = m if isinstance(m, dict) else {}
                    metric = TrainingMetric(
                        task_id=task_id,
                        epoch=epoch,
                        box_loss=float(d.get("metrics/box_loss", 0)),
                        cls_loss=float(d.get("metrics/cls_loss", 0)),
                        dfl_loss=float(d.get("metrics/dfl_loss", 0)),
                        precision=float(d.get("metrics/precision(B)", 0)),
                        recall=float(d.get("metrics/recall(B)", 0)),
                        map50=float(d.get("metrics/mAP50(B)", 0)),
                        map50_95=float(d.get("metrics/mAP50-95(B)", 0)),
                    )
                    db.add(metric)
                    total = config.get("epochs", 50)
                    task.current_epoch = epoch
                    task.progress = int((epoch / total) * 100)
                    db.commit()
                except Exception as e:
                    logger.warning("回调异常：%s", str(e))
                    db.rollback()

            model.add_callback("on_train_epoch_end", on_train_epoch_end)
            logger.info(
                "开始训练：data=%s, epochs=%d", data_yaml, train_kwargs["epochs"]
            )
            model.train(**train_kwargs)

            task.status = "completed"
            task.progress = 100
            task.current_epoch = config.get("epochs", 50)
            task.completed_at = datetime.now()
            db.commit()

            project_path = os.path.join(original_cwd, settings.TRAIN_OUTPUT_DIR)
            TrainingService._parse_final_results(
                db, task_id, task_uuid, config, project_path
            )
            logger.info("训练完成：task_id=%d", task_id)

        except FileNotFoundError as e:
            logger.error("训练文件缺失：%s", str(e), exc_info=True)
            task.status = "failed"
            task.error_message = "训练所需文件不存在，请检查数据集和模型配置"
            db.commit()
        except Exception as e:
            logger.error("训练异常：%s", str(e), exc_info=True)
            task.status = "failed"
            task.error_message = "训练失败，请联系管理员查看后端日志"
            db.commit()
        finally:
            try:
                if original_content and data_yaml:
                    with open(data_yaml, "w", encoding="utf-8") as f:
                        f.write(original_content)
            except Exception:
                pass
            try:
                os.chdir(original_cwd)
            except Exception:
                pass
            with _running_lock:
                _running_tasks.pop(task_uuid, None)
            db.close()

    @staticmethod
    def _parse_final_results(
        db, task_id: int, task_uuid: str, config: dict, project_path: str = None
    ):
        if project_path is None:
            project_path = settings.TRAIN_OUTPUT_DIR
        results_csv = os.path.join(project_path, f"task_{task_uuid}", "results.csv")
        if not os.path.exists(results_csv):
            return
        try:
            existing_epochs = {
                m.epoch
                for m in db.query(TrainingMetric)
                .filter(TrainingMetric.task_id == task_id)
                .all()
            }
            with open(results_csv, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
                for row in rows:
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    epoch = int(row.get("epoch", 0)) + 1
                    if epoch in existing_epochs:
                        continue
                    db.add(
                        TrainingMetric(
                            task_id=task_id,
                            epoch=epoch,
                            box_loss=_safe_float(row.get("train/box_loss", "")),
                            cls_loss=_safe_float(row.get("train/cls_loss", "")),
                            dfl_loss=_safe_float(row.get("train/dfl_loss", "")),
                            precision=_safe_float(row.get("metrics/precision(B)", "")),
                            recall=_safe_float(row.get("metrics/recall(B)", "")),
                            map50=_safe_float(row.get("metrics/mAP50(B)", "")),
                            map50_95=_safe_float(row.get("metrics/mAP50-95(B)", "")),
                            lr=_safe_float(row.get("lr/pg0", "")),
                        )
                    )
            db.commit()

            # 同步更新 ModelVersion 指标（供模型版本列表展示 mAP 等）
            try:
                from app.entity.db_models import ModelVersion

                mv = (
                    db.query(ModelVersion)
                    .filter(ModelVersion.training_task_id == task_id)
                    .first()
                )
                if mv and rows:
                    last_row = {k.strip(): v.strip() for k, v in rows[-1].items()}
                    mv.map50 = _safe_float(last_row.get("metrics/mAP50(B)", ""))
                    mv.map50_95 = _safe_float(last_row.get("metrics/mAP50-95(B)", ""))
                    mv.precision = _safe_float(last_row.get("metrics/precision(B)", ""))
                    mv.recall = _safe_float(last_row.get("metrics/recall(B)", ""))
                    db.commit()
            except Exception as e:
                logger.warning("更新ModelVersion指标失败: %s", str(e))
        except Exception as e:
            logger.warning("CSV解析异常：%s", str(e))
            db.rollback()

    @staticmethod
    def get_training_status(db, task_id: int) -> dict:
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        latest = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.desc())
            .first()
        )
        with _running_lock:
            is_running = task.task_uuid in _running_tasks
        return {
            "task": {
                "id": task.id,
                "task_uuid": task.task_uuid,
                "status": task.status,
                "model_name": task.model_name,
                "epochs": task.epochs,
                "current_epoch": task.current_epoch,
                "progress": task.progress,
                "device": task.device,
                "batch_size": task.batch_size,
                "img_size": task.img_size,
                "started_at": str(task.started_at) if task.started_at else None,
                "completed_at": str(task.completed_at) if task.completed_at else None,
                "error_message": task.error_message,
            },
            "latest_metric": {
                "epoch": latest.epoch,
                "box_loss": latest.box_loss,
                "cls_loss": latest.cls_loss,
                "dfl_loss": latest.dfl_loss,
                "precision": latest.precision,
                "recall": latest.recall,
                "map50": latest.map50,
                "map50_95": latest.map50_95,
                "lr": latest.lr,
            }
            if latest
            else None,
            "is_running": is_running,
        }

    @staticmethod
    def get_training_metrics(db, task_id: int) -> list:
        metrics = (
            db.query(TrainingMetric)
            .filter(TrainingMetric.task_id == task_id)
            .order_by(TrainingMetric.epoch.asc())
            .all()
        )
        return [
            {
                "epoch": m.epoch,
                "box_loss": m.box_loss,
                "cls_loss": m.cls_loss,
                "dfl_loss": m.dfl_loss,
                "precision": m.precision,
                "recall": m.recall,
                "map50": m.map50,
                "map50_95": m.map50_95,
                "lr": m.lr,
            }
            for m in metrics
        ]

    @staticmethod
    def stop_training(db, task_id: int) -> dict:
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "running":
            return {"error": f"任务状态为 {task.status}，无法停止"}
        with _running_lock:
            model = _running_tasks.get(task.task_uuid)
            if model:
                try:
                    model.trainer.stop()
                except Exception as e:
                    logger.warning("停止训练异常：%s", str(e))
        task.status = "cancelled"
        task.completed_at = datetime.now()
        db.commit()
        return {"message": "训练任务已停止", "task_id": task_id}

    @staticmethod
    def get_task_list(db, user_id: int = None, limit: int = 20) -> list:
        query = db.query(TrainingTask)
        if user_id:
            query = query.filter(TrainingTask.user_id == user_id)
        tasks = query.order_by(TrainingTask.created_at.desc()).limit(limit).all()
        return [
            {
                "id": t.id,
                "task_uuid": t.task_uuid,
                "status": t.status,
                "model_name": t.model_name,
                "epochs": t.epochs,
                "current_epoch": t.current_epoch,
                "progress": t.progress,
                "device": t.device,
                "created_at": str(t.created_at),
                "started_at": str(t.started_at) if t.started_at else None,
                "completed_at": str(t.completed_at) if t.completed_at else None,
            }
            for t in tasks
        ]

    @staticmethod
    def parse_results_csv(results_csv_path: str) -> list:
        metrics = []
        if not os.path.exists(results_csv_path):
            return metrics
        with open(results_csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row = {k.strip(): v.strip() for k, v in row.items()}
                metrics.append(
                    {
                        "epoch": int(row.get("epoch", 0)) + 1,
                        "box_loss": _safe_float(row.get("train/box_loss", "")),
                        "cls_loss": _safe_float(row.get("train/cls_loss", "")),
                        "dfl_loss": _safe_float(row.get("train/dfl_loss", "")),
                        "precision": _safe_float(row.get("metrics/precision(B)", "")),
                        "recall": _safe_float(row.get("metrics/recall(B)", "")),
                        "map50": _safe_float(row.get("metrics/mAP50(B)", "")),
                        "map50_95": _safe_float(row.get("metrics/mAP50-95(B)", "")),
                        "lr": _safe_float(row.get("lr/pg0", "")),
                    }
                )
        return metrics

    @staticmethod
    def _resolve_weights_path(db, task_id: int) -> str:
        """解析模型权重文件路径（训练输出 → ModelVersion 记录 → 报错）"""
        from app.entity.db_models import ModelVersion

        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return None

        # 1) 优先尝试训练输出路径
        weights_path = os.path.join(
            os.getcwd(),
            settings.TRAIN_OUTPUT_DIR,
            f"task_{task.task_uuid}",
            "weights",
            "best.pt",
        )
        if os.path.exists(weights_path):
            return weights_path

        # 2) 回退到 ModelVersion 记录中的路径（预注册模型等场景）
        mv = (
            db.query(ModelVersion)
            .filter(ModelVersion.training_task_id == task_id)
            .first()
        )
        if mv and mv.model_path and os.path.exists(mv.model_path):
            return mv.model_path

        return None

    @staticmethod
    def validate_model(
        db, task_id: int, split: str = "val", conf: float = 0.001, iou: float = 0.6
    ) -> dict:
        """对已完成训练的模型执行验证集评估"""
        from app.entity.db_models import DetectionScene, ModelVersion
        from ultralytics import YOLO

        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "completed":
            return {"error": f"训练任务状态为 {task.status}，只有已完成的任务才能评估"}

        original_cwd = os.getcwd()
        weights_path = TrainingService._resolve_weights_path(db, task_id)
        if not weights_path:
            logger.warning("模型评估失败，权重文件不存在: task_id=%d", task_id)
            return {"error": "模型权重文件不存在，请确认训练已完成或模型已注册"}

        data_yaml = task.data_yaml
        if not data_yaml or not os.path.exists(data_yaml):
            if task.dataset_path:
                # 尝试多个可能的 data.yaml 位置
                candidates = [
                    os.path.join(task.dataset_path, "data.yaml"),
                    os.path.join(task.dataset_path, "yolo_dataset", "data.yaml"),
                ]
                for candidate in candidates:
                    if os.path.exists(candidate):
                        data_yaml = candidate
                        break
            if not data_yaml or not os.path.exists(data_yaml):
                return {"error": "data.yaml 不存在，无法进行评估"}

        # 临时修改 data.yaml 的 path 为绝对路径
        data_yaml_dir = os.path.dirname(os.path.abspath(data_yaml))
        with open(data_yaml, "r", encoding="utf-8") as f:
            original_content = f.read()
        with open(data_yaml, "w", encoding="utf-8") as f:
            f.write(original_content.replace("path: .", f"path: {data_yaml_dir}"))

        try:
            model = YOLO(weights_path)
            results = model.val(
                data=data_yaml,
                split=split,
                conf=conf,
                iou=iou,
                imgsz=task.img_size,
                device="cpu",
                save_json=True,
                plots=True,
                project=os.path.join(original_cwd, settings.TRAIN_OUTPUT_DIR),
                name=f"task_{task.task_uuid}",
                exist_ok=True,
                verbose=False,
            )

            overall = {
                "precision": float(results.box.mp),
                "recall": float(results.box.mr),
                "map50": float(results.box.map50),
                "map50_95": float(results.box.map),
            }

            per_class = {}
            if results.box.ap is not None:
                for i, ap50 in enumerate(results.box.ap50):
                    class_name = model.names.get(i, f"class_{i}")
                    ap50_95 = results.box.ap[i] if i < len(results.box.ap) else 0.0
                    per_class[class_name] = {
                        "ap50": round(float(ap50), 4),
                        "ap50_95": round(float(ap50_95), 4),
                    }

            report = {
                "task_id": task_id,
                "task_uuid": task.task_uuid,
                "split": split,
                "overall": overall,
                "per_class": per_class,
            }

            # 创建/更新 ModelVersion 记录
            scene = (
                db.query(DetectionScene)
                .filter(DetectionScene.id == task.scene_id)
                .first()
            )
            model_version = (
                db.query(ModelVersion)
                .filter(ModelVersion.training_task_id == task_id)
                .first()
            )

            if not model_version:
                existing_count = (
                    db.query(ModelVersion)
                    .filter(ModelVersion.scene_id == task.scene_id)
                    .count()
                )
                version = f"v{existing_count + 1}.0.0"
                model_version = ModelVersion(
                    scene_id=task.scene_id,
                    training_task_id=task_id,
                    version=version,
                    model_name=f"{task.model_name}_{scene.name}_{version}"
                    if scene
                    else task.model_name,
                    model_type=task.model_name,
                    model_path=weights_path,
                    map50=overall["map50"],
                    map50_95=overall["map50_95"],
                    precision=overall["precision"],
                    recall=overall["recall"],
                    per_class_ap=per_class,
                    file_size=os.path.getsize(weights_path),
                    description=f"训练任务 {task.task_uuid} 自动评估产出",
                )
                db.add(model_version)
            else:
                model_version.map50 = overall["map50"]
                model_version.map50_95 = overall["map50_95"]
                model_version.precision = overall["precision"]
                model_version.recall = overall["recall"]
                model_version.per_class_ap = per_class

            db.commit()
            report["model_version_id"] = model_version.id
            report["model_version"] = model_version.version

            logger.info(
                "模型评估完成: task_id=%d, mAP50=%.4f", task_id, overall["map50"]
            )
            return report

        except Exception as e:
            db.rollback()
            logger.error("模型评估异常: task_id=%d, error=%s", task_id, str(e), exc_info=True)
            return {"error": "评估失败，请联系管理员查看后端日志"}
        finally:
            try:
                with open(data_yaml, "w", encoding="utf-8") as f:
                    f.write(original_content)
            except Exception as e:
                logger.warning("恢复 data.yaml 失败: %s", str(e), exc_info=True)

    @staticmethod
    def export_model(
        db,
        task_id: int,
        version: str = None,
        description: str = None,
        set_default: bool = False,
        upload_minio: bool = True,
    ) -> dict:
        """导出训练好的模型为正式版本"""
        from app.entity.db_models import DetectionScene, ModelVersion

        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "completed":
            return {"error": f"训练任务状态为 {task.status}，只有已完成的任务才能导出"}

        original_cwd = os.getcwd()
        weights_path = TrainingService._resolve_weights_path(db, task_id)
        if not weights_path:
            logger.warning("模型导出失败，权重文件不存在: task_id=%d", task_id)
            return {"error": "模型权重文件不存在，请确认训练已完成或模型已注册"}

        scene = (
            db.query(DetectionScene).filter(DetectionScene.id == task.scene_id).first()
        )
        if not scene:
            return {"error": "关联场景不存在"}

        if not version:
            existing_count = (
                db.query(ModelVersion)
                .filter(ModelVersion.scene_id == task.scene_id)
                .count()
            )
            version = f"v{existing_count + 1}.0.0"

        export_dir = os.path.join(original_cwd, "models", f"{scene.name}_{version}")
        os.makedirs(export_dir, exist_ok=True)

        exported_weight = os.path.join(export_dir, "best.pt")
        shutil.copy2(weights_path, exported_weight)
        logger.info("模型文件已复制: %s → %s", weights_path, exported_weight)

        # 复制评估图表
        task_output_dir = os.path.join(
            original_cwd, settings.TRAIN_OUTPUT_DIR, f"task_{task.task_uuid}"
        )
        for plot_name in [
            "confusion_matrix.png",
            "PR_curve.png",
            "F1_curve.png",
            "results.png",
        ]:
            src = os.path.join(task_output_dir, plot_name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(export_dir, plot_name))

        # 从 results.csv 读取最终指标
        csv_path = os.path.join(task_output_dir, "results.csv")
        overall = {}
        per_class = {}
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                if rows:
                    last_row = {k.strip(): v.strip() for k, v in rows[-1].items()}
                    overall = {
                        "precision": _safe_float(
                            last_row.get("metrics/precision(B)", "")
                        ),
                        "recall": _safe_float(last_row.get("metrics/recall(B)", "")),
                        "map50": _safe_float(last_row.get("metrics/mAP50(B)", "")),
                        "map50_95": _safe_float(
                            last_row.get("metrics/mAP50-95(B)", "")
                        ),
                    }
            except Exception as e:
                logger.warning("读取 results.csv 失败: %s", e)

        # 从已有 ModelVersion 获取每类指标
        existing_version = (
            db.query(ModelVersion)
            .filter(ModelVersion.training_task_id == task_id)
            .first()
        )
        if existing_version and existing_version.per_class_ap:
            per_class = existing_version.per_class_ap

        # 保存评估报告 JSON
        report = {
            "version": version,
            "model_name": task.model_name,
            "scene": scene.name,
            "training_task": task.task_uuid,
            "evaluation": {"split": "val", "overall": overall, "per_class": per_class},
            "training_config": {
                "epochs": task.epochs,
                "batch_size": task.batch_size,
                "img_size": task.img_size,
                "optimizer": task.optimizer,
                "lr0": task.lr0,
                "device": task.device,
            },
            "exported_at": datetime.now().isoformat(),
        }
        with open(
            os.path.join(export_dir, "eval_report.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # 上传 MinIO
        minio_url = None
        if upload_minio:
            try:
                from app.storage.minio_client import MinIOClient

                minio_client = MinIOClient()
                object_name = f"models/{scene.name}/{version}/best.pt"
                minio_url = minio_client.upload_file(object_name, exported_weight)
                logger.info("模型已上传 MinIO: %s", minio_url)
            except Exception as e:
                logger.warning("MinIO 上传失败（不影响导出）: %s", str(e))

        # 创建/更新 ModelVersion
        model_version = existing_version
        if model_version:
            model_version.version = version
            model_version.model_path = exported_weight
            model_version.minio_url = minio_url
            model_version.map50 = overall.get("map50")
            model_version.map50_95 = overall.get("map50_95")
            model_version.precision = overall.get("precision")
            model_version.recall = overall.get("recall")
            model_version.per_class_ap = per_class
            model_version.file_size = os.path.getsize(exported_weight)
            model_version.description = description or f"训练任务 {task.task_uuid} 导出"
        else:
            model_version = ModelVersion(
                scene_id=task.scene_id,
                training_task_id=task_id,
                version=version,
                model_name=f"{task.model_name}_{scene.name}_{version}",
                model_type=task.model_name,
                model_path=exported_weight,
                minio_url=minio_url,
                map50=overall.get("map50"),
                map50_95=overall.get("map50_95"),
                precision=overall.get("precision"),
                recall=overall.get("recall"),
                per_class_ap=per_class,
                file_size=os.path.getsize(exported_weight),
                description=description or f"训练任务 {task.task_uuid} 导出",
            )
            db.add(model_version)

        if set_default:
            db.query(ModelVersion).filter(
                ModelVersion.scene_id == task.scene_id,
                ModelVersion.id != model_version.id,
            ).update({"is_default": False})
            model_version.is_default = True

            # 将默认模型权重同步到 models/best.pt，检测服务自动使用
            default_model_path = os.path.join(original_cwd, "models", "best.pt")
            os.makedirs(os.path.dirname(default_model_path), exist_ok=True)
            shutil.copy2(exported_weight, default_model_path)
            logger.info("默认模型已更新: %s", default_model_path)

        db.commit()
        db.refresh(model_version)

        # 设为默认时，热重载检测服务中的模型
        if set_default:
            try:
                from app.services.detection_service import detection_service

                detection_service.reload_model()
                logger.info("检测服务模型已热重载为新导出模型")
            except Exception as e:
                logger.warning("检测服务模型重载失败（不影响导出）: %s", str(e))

        logger.info(
            "模型导出完成: scene=%s, version=%s, mAP50=%.4f",
            scene.name,
            version,
            overall.get("map50", 0),
        )

        return {
            "model_version_id": model_version.id,
            "version": version,
            "model_name": model_version.model_name,
            "model_path": exported_weight,
            "export_dir": export_dir,
            "minio_url": minio_url,
            "file_size": model_version.file_size,
            "evaluation": {
                "map50": overall.get("map50"),
                "map50_95": overall.get("map50_95"),
                "precision": overall.get("precision"),
                "recall": overall.get("recall"),
                "per_class": per_class,
            },
            "is_default": model_version.is_default,
            "message": f"模型已导出为版本 {version}",
        }

    @staticmethod
    def get_model_download_path(db, task_id: int) -> dict:
        """获取训练任务模型权重文件的下载路径"""
        task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
        if not task:
            return {"error": "训练任务不存在"}
        if task.status != "completed":
            return {"error": "训练任务未完成"}

        original_cwd = os.getcwd()
        weights_path = TrainingService._resolve_weights_path(db, task_id)
        if not weights_path:
            return {"error": "模型权重文件不存在，请确认训练已完成或模型已注册"}

        return {
            "file_path": weights_path,
            "filename": f"{task.model_name}_{task.task_uuid}_best.pt",
        }


def _safe_float(value) -> float:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


training_service = TrainingService()
