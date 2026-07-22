"""
对话相关 API 路由

接口列表：
  - POST /api/chat/upload              上传图片/文件，返回服务端路径
  - POST /api/chat/stream              SSE 流式对话（核心接口）
  - GET  /api/chat/sessions            获取当前用户的会话列表
  - GET  /api/chat/sessions/{id}/messages  获取指定会话的消息历史
  - DELETE /api/chat/sessions/{id}     删除指定会话
"""

import json
import os
import tempfile
import time
import uuid

from app.agent.detection_agent import detection_agent
from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.services import chat_service
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
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


@router.post("/stream", summary="SSE 流式对话（含历史持久化）")
async def chat_stream(
    request: Request,
    current_user=Depends(get_current_user),
):
    """
    SSE 流式对话接口

    请求体：
    {
        "message": "帮我分析这张胸片",
        "image_path": "/tmp/xxx.png",        // 可选
        "session_id": 123,                    // 可选，不传则自动创建新会话
        "patient_profile_id": 123             // 可选，医生/管理员指定患者
    }
    """
    body = await request.json()
    message = body.get("message", "")
    image_path = body.get("image_path")
    session_id = body.get("session_id")
    patient_profile_id = body.get("patient_profile_id")

    if not message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    logger.info("用户 %s 发起对话: %s", current_user.username, message[:50])

    # ── ① 创建或获取会话 ──
    try:
        session = chat_service.get_or_create_session(
            user_id=current_user.id,
            session_id=session_id,
        )
        db_session_id = session.id
        session_uuid = session.session_uuid
    except Exception as e:
        logger.error("创建/获取会话失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建会话失败")

    # ── ② 保存用户消息 ──
    try:
        chat_service.save_message(
            session_id=db_session_id,
            role="user",
            content=message,
        )
    except Exception as e:
        logger.warning("保存用户消息失败: %s", str(e))

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
            if patient_profile_id and current_user.user_type != "patient":
                profile = (
                    db.query(PatientProfile)
                    .filter(PatientProfile.id == patient_profile_id)
                    .first()
                )
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
            elif current_user.user_type == "patient":
                profile = (
                    db.query(PatientProfile)
                    .filter(PatientProfile.user_id == current_user.id)
                    .first()
                )

            if profile:
                records = (
                    db.query(MedicalRecord)
                    .filter(MedicalRecord.patient_profile_id == profile.id)
                    .order_by(MedicalRecord.visit_date.desc().nullslast())
                    .limit(3)
                    .all()
                )
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
        full_response = ""  # 收集完整回复
        start_time = time.time()

        # ── ④ 加载对话历史（实现多轮记忆）──
        chat_history = []
        if session_id:
            try:
                chat_history = chat_service.build_langchain_history(
                    session_id=db_session_id,
                    user_id=current_user.id,
                )
                logger.info("加载 %d 条历史消息到 Agent 上下文", len(chat_history))
            except Exception as e:
                logger.warning("加载对话历史失败: %s", str(e))

        try:
            from app.agent.detection_agent import DetectionAgent

            DetectionAgent._current_user = current_user
            async for event in detection_agent.chat_stream(
                message=enhanced_message,
                image_path=image_path,
                chat_history=chat_history if chat_history else None,
            ):
                event_data = json.dumps(event, ensure_ascii=False)
                yield f"data: {event_data}\n\n"

                # 收集 AI 文本回复
                if event.get("type") == "text_chunk":
                    full_response += event.get("content", "")

            # 发送完成信号（附带 session 信息）
            done_data = json.dumps(
                {
                    "type": "done",
                    "session_id": db_session_id,
                    "session_uuid": session_uuid,
                },
                ensure_ascii=False,
            )
            yield f"data: {done_data}\n\n"

            # ── ③ 保存 AI 回复 ──
            latency_ms = int((time.time() - start_time) * 1000)
            if full_response.strip():
                try:
                    chat_service.save_message(
                        session_id=db_session_id,
                        role="assistant",
                        content=full_response.strip(),
                        latency_ms=latency_ms,
                    )
                except Exception as e:
                    logger.warning("保存 AI 回复失败: %s", str(e))

        except Exception as e:
            logger.error("SSE 异常: %s", str(e), exc_info=True)
            error_data = json.dumps(
                {"type": "error", "content": "对话处理失败，请稍后重试"},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════
# 对话历史相关 API
# ══════════════════════════════════════════════════════════════


@router.get("/sessions", summary="获取当前用户的会话列表")
async def list_sessions(
    status: str = Query("active", description="会话状态：active/archived/all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
):
    """获取当前登录用户的所有对话会话"""
    filter_status = None if status == "all" else status
    sessions = chat_service.get_user_sessions(
        user_id=current_user.id,
        status=filter_status,
        limit=limit,
        offset=offset,
    )
    return {
        "total": len(sessions),
        "sessions": [
            {
                "id": s.id,
                "session_uuid": s.session_uuid,
                "title": s.title,
                "status": s.status,
                "message_count": s.message_count,
                "last_message_at": s.last_message_at.isoformat()
                if s.last_message_at
                else None,
                "created_at": s.created_at.isoformat(),
            }
            for s in sessions
        ],
    }


@router.get("/sessions/{session_id}/messages", summary="获取指定会话的消息历史")
async def get_messages(
    session_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
):
    """获取指定会话的所有消息（仅会话所有者可访问）"""
    messages = chat_service.get_session_messages(
        session_id=session_id,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
    )
    if not messages and not chat_service.get_or_create_session(
        current_user.id, session_id
    ):
        # session 不存在或不属于该用户
        # get_or_create_session 会为不存在的 session_id 创建新会话，这里额外判断
        pass

    return {
        "session_id": session_id,
        "total": len(messages),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "agent_used": m.agent_used,
                "tool_calls": m.tool_calls,
                "tool_result": m.tool_result,
                "tokens_used": m.tokens_used,
                "latency_ms": m.latency_ms,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.delete("/sessions/{session_id}", summary="删除指定会话")
async def delete_session(
    session_id: int,
    current_user=Depends(get_current_user),
):
    """删除指定会话及其所有消息（仅会话所有者可操作）"""
    success = chat_service.delete_session(
        session_id=session_id,
        user_id=current_user.id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在或无权操作")
    return {"message": "会话已删除", "session_id": session_id}


@router.put("/sessions/{session_id}/archive", summary="归档指定会话")
async def archive_session(
    session_id: int,
    current_user=Depends(get_current_user),
):
    """归档指定会话（仅会话所有者可操作）"""
    session = chat_service.archive_session(
        session_id=session_id,
        user_id=current_user.id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或无权操作")
    return {"message": "会话已归档", "session_id": session_id}
