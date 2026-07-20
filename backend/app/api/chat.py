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
from langchain_core.messages import SystemMessage

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

    # ── ② 在保存本轮消息前加载历史，避免把当前问题重复放入上下文 ──
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

    # 当前用户有权知道自己的登录身份。历史中的自述姓名、专长等信息也应
    # 作为后续对话上下文使用，而不是以“隐私限制”为由遗忘。
    chat_history.insert(
        0,
        SystemMessage(
            content=(
                "[当前登录用户上下文] "
                f"用户ID={current_user.id}，账号={current_user.username}，"
                f"用户类型={current_user.user_type}，邮箱={current_user.email}。"
                "你可以向当前用户复述其本人的这些信息。若历史消息中用户曾自述姓名、"
                "职称、专长或偏好，应在后续回答中记住并注明这是用户自述信息。"
            )
        ),
    )

    # ── ③ 保存用户消息 ──
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

        # ── ④ Day11: 缓存用户消息到 Redis 记忆 ──
        try:
            from app.agent.memory import conversation_memory
            conversation_memory.save_message(
                current_user.id, str(db_session_id), "user", enhanced_message
            )
        except Exception as e:
            logger.warning("Redis缓存用户消息失败: %s", str(e))

        try:
            from app.agent.detection_agent import DetectionAgent

            DetectionAgent._current_user = current_user
            # Day11: 设置工具模块中的当前用户
            from app.agent.tools.analysis_tool import set_current_user
            set_current_user(current_user)

            async for event in detection_agent.chat_stream(
                message=enhanced_message,
                image_path=image_path,
                chat_history=chat_history if chat_history else None,
                user_id=current_user.id,
                session_id=str(db_session_id),
            ):
                # DetectionAgent 的内部 done 不含会话 ID。若转发，前端会提前
                # 结束 SSE，收不到下面携带 session_id 的最终 done，导致每轮
                # 对话都新建会话、历史永久丢失。
                if event.get("type") == "done":
                    continue
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
                {"type": "error", "content": str(e)}, ensure_ascii=False
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


# ══════════════════════════════════════════════════════════════
# Multi-Agent 多智能体对话 API（Day 12 新增）
# ══════════════════════════════════════════════════════════════


@router.post("/multi-agent", summary="多智能体协作对话（LangGraph）")
async def multi_agent_chat(
    request: Request,
    current_user=Depends(get_current_user),
):
    """
    多智能体协作对话接口 — 使用 LangGraph 编排多个专业 Agent

    Agent 协作流程：
      Supervisor（路由）→ Detection/Diagnosis/Report/QA → Summarize（汇总输出）

    请求体：
    {
        "message": "帮我分析这张胸片",
        "image_path": "/tmp/xxx.png",      // 可选，附件图片路径
        "session_id": 123,                  // 可选，不传则自动创建
        "patient_profile_id": 123           // 可选，指定患者上下文
    }

    响应：SSE 流式事件
    """
    body = await request.json()
    message = body.get("message", "")
    image_path = body.get("image_path")
    session_id = body.get("session_id")
    patient_profile_id = body.get("patient_profile_id")

    if not message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    # ── 创建或获取会话 ──
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

    # ── 保存用户消息 ──
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
                    .limit(5)
                    .all()
                )
                tasks = (
                    db.query(DetectionTask)
                    .filter(
                        DetectionTask.patient_profile_id == profile.id,
                        DetectionTask.status == "completed",
                    )
                    .order_by(DetectionTask.created_at.desc())
                    .limit(5)
                    .all()
                )

                if records or tasks:
                    ctx_parts = [
                        f"[系统上下文：以下是患者 {profile.patient_code} 的历史信息]\n"
                    ]
                    if records:
                        ctx_parts.append("## 历史病例")
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

    # ── 如果有图片路径注入到消息中 ──
    if image_path:
        enhanced_message = f"{enhanced_message}\n[附件图片路径: {image_path}]"

    logger.info(
        "Multi-Agent 对话: user=%s, session=%d, msg=%s",
        current_user.username, db_session_id, enhanced_message[:80],
    )

    async def event_generator():
        full_response = ""
        start_time = time.time()

        try:
            from langchain_core.messages import AIMessage, HumanMessage

            from app.agent.graph import build_agent_graph
            from app.agent.memory import conversation_memory
            from app.agent.state import MultiAgentState

            # ── 加载对话历史 ──
            chat_history = []
            try:
                history = conversation_memory.load_history(
                    user_id=current_user.id, session_id=str(db_session_id)
                )
                for msg in history[-20:]:
                    if msg.get("role") == "user":
                        chat_history.append(HumanMessage(content=msg.get("content", "")))
                    elif msg.get("role") == "ai":
                        chat_history.append(AIMessage(content=msg.get("content", "")))
                logger.info("Multi-Agent 加载 %d 条历史消息", len(chat_history))
            except Exception as e:
                logger.warning("Multi-Agent 加载历史失败: %s", str(e))

            # ── 缓存用户消息 ──
            try:
                conversation_memory.save_message(
                    current_user.id, str(db_session_id), "user", enhanced_message
                )
            except Exception as e:
                logger.warning("缓存用户消息失败: %s", str(e))

            # ── 构建初始状态 ──
            initial_state: MultiAgentState = {
                "messages": chat_history + [HumanMessage(content=enhanced_message)],
                "next_agent": "",
                "detection_result": {},
                "diagnosis_result": {},
                "report_result": "",
                "qa_result": "",
                "final_response": "",
                "knowledge_sources": [],
                "has_knowledge": False,
                "user_id": current_user.id,
                "session_id": str(db_session_id),
                "patient_profile_id": patient_profile_id,
                "image_path": image_path,
                "task_id": None,
                "error": None,
            }

            # ── 构建并运行 LangGraph ──
            graph = build_agent_graph()
            yield f"data: {json.dumps({'type': 'thinking', 'content': 'Multi-Agent 协作分析中...'}, ensure_ascii=False)}\n\n"

            async for event in graph.astream(initial_state, stream_mode="updates"):
                for node_name, node_output in event.items():
                    logger.info("Multi-Agent 节点完成: %s", node_name)

                    # 发送节点状态事件
                    yield f"data: {json.dumps({'type': 'agent_node', 'node': node_name, 'status': 'completed'}, ensure_ascii=False)}\n\n"

                    # 如果是汇总节点，发送最终回复
                    if node_name == "summarize" and node_output.get("final_response"):
                        final_text = node_output["final_response"]
                        knowledge_sources = node_output.get("knowledge_sources", [])
                        has_knowledge = node_output.get("has_knowledge", False)

                        # 发送 text_chunk
                        yield f"data: {json.dumps({'type': 'text_chunk', 'content': final_text, 'knowledge_sources': knowledge_sources, 'has_knowledge': has_knowledge}, ensure_ascii=False)}\n\n"
                        full_response = final_text

            # ── 保存 AI 回复 ──
            latency_ms = int((time.time() - start_time) * 1000)
            if full_response.strip():
                try:
                    conversation_memory.save_message(
                        current_user.id, str(db_session_id), "ai", full_response.strip()
                    )
                    chat_service.save_message(
                        session_id=db_session_id,
                        role="assistant",
                        content=full_response.strip(),
                        agent_used="multi-agent",
                        latency_ms=latency_ms,
                    )
                except Exception as e:
                    logger.warning("保存AI回复失败: %s", str(e))

            # ── 发送完成事件 ──
            yield f"data: {json.dumps({'type': 'done', 'session_id': db_session_id, 'session_uuid': session_uuid}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error("Multi-Agent 异常: %s", str(e), exc_info=True)
            error_data = json.dumps(
                {"type": "error", "content": f"Multi-Agent处理出错: {str(e)}"},
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
