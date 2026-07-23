"""
训练相关 API 路由

接口列表：
  - POST   /api/training/start              启动训练任务
  - GET    /api/training/tasks               获取训练任务列表
  - GET    /api/training/status/{task_id}    获取训练状态（含最新指标）
  - GET    /api/training/metrics/{task_id}   获取训练指标历史
  - POST   /api/training/stop/{task_id}      停止训练任务
  - GET    /api/training/results/{task_uuid}  获取 results.csv
  - POST   /api/training/validate/{task_id}  模型评估（Day 7 新增）
  - POST   /api/training/export/{task_id}    模型导出（Day 7 新增）
  - GET    /api/training/download/{task_id}  下载模型权重（Day 7 新增）
  - POST   /api/training/predict             上传测试图验证（Day 7 新增）
"""

import base64
import os
import shutil
import tempfile

import cv2
import numpy as np
from app.api.auth import get_current_user
from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import get_db
from app.entity.schemas import (
    ModelExportRequest,
    ModelExportResponse,
    ModelValidateRequest,
    ModelValidateResponse,
    TrainingTaskCreate,
)
from app.training.training_service import training_service
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

logger = get_logger(__name__)

router = APIRouter(prefix="/api/training", tags=["模型训练"])


def _load_dataset_class_metadata(data_yaml: str) -> tuple[list[str], dict[str, str]]:
    """Read class metadata from a YOLO data.yaml file.

    Custom scenes created from uploaded datasets must satisfy the non-null
    detection_scenes.class_names constraint. If parsing fails, keep a minimal
    placeholder so training startup can still produce a valid scene row.
    """
    default_names = ["class_0"]
    try:
        import yaml

        with open(data_yaml, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        raw_names = config.get("names")
        if isinstance(raw_names, dict):
            items = sorted(raw_names.items(), key=lambda item: int(item[0]))
            class_names = [str(value) for _, value in items if str(value)]
        elif isinstance(raw_names, list):
            class_names = [str(value) for value in raw_names if str(value)]
        else:
            nc = int(config.get("nc") or 0)
            class_names = [f"class_{idx}" for idx in range(nc)]
        if not class_names:
            class_names = default_names
    except Exception as e:
        logger.warning("读取数据集类别失败: %s", str(e), exc_info=True)
        class_names = default_names

    class_names_cn = {
        name: f"类别{idx}" if name == f"class_{idx}" else name
        for idx, name in enumerate(class_names)
    }
    return class_names, class_names_cn


@router.post("/start")
async def start_training(
    request: TrainingTaskCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    config = {
        "model_name": request.model_name,
        "epochs": request.epochs,
        "img_size": request.img_size,
        "batch_size": request.batch_size,
        "device": request.device,
        "optimizer": request.optimizer,
        "lr0": request.lr0,
        "augment_config": request.augment_config,
    }

    from app.entity.db_models import DetectionScene

    # ── 解析场景：优先 scene_id，其次 scene_name ──
    api_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(api_dir)
    backend_dir = os.path.dirname(app_dir)

    scene = None
    scene_name = None

    if request.scene_id:
        scene = (
            db.query(DetectionScene)
            .filter(DetectionScene.id == request.scene_id)
            .first()
        )
        if scene:
            scene_name = scene.name

    if not scene and request.scene_name:
        scene_name = request.scene_name
        # 查找已有场景
        scene = (
            db.query(DetectionScene).filter(DetectionScene.name == scene_name).first()
        )
        # 如果不存在，自动创建
        if not scene:
            dataset_yaml = os.path.join(
                backend_dir, "datasets", scene_name, "yolo_dataset", "data.yaml"
            )
            if not os.path.exists(dataset_yaml):
                raise HTTPException(
                    status_code=400,
                    detail="数据集不存在或未完成初始化，请先上传数据集",
                )
            class_names, class_names_cn = _load_dataset_class_metadata(dataset_yaml)
            scene = DetectionScene(
                name=scene_name,
                display_name=scene_name,
                category="custom",
                class_names=class_names,
                class_names_cn=class_names_cn,
                is_active=True,
                created_by=current_user.id,
            )
            db.add(scene)
            db.commit()
            db.refresh(scene)

    if not scene:
        raise HTTPException(
            status_code=404, detail="检测场景不存在，请指定 scene_id 或 scene_name"
        )

    dataset_path = os.path.join(backend_dir, "datasets", scene.name, "yolo_dataset")
    config["dataset_path"] = dataset_path

    data_yaml = os.path.join(dataset_path, "data.yaml")
    if os.path.exists(data_yaml):
        config["data_yaml"] = data_yaml
    else:
        raise HTTPException(status_code=400, detail="数据集配置文件不存在")

    try:
        task = training_service.start_training(
            db=db,
            user_id=current_user.id,
            scene_id=scene.id,
            config=config,
        )
    except Exception as e:
        logger.error("启动训练失败：%s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="启动训练失败，请联系管理员查看后端日志")

    logger.info(
        "用户 %s 启动训练：scene=%s, model=%s, epochs=%d",
        current_user.username,
        scene.name,
        request.model_name,
        request.epochs,
    )

    return {
        "id": task.id,
        "task_uuid": task.task_uuid,
        "status": task.status,
        "model_name": task.model_name,
        "epochs": task.epochs,
        "message": "训练任务已创建，正在后台启动",
    }


@router.get("/tasks")
async def list_training_tasks(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    tasks = training_service.get_task_list(db, user_id=current_user.id)
    return {"total": len(tasks), "items": tasks}


@router.get("/status/{task_id}")
async def get_training_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    status = training_service.get_training_status(db, task_id)
    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])
    return status


@router.get("/metrics/{task_id}")
async def get_training_metrics(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    metrics = training_service.get_training_metrics(db, task_id)
    return {"task_id": task_id, "total": len(metrics), "metrics": metrics}


@router.post("/stop/{task_id}")
async def stop_training(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = training_service.stop_training(db, task_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/results/{task_uuid}")
async def get_results_csv(
    task_uuid: str,
    current_user=Depends(get_current_user),
):
    results_path = os.path.join(
        settings.TRAIN_OUTPUT_DIR,
        f"task_{task_uuid}",
        "results.csv",
    )
    if not os.path.exists(results_path):
        raise HTTPException(status_code=404, detail="results.csv 文件不存在")
    return FileResponse(
        path=results_path,
        media_type="text/csv",
        filename=f"training_results_{task_uuid}.csv",
    )


# ═══════════════════════════════════════════════════
# Day 7 新增接口
# ═══════════════════════════════════════════════════


@router.post("/validate/{task_id}", response_model=ModelValidateResponse)
async def validate_model(
    task_id: int,
    request: ModelValidateRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """对已完成训练的模型执行评估，返回 mAP/Precision/Recall/每类AP"""
    if request is None:
        request = ModelValidateRequest()
    result = training_service.validate_model(
        db=db,
        task_id=task_id,
        split=request.split,
        conf=request.conf,
        iou=request.iou,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 评估模型: task_id=%d, mAP50=%.4f",
        current_user.username,
        task_id,
        result.get("overall", {}).get("map50", 0),
    )
    return result


@router.post("/export/{task_id}", response_model=ModelExportResponse)
async def export_model(
    task_id: int,
    request: ModelExportRequest = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """导出训练好的模型为正式版本，复制权重+保存评估报告+创建版本记录"""
    if request is None:
        request = ModelExportRequest()
    result = training_service.export_model(
        db=db,
        task_id=task_id,
        version=request.version,
        description=request.description,
        set_default=request.set_default,
        upload_minio=request.upload_minio,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    logger.info(
        "用户 %s 导出模型: task_id=%d, version=%s",
        current_user.username,
        task_id,
        result.get("version"),
    )
    return result


@router.get("/download/{task_id}")
async def download_model(
    task_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """下载训练好的模型权重文件（best.pt）"""
    result = training_service.get_model_download_path(db, task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    logger.info("用户 %s 下载模型: task_id=%d", current_user.username, task_id)
    return FileResponse(
        path=result["file_path"],
        media_type="application/octet-stream",
        filename=result["filename"],
    )


# ═══════════════════════════════════════════════════
# 模型版本管理（全局默认模型切换）
# ═══════════════════════════════════════════════════


@router.get("/models")
async def list_model_versions(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取所有模型版本列表（供训练页切换全局默认模型）"""
    from app.entity.db_models import (
        DetectionScene,
        ModelVersion,
        TrainingMetric,
        TrainingTask,
    )

    scene = db.query(DetectionScene).filter(DetectionScene.name == "chest_xray").first()
    if not scene:
        return {"models": []}

    versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.scene_id == scene.id)
        .order_by(ModelVersion.created_at.desc())
        .all()
    )

    result = []
    for v in versions:
        map50 = v.map50
        map50_95 = v.map50_95
        precision = v.precision
        recall = v.recall

        # 如果 ModelVersion 指标为空，从 TrainingMetric 回退查询
        if map50 is None and v.training_task_id:
            last_metric = (
                db.query(TrainingMetric)
                .filter(TrainingMetric.task_id == v.training_task_id)
                .order_by(TrainingMetric.epoch.desc())
                .first()
            )
            if last_metric:
                map50 = last_metric.map50
                map50_95 = last_metric.map50_95
                precision = last_metric.precision
                recall = last_metric.recall

        result.append(
            {
                "id": v.id,
                "version": v.version,
                "model_name": v.model_name,
                "model_type": v.model_type,
                "map50": map50,
                "map50_95": map50_95,
                "precision": precision,
                "recall": recall,
                "is_default": v.is_default,
                "file_size": v.file_size,
                "created_at": str(v.created_at),
                "training_task_uuid": (
                    v.training_task.task_uuid if v.training_task else None
                ),
            }
        )

    return {"models": result}


@router.post("/models/{model_version_id}/set-default")
async def set_default_model(
    model_version_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """将指定模型版本设为全局默认（检测接口自动使用该模型）"""
    from app.entity.db_models import ModelVersion

    mv = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
    if not mv:
        raise HTTPException(status_code=404, detail="模型版本不存在")

    # 清除同场景其他模型的默认标记
    db.query(ModelVersion).filter(
        ModelVersion.scene_id == mv.scene_id,
        ModelVersion.id != model_version_id,
    ).update({"is_default": False})

    mv.is_default = True
    db.commit()

    # 同步更新 models/best.pt 并热重载
    try:
        default_path = os.path.join(os.getcwd(), "models", "best.pt")
        os.makedirs(os.path.dirname(default_path), exist_ok=True)
        shutil.copy2(mv.model_path, default_path)
        from app.services.detection_service import detection_service

        detection_service.reload_model()
        logger.info("全局默认模型已切换并热重载: %s (id=%d)", mv.model_name, mv.id)
    except Exception as e:
        logger.warning("模型热重载失败（不影响切换）: %s", str(e))

    logger.info(
        "用户 %s 设置默认模型: version_id=%d, model=%s",
        current_user.username,
        model_version_id,
        mv.model_name,
    )
    return {
        "message": f"模型 {mv.model_name} (v{mv.version}) 已设为全局默认",
        "model_version_id": mv.id,
    }


@router.post("/predict")
async def predict_test_image(
    file: UploadFile = File(..., description="测试图片"),
    task_id: int = Form(..., description="训练任务 ID"),
    conf: float = Form(0.25, description="置信度阈值"),
    iou: float = Form(0.45, description="NMS IoU 阈值"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """上传测试图片，使用训练好的模型进行预测，返回标注图+检测结果"""
    from app.entity.db_models import TrainingTask
    from ultralytics import YOLO

    # 验证文件类型
    allowed_types = {"image/jpeg", "image/png", "image/bmp", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file.content_type}",
        )

    task = db.query(TrainingTask).filter(TrainingTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="训练任务未完成")

    weights_path = training_service._resolve_weights_path(db, task_id)
    if not weights_path:
        raise HTTPException(
            status_code=404, detail="模型权重文件不存在，请确认训练已完成或模型已注册"
        )

    # 保存上传文件
    suffix = os.path.splitext(file.filename)[1] or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        model = YOLO(weights_path)
        results = model.predict(
            source=tmp_path,
            conf=conf,
            iou=iou,
            imgsz=task.img_size,
            device="cpu",
            save=False,
            verbose=False,
        )

        result = results[0]
        detections = []
        if result.boxes is not None and len(result.boxes) > 0:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                cls_name = model.names.get(cls_id, f"class_{cls_id}")
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(
                    {
                        "class_name": cls_name,
                        "class_id": cls_id,
                        "confidence": round(confidence, 4),
                        "bbox": [
                            round(x1, 1),
                            round(y1, 1),
                            round(x2, 1),
                            round(y2, 1),
                        ],
                    }
                )

        annotated_img = result.plot()
        _, buffer = cv2.imencode(".jpg", annotated_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        annotated_base64 = base64.b64encode(buffer).decode("utf-8")

        class_counts = {}
        for det in detections:
            name = det["class_name"]
            class_counts[name] = class_counts.get(name, 0) + 1

        return {
            "task_id": task_id,
            "task_uuid": task.task_uuid,
            "filename": file.filename,
            "total_objects": len(detections),
            "detections": detections,
            "class_counts": class_counts,
            "annotated_image": annotated_base64,
            "inference_time": round(float(result.speed.get("inference", 0)), 2),
        }
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
