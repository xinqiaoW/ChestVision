"""
检测推理 API 路由

接口列表：
  - POST /api/detection/detect   单张胸片检测（含 DB 记录）
  - POST /api/detection/single   快捷单图检测（跳过 DB）
  - POST /api/detection/batch    快捷批量检测
  - POST /api/detection/zip      快捷 ZIP 检测
  - GET  /api/detection/image/{id} 标注结果图
  - GET  /api/detection/tasks    检测历史列表
  - GET  /api/detection/task/{id} 检测任务详情
"""

import base64
import os
import tempfile
import uuid

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.database.session import get_db
from app.entity.db_models import (
    DetectionResult,
    DetectionScene,
    DetectionTask,
    DoctorPatientRelation,
    ModelVersion,
    PatientProfile,
    User,
)
from app.services.detection_service import detection_service
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

logger = get_logger(__name__)

router = APIRouter(prefix="/api/detection", tags=["病灶检测"])


@router.post("/detect")
async def detect(
    file: UploadFile = File(..., description="胸片图像文件（支持 jpg/png/dicom 等）"),
    conf_threshold: float = Query(0.25, ge=0.01, le=1.0, description="置信度阈值"),
    iou_threshold: float = Query(0.45, ge=0.01, le=1.0, description="NMS IoU 阈值"),
    model_version_id: int = Query(None, description="指定模型版本ID，不传则自动选择"),
    patient_profile_id: int = Query(
        None, description="关联患者档案ID，用于病史分析与AI医生匹配"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    上传一张胸片图像，执行 AI 病灶检测

    返回检测到的病灶列表（类别、置信度、边界框），
    以及标注好的检测结果图片。
    """
    # ── 校验文件类型 ──
    allowed_exts = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".dcm"}
    ext = os.path.splitext(file.filename or "unknown.jpg")[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}，支持: {', '.join(allowed_exts)}",
        )

    # ── 保存上传文件到临时目录 ──
    tmp_dir = tempfile.mkdtemp(prefix="chestx_detect_")
    tmp_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex[:8]}{ext}")

    try:
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        if len(content) > 50 * 1024 * 1024:  # 50MB 限制
            raise HTTPException(status_code=400, detail="文件大小不能超过 50MB")

        with open(tmp_path, "wb") as f:
            f.write(content)

        # ── 获取场景和模型 ──
        scene = (
            db.query(DetectionScene).filter(DetectionScene.name == "chest_xray").first()
        )
        if not scene:
            raise HTTPException(
                status_code=500, detail="检测场景 'chest_xray' 不存在，请先初始化数据库"
            )

        # 选择模型版本：指定 > 默认 > 最新
        model_version = None
        model_path = None
        if model_version_id:
            model_version = (
                db.query(ModelVersion)
                .filter(
                    ModelVersion.id == model_version_id,
                    ModelVersion.scene_id == scene.id,
                )
                .first()
            )
            if not model_version:
                raise HTTPException(
                    status_code=404, detail=f"模型版本不存在: id={model_version_id}"
                )
            model_path = model_version.model_path
        else:
            # 优先默认模型，其次最新
            model_version = (
                db.query(ModelVersion)
                .filter(
                    ModelVersion.scene_id == scene.id,
                    ModelVersion.is_default == True,  # noqa: E712
                )
                .first()
            )
            if not model_version:
                model_version = (
                    db.query(ModelVersion)
                    .filter(ModelVersion.scene_id == scene.id)
                    .order_by(ModelVersion.created_at.desc())
                    .first()
                )
            if model_version:
                model_path = model_version.model_path

        # ── 执行检测 ──
        try:
            result = detection_service.predict(
                image_path=tmp_path,
                conf_threshold=conf_threshold,
                iou_threshold=iou_threshold,
                model_path=model_path,
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=500, detail=f"模型加载失败: {str(e)}")
        except Exception as e:
            logger.error("检测推理失败: %s", str(e), exc_info=True)
            raise HTTPException(status_code=500, detail=f"检测推理失败: {str(e)}")

        # ── 关联患者档案（v3.0）──
        linked_patient_profile_id = None
        if patient_profile_id:
            profile = (
                db.query(PatientProfile)
                .filter(PatientProfile.id == patient_profile_id)
                .first()
            )
            if profile is None:
                raise HTTPException(status_code=404, detail="患者档案不存在")
            is_allowed = current_user.user_type == "admin" or profile.user_id == current_user.id
            if current_user.user_type == "doctor":
                is_allowed = (
                    db.query(DoctorPatientRelation)
                    .filter(
                        DoctorPatientRelation.doctor_id == current_user.id,
                        DoctorPatientRelation.patient_id == profile.user_id,
                        DoctorPatientRelation.relation_status == "active",
                    )
                    .first()
                    is not None
                )
            if not is_allowed:
                raise HTTPException(status_code=403, detail="无权关联该患者档案")
            linked_patient_profile_id = profile.id
        elif current_user.user_type == "patient":
            profile = (
                db.query(PatientProfile)
                .filter(PatientProfile.user_id == current_user.id)
                .first()
            )
            if profile:
                linked_patient_profile_id = profile.id  # type: ignore[arg-type]

        # ── 保存检测记录到数据库 ──
        task = detection_service.save_detection_task(
            db=db,
            user_id=current_user.id,  # type: ignore[arg-type]
            scene_id=scene.id,  # type: ignore[arg-type]
            model_version_id=model_version.id if model_version else None,  # type: ignore[arg-type]
            image_path=file.filename or "",
            predict_result=result,
            patient_profile_id=linked_patient_profile_id,
        )

        # ── LLM 病史感知分析（v3.0 P1）──
        analysis = None
        if linked_patient_profile_id:
            try:
                analysis = detection_service.analyze_with_history(
                    db=db,
                    patient_profile_id=linked_patient_profile_id,
                    current_result={
                        "total_objects": result["total_objects"],
                        "class_counts": {
                            obj["class_name_cn"]: sum(
                                1
                                for o in result["objects"]
                                if o["class_name_cn"] == obj["class_name_cn"]
                            )
                            for obj in result["objects"]
                        },
                        "detections": result["objects"],
                    },
                )
                if analysis.get("analysis_report"):
                    task.analysis_report = analysis["analysis_report"]
                    task.risk_level = analysis["risk_level"]
                    task.referenced_record_ids = analysis["referenced_record_ids"]
                    db.commit()
                    logger.info(
                        "病史感知分析已保存: task_id=%d, risk=%s",
                        task.id,
                        analysis["risk_level"],
                    )
            except Exception as e:
                logger.error("病史分析异常: %s", str(e))

        # ── 读取标注图 base64 ──
        annotated_base64 = ""
        if result.get("annotated_image_path") and os.path.exists(
            result["annotated_image_path"]
        ):
            with open(result["annotated_image_path"], "rb") as f:
                annotated_base64 = base64.b64encode(f.read()).decode("utf-8")

        # ── 返回结果 ──
        return {
            "task_id": task.id,
            "status": "completed",
            "total_objects": result["total_objects"],
            "inference_time_ms": result["inference_time"],
            "image_size": f"{result['image_width']}×{result['image_height']}",
            "objects": result["objects"],
            "annotated_image_url": f"/api/detection/image/{task.id}",
            "annotated_image_base64": annotated_base64,
            "ai_analysis": {
                "report": analysis.get("analysis_report") if analysis else None,
                "risk_level": analysis.get("risk_level") if analysis else None,
            }
            if analysis
            else None,
        }

    finally:
        # 清理临时文件
        try:
            os.remove(tmp_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass


@router.get("/image/{task_id}")
async def get_annotated_image(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取检测标注结果图片"""
    task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="检测任务不存在")

    # 从检测结果中找到标注图路径
    result = (
        db.query(DetectionResult).filter(DetectionResult.task_id == task_id).first()
    )
    if not result or not result.annotated_image_url:  # type: ignore[arg-type]
        raise HTTPException(status_code=404, detail="标注图片不存在")

    annotated_path: str = result.annotated_image_url  # type: ignore[assignment]
    if not os.path.exists(annotated_path):
        raise HTTPException(status_code=404, detail="标注图片文件不存在")

    return FileResponse(
        path=annotated_path,
        media_type="image/jpeg",
    )


@router.get("/tasks")
async def list_detection_tasks(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的检测历史列表，支持按日期筛选"""
    from datetime import datetime as dt

    query = db.query(DetectionTask).filter(DetectionTask.user_id == current_user.id)
    if start_date:
        query = query.filter(DetectionTask.created_at >= dt.fromisoformat(start_date))
    if end_date:
        query = query.filter(
            DetectionTask.created_at <= dt.fromisoformat(end_date + "T23:59:59")
        )
    query = query.order_by(DetectionTask.created_at.desc())
    total = query.count()
    tasks = query.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for t in tasks:
        # 获取该任务检测到的类别汇总
        class_summary = {}
        for r in t.results:
            cn = r.class_name_cn or r.class_name
            class_summary[cn] = class_summary.get(cn, 0) + 1

        items.append(
            {
                "id": t.id,
                "task_type": t.task_type,
                "status": t.status,
                "total_images": t.total_images,
                "total_objects": t.total_objects,
                "inference_time_ms": t.total_inference_time,
                "class_summary": class_summary,
                "created_at": str(t.created_at),
            }
        )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.get("/task/{task_id}")
async def get_detection_task_detail(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单次检测任务的详细信息（含所有检测到的病灶）"""
    task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="检测任务不存在")

    results = []
    for r in task.results:
        results.append(
            {
                "id": r.id,
                "class_name": r.class_name,
                "class_name_cn": r.class_name_cn,
                "class_id": r.class_id,
                "confidence": r.confidence,
                "bbox": r.bbox,
                "image_width": r.image_width,
                "image_height": r.image_height,
            }
        )

    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "total_images": task.total_images,
        "total_objects": task.total_objects,
        "inference_time_ms": task.total_inference_time,
        "conf_threshold": task.conf_threshold,
        "iou_threshold": task.iou_threshold,
        "image_size": task.image_size,
        "created_at": str(task.created_at),
        "completed_at": str(task.completed_at) if task.completed_at else None,  # type: ignore[arg-type]
        "objects": results,
        "annotated_image_url": f"/api/detection/image/{task_id}",
    }


@router.get("/models")
async def list_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取可用的模型版本列表（供检测页面切换模型）"""
    scene = db.query(DetectionScene).filter(DetectionScene.name == "chest_xray").first()
    if not scene:
        return {"models": []}

    versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.scene_id == scene.id)
        .order_by(ModelVersion.created_at.desc())
        .all()
    )

    return {
        "models": [
            {
                "id": v.id,
                "version": v.version,
                "model_name": v.model_name,
                "model_type": v.model_type,
                "map50": v.map50,
                "map50_95": v.map50_95,
                "is_default": v.is_default,
                "created_at": str(v.created_at),
            }
            for v in versions
        ]
    }


# ═══════════════════════════════════════════════════
# 快捷检测接口（v3.0 改为入库）
# ═══════════════════════════════════════════════════


@router.post("/single", summary="快捷单图检测")
async def detect_single_shortcut(
    file: UploadFile = File(...),
    conf: float = Form(0.25),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """快捷单图检测（入库 + 返回结果）"""
    suffix = os.path.splitext(file.filename or "image.png")[1] or ".png"
    tmp_path = os.path.join(
        tempfile.gettempdir(), f"chestx_{uuid.uuid4().hex[:8]}{suffix}"
    )
    try:
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)
        result = detection_service.detect_single(tmp_path, conf=conf)
        result["filename"] = file.filename

        # 入库
        task = _save_detection_to_db(
            db, current_user, file.filename or "single", result, "single"
        )
        result["task_id"] = task.id if task else None
        return result
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@router.post("/batch", summary="快捷批量检测")
async def detect_batch_shortcut(
    files: list[UploadFile] = File(...),
    conf: float = Form(0.25),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """快捷批量检测（入库 + 返回结果）"""
    temp_paths = []
    try:
        for file in files:
            suffix = os.path.splitext(file.filename or "img.png")[1] or ".png"
            tmp = os.path.join(
                tempfile.gettempdir(), f"chestx_{uuid.uuid4().hex[:8]}{suffix}"
            )
            content = await file.read()
            with open(tmp, "wb") as f:
                f.write(content)
            temp_paths.append(tmp)

        result = detection_service.detect_batch(temp_paths, conf=conf)

        # 入库
        filename_list = [f.filename or "batch" for f in files]
        task = _save_detection_to_db(
            db, current_user, ", ".join(filename_list), result, "batch"
        )
        result["task_id"] = task.id if task else None
        return result
    finally:
        for p in temp_paths:
            try:
                os.remove(p)
            except Exception:
                pass


@router.post("/zip", summary="快捷 ZIP 检测")
async def detect_zip_shortcut(
    file: UploadFile = File(...),
    conf: float = Form(0.25),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """快捷 ZIP 检测（入库 + 返回结果）"""
    tmp_path = os.path.join(
        tempfile.gettempdir(), f"chestx_zip_{uuid.uuid4().hex[:8]}.zip"
    )
    try:
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)
        result = detection_service.detect_zip(tmp_path, conf=conf)

        # 入库
        task = _save_detection_to_db(
            db, current_user, file.filename or "zip", result, "zip"
        )
        result["task_id"] = task.id if task else None
        return result
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _save_detection_to_db(db, current_user, image_path, result, task_type):
    """快捷检测入库辅助函数，适配 detect_single/batch/zip 返回格式"""
    from app.entity.db_models import DetectionScene, ModelVersion, PatientProfile

    try:
        scene = (
            db.query(DetectionScene).filter(DetectionScene.name == "chest_xray").first()
        )
        if not scene:
            logger.warning("检测场景不存在，跳过入库")
            return None

        model_version = (
            db.query(ModelVersion)
            .filter(ModelVersion.scene_id == scene.id)
            .order_by(ModelVersion.created_at.desc())
            .first()
        )

        patient_profile_id = None
        if current_user.user_type == "patient":
            profile = (
                db.query(PatientProfile)
                .filter(PatientProfile.user_id == current_user.id)
                .first()
            )
            if profile:
                patient_profile_id = profile.id

        # 适配 detect_single 返回格式（detections）→ predict 格式（objects）
        predict_result = {
            "total_objects": result.get("total_objects", 0),
            "inference_time": result.get("inference_time", 0),
            "image_width": 0,
            "image_height": 0,
            "objects": [
                {
                    "class_name": d.get("class_name", ""),
                    "class_name_cn": d.get("class_name_cn", ""),
                    "class_id": d.get("class_id", 0),
                    "confidence": d.get("confidence", 0),
                    "bbox": d.get("bbox", []),
                }
                for d in result.get("detections", [])
            ],
        }
        # 如果是 batch/zip，可能有 annotated_image_path
        if result.get("annotated_image_path"):
            predict_result["annotated_image_path"] = result["annotated_image_path"]

        return detection_service.save_detection_task(
            db=db,
            user_id=current_user.id,
            scene_id=scene.id,
            model_version_id=model_version.id if model_version else None,
            image_path=image_path,
            predict_result=predict_result,
            task_type=task_type,
            patient_profile_id=patient_profile_id,
        )
    except Exception as e:
        logger.error("快捷检测入库失败: %s", str(e))
        return None
