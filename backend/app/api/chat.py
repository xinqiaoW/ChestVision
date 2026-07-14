"""
对话相关 API 路由

接口列表：
  - POST /api/chat/upload    上传图片/文件，返回服务端路径
  - POST /api/chat/stream    SSE 流式对话（核心接口）
"""

import json
import os
import tempfile

from app.agent.detection_agent import detection_agent
from app.api.auth import get_current_user
from app.core.logger import get_logger
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["智能对话"])

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "chestx_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload", summary="上传胸片文件")
async def upload_image(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """上传胸片文件到服务端临时目录"""
    suffix = os.path.splitext(file.filename or "image.png")[1] or ".png"
    filename = f"{os.getpid()}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    logger.info("文件上传: %s -> %s", file.filename, file_path)
    return {"image_path": file_path}


@router.post("/stream", summary="SSE 流式对话")
async def chat_stream(
    request: Request,
    current_user=Depends(get_current_user),
):
    """
    SSE 流式对话接口

    请求体：
    {
        "message": "帮我分析这张胸片",
        "image_path": "/tmp/xxx.png",   // 可选
        "session_id": 123                // 可选
    }
    """
    body = await request.json()
    message = body.get("message", "")
    image_path = body.get("image_path")
    patient_profile_id = body.get("patient_profile_id")  # v3.0：医生/管理员指定患者

    if not message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    logger.info("用户 %s 发起对话: %s", current_user.username, message[:50])

    # ── 注入患者病史上下文 ──
    enhanced_message = message
    try:
        from app.database.session import SessionLocal
        from app.entity.db_models import (
            DetectionTask,
            DoctorPatientRelation,
            MedicalRecord,
            PatientProfile,
        )

        db = SessionLocal()
        try:
            profile = None
            # 医生/管理员指定了患者
            if patient_profile_id and current_user.user_type != "patient":
                profile = (
                    db.query(PatientProfile)
                    .filter(PatientProfile.id == patient_profile_id)
                    .first()
                )
                # 医生权限检查
                if profile and current_user.user_type == "doctor":
                    rel = (
                        db.query(DoctorPatientRelation)
                        .filter(
                            DoctorPatientRelation.doctor_id == current_user.id,
                            DoctorPatientRelation.patient_id == profile.user_id,
                            DoctorPatientRelation.relation_status == "active",
                        )
                        .first()
                    )
                    if not rel:
                        profile = None
            # 病人自动关联自己
            elif current_user.user_type == "patient":
                profile = (
                    db.query(PatientProfile)
                    .filter(PatientProfile.user_id == current_user.id)
                    .first()
                )

            if profile:
                # 拉取最近病例
                records = (
                    db.query(MedicalRecord)
                    .filter(MedicalRecord.patient_profile_id == profile.id)
                    .order_by(MedicalRecord.visit_date.desc().nullslast())
                    .limit(3)
                    .all()
                )
                # 拉取最近检测
                tasks = (
                    db.query(DetectionTask)
                    .filter(
                        DetectionTask.patient_profile_id == profile.id,
                        DetectionTask.status == "completed",
                    )
                    .order_by(DetectionTask.created_at.desc())
                    .limit(3)
                    .all()
                )

                if records or tasks:
                    ctx_parts = [
                        f"[系统上下文：以下是患者 {profile.patient_code} 的历史信息，请在回答时参考]\n"
                    ]
                    if records:
                        ctx_parts.append("## 患者历史病例")
                        for r in records:
                            ctx_parts.append(
                                f"- {r.record_type} ({r.visit_date}): "
                                f"主诉={r.chief_complaint or '无'}, "
                                f"诊断={r.diagnosis or '无'}"
                            )
                    if tasks:
                        ctx_parts.append("\n## 历史检测结果")
                        for t in tasks:
                            ctx_parts.append(
                                f"- 检测ID={t.id} ({t.created_at}): "
                                f"检出{t.total_objects}个病灶, "
                                f"风险={t.risk_level or '未评估'}"
                            )
                    enhanced_message = "\n".join(ctx_parts) + "\n\n" + message
        finally:
            db.close()
    except Exception as e:
        logger.warning("注入病史上下文失败: %s", str(e))

    async def event_generator():
        try:
            from app.agent.detection_agent import DetectionAgent

            DetectionAgent._current_user = current_user
            async for event in detection_agent.chat_stream(
                message=enhanced_message, image_path=image_path
            ):
                event_data = json.dumps(event, ensure_ascii=False)
                yield f"data: {event_data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error("SSE 异常: %s", str(e), exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
