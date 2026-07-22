"""
对话相关 API 路由

接口列表：
  - POST /api/chat/upload              上传图片/文件，返回服务端路径
  - POST /api/chat/stream              SSE 流式对话（核心接口 — 多 Agent 协作）
  - GET  /api/chat/sessions            获取当前用户的会话列表
  - GET  /api/chat/sessions/{id}/messages  获取指定会话的消息历史
  - DELETE /api/chat/sessions/{id}     删除指定会话

架构说明（v2 — 多 Agent 协作）：
  用户请求 → Supervisor 路由 → Detection/Diagnosis/Report/QA Agent
                                → Summarize 汇总 → SSE 流式返回
"""

import asyncio
import json
import os
import re
import tempfile
import time

from app.agent.graph import run_graph_stream
from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.services import chat_service
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage as LcAIMessage
from langchain_core.messages import HumanMessage as LcHumanMessage
from langchain_core.messages import SystemMessage

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["智能对话"])

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "chestx_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

_ATTACHMENT_PATH_PATTERN = re.compile(
    r"\[附件(?:图片|多张图片|视频|ZIP)路径:\s*.*?\]"
)
_SERVER_PATH_PATTERN = re.compile(
    r"(?:(?:/tmp|/app|/var/tmp)/[^\s`，。；、）》）\]]+)"
)


def _sanitize_visible_message(content: str) -> str:
    """历史接口不得向浏览器返回工具使用的服务器内部路径。"""
    if not isinstance(content, str):
        return content
    content = _ATTACHMENT_PATH_PATTERN.sub("已上传胸片", content)
    return _SERVER_PATH_PATTERN.sub("内部路径已隐藏", content)


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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("创建/获取会话失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建会话失败")

    # ── ② 在保存本轮消息前加载历史，避免把当前问题重复放入上下文 ──
    chat_history = []
    if session_id:
        try:
            raw_history = chat_service.build_langchain_history(
                session_id=db_session_id,
                user_id=current_user.id,
            )
            # 历史上下文只保留对话内容，不把服务端附件路径再次交给模型。
            for history_message in raw_history:
                clean_content = re.sub(
                    r"\[附件(图片|多张图片|视频|ZIP)路径:.*?\]",
                    "",
                    history_message.content,
                ).strip()
                if not clean_content:
                    continue
                if history_message.__class__.__name__ == "HumanMessage":
                    chat_history.append(LcHumanMessage(content=clean_content))
                elif history_message.__class__.__name__ == "AIMessage":
                    chat_history.append(LcAIMessage(content=clean_content))
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
        detected_agent = "unknown"

        # ── ④ 注入上下文（无新图片时从DB查最近检测结果）──
        final_message = enhanced_message
        prior_detection = None  # 用于注入到 state
        if not image_path and chat_history:
            has_prior_ai = any(
                msg.__class__.__name__ == "AIMessage" for msg in chat_history
            )
            if has_prior_ai and len(chat_history) >= 2:
                try:
                    from app.database.session import SessionLocal as SL2
                    from app.entity.db_models import DetectionResult, DetectionTask
                    db2 = SL2()
                    try:
                        last_task = (
                            db2.query(DetectionTask)
                            .filter(
                                DetectionTask.user_id == current_user.id,
                                DetectionTask.status == "completed",
                            )
                            .order_by(DetectionTask.created_at.desc())
                            .first()
                        )
                        if last_task and last_task.total_objects:
                            details = (
                                db2.query(DetectionResult)
                                .filter(DetectionResult.task_id == last_task.id)
                                .all()
                            )
                            # 构建 class_counts（用于 diagnosis 和 summarize 节点）
                            class_counts = {}
                            detections_list = []
                            for r in details:
                                cls_name = r.class_name or "Unknown"
                                class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
                                detections_list.append({
                                    "class_name": cls_name,
                                    "class_name_cn": r.class_name_cn or cls_name,
                                    "confidence": r.confidence or 0,
                                    "bbox": r.bbox_json if hasattr(r, 'bbox_json') else None,
                                })
                            # ⭐ 关键修复：把历史检测结果注入到 state 结构中
                            prior_detection = {
                                "total_objects": last_task.total_objects,
                                "class_counts": class_counts,
                                "inference_time": last_task.total_inference_time or 0,
                                "status": "completed",
                                "task_id": last_task.id,
                                "detections": detections_list,
                                "risk_level": last_task.risk_level or "unknown",
                            }
                            lesion_list = "、".join(
                                f"{r.class_name_cn or r.class_name}"
                                f"({r.confidence:.0%})"
                                for r in details[:10]
                            )
                            final_message = (
                                f"「上下文」用户刚才完成了胸片检测，"
                                f"真实检测结果为：共{last_task.total_objects}个病灶 → {lesion_list}。"
                                f"现在用户问：「{enhanced_message}」。"
                                f"请基于以上真实检测数据回答，不要编造其他病灶类型。"
                            )
                            logger.info("注入检测上下文: task=%d, lesions=%s", last_task.id, lesion_list)
                        else:
                            final_message = (
                                f"「上下文」你与用户已有对话历史。"
                                f"现在用户继续问：「{enhanced_message}」。"
                                f"请基于对话历史直接回答，不要索要图片、不要编造数据。"
                            )
                    finally:
                        db2.close()
                except Exception as e:
                    logger.warning("查询最近检测结果失败: %s", str(e))

        # ── ⑤ 构建多 Agent 图初始状态 ──
        messages_for_graph = list(chat_history) if chat_history else []
        messages_for_graph.append(LcHumanMessage(content=final_message))

        # ⭐ 将历史检测结果注入 state，确保 diagnosis 节点能获取到
        detection_result_for_state = prior_detection if prior_detection else {}
        task_id_for_state = prior_detection.get("task_id") if prior_detection else None

        initial_state = {
            "messages": messages_for_graph,
            "user_id": current_user.id,
            "session_id": str(db_session_id),
            "patient_profile_id": patient_profile_id,
            "image_path": image_path,
            "next_agent": "supervisor",
            "routed_agent": "",
            "detection_result": detection_result_for_state,
            "diagnosis_result": {},
            "report_result": "",
            "qa_result": "",
            "case_analysis_result": {},
            "final_response": "",
            "knowledge_sources": [],
            "has_knowledge": False,
            "task_id": task_id_for_state,
            "error": None,
        }

        # ── ⑥ 缓存用户消息到 Redis（缓存层，DB 为主存储）──
        try:
            from app.agent.memory import conversation_memory
            conversation_memory.save_message(
                current_user.id, str(db_session_id), "user", message  # 存原始消息
            )
        except Exception as e:
            logger.warning("Redis缓存用户消息失败: %s", str(e))

        # ── ⑦ 执行多 Agent 图并流式返回 ──
        try:
            async for event in run_graph_stream(initial_state):
                event_type = event.get("type", "")

                # 记录 Agent 路径
                if event_type == "thinking":
                    content = event.get("content", "")
                    if "检测" in content:
                        detected_agent = "detection"
                    elif "诊断" in content:
                        detected_agent = "diagnosis"
                    elif "报告" in content:
                        detected_agent = "report"
                    elif "知识库" in content:
                        detected_agent = "qa"

                # 收集文本回复
                if event_type == "text_chunk":
                    full_response += event.get("content", "")

                # 对 done 事件补充 session 信息
                if event_type == "done":
                    event["session_id"] = db_session_id
                    event["session_uuid"] = session_uuid
                    event["agent_used"] = detected_agent

                event_data = json.dumps(event, ensure_ascii=False)
                yield f"data: {event_data}\n\n"

            # ── ⑧ 保存 AI 回复到 DB（主存储）──
            latency_ms = int((time.time() - start_time) * 1000)
            if full_response.strip():
                try:
                    chat_service.save_message(
                        session_id=db_session_id,
                        role="assistant",
                        content=full_response.strip(),
                        agent_used=detected_agent,
                        latency_ms=latency_ms,
                    )
                except Exception as e:
                    logger.warning("保存 AI 回复失败: %s", str(e))

                # Redis 缓存（与 DB 存储相同内容，保证一致性）
                try:
                    from app.agent.memory import conversation_memory
                    conversation_memory.save_message(
                        current_user.id, str(db_session_id), "ai", full_response.strip()
                    )
                except Exception as e:
                    logger.warning("Redis缓存AI回复失败: %s", str(e))

        except Exception as e:
            logger.error("多 Agent 图执行异常: %s", str(e), exc_info=True)
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
    if not chat_service.get_session(session_id=session_id, user_id=current_user.id):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    return {
        "session_id": session_id,
        "total": len(messages),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": _sanitize_visible_message(m.content),
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
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("创建/获取会话失败: %s", str(e))
        raise HTTPException(status_code=500, detail="创建会话失败")

    # ── 从 DB 加载历史（主存储），并在保存本轮消息前完成，避免当前消息重复 ──
    chat_history = []
    try:
        raw_history = chat_service.build_langchain_history(
            session_id=db_session_id,
            user_id=current_user.id,
        )
        for history_message in raw_history:
            clean_content = re.sub(
                r"\[附件(图片|多张图片|视频|ZIP)路径:.*?\]",
                "",
                history_message.content,
            ).strip()
            if not clean_content:
                continue
            if history_message.__class__.__name__ == "HumanMessage":
                chat_history.append(LcHumanMessage(content=clean_content))
            elif history_message.__class__.__name__ == "AIMessage":
                chat_history.append(LcAIMessage(content=clean_content))
        logger.info("Multi-Agent 从数据库加载 %d 条历史消息", len(chat_history))
    except Exception as e:
        logger.warning("Multi-Agent 加载数据库历史失败: %s", str(e))

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

    # ── 保存用户消息 ──
    try:
        chat_service.save_message(
            session_id=db_session_id,
            role="user",
            content=message,
        )
    except Exception as e:
        logger.warning("保存用户消息失败: %s", str(e))

    # ── 注：Multi-Agent 架构下，患者病史由各节点通过 _get_patient_context() 独立加载
    # 不再注入到消息中，避免病史中的关键词（如"诊断"）干扰 Supervisor 的路由判断

    # ── 确保 patient_profile_id 正确（自动匹配患者身份）──
    resolved_patient_id = patient_profile_id
    try:
        from app.database.session import SessionLocal
        from app.entity.db_models import DoctorPatientRelation, PatientProfile

        db = SessionLocal()
        try:
            if resolved_patient_id:
                profile = (
                    db.query(PatientProfile)
                    .filter(PatientProfile.id == resolved_patient_id)
                    .first()
                )
                if not profile:
                    raise HTTPException(status_code=404, detail="患者档案不存在")

                is_allowed = current_user.user_type == "admin"
                if current_user.user_type == "patient":
                    is_allowed = profile.user_id == current_user.id
                elif current_user.user_type == "doctor":
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
                    raise HTTPException(status_code=403, detail="无权访问该患者档案")
            elif current_user.user_type == "patient":
                profile = db.query(PatientProfile).filter(
                    PatientProfile.user_id == current_user.id
                ).first()
                if profile:
                    resolved_patient_id = profile.id
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("匹配或校验患者身份失败: %s", str(e))

    # ── 如果有图片路径注入到消息中 ──
    enhanced_message = message
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
            from app.agent.graph import build_agent_graph
            from app.agent.memory import conversation_memory
            from app.agent.state import MultiAgentState

            # ── 缓存用户消息 ──
            try:
                conversation_memory.save_message(
                    current_user.id, str(db_session_id), "user", enhanced_message
                )
            except Exception as e:
                logger.warning("缓存用户消息失败: %s", str(e))

            # ── 构建初始状态 ──
            initial_state: MultiAgentState = {
                "messages": chat_history + [LcHumanMessage(content=enhanced_message)],
                "next_agent": "",
                "routed_agent": "",
                "detection_result": {},
                "diagnosis_result": {},
                "report_result": "",
                "qa_result": "",
                "case_analysis_result": {},
                "final_response": "",
                "knowledge_sources": [],
                "has_knowledge": False,
                "user_id": current_user.id,
                "session_id": str(db_session_id),
                "patient_profile_id": resolved_patient_id,
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

                    # 检测节点完成后把卡片数据交给前端，并触发有病灶时的医生推荐。
                    if node_name == "detection":
                        detection_result = node_output.get("detection_result", {})
                        if (
                            detection_result
                            and "error" not in detection_result
                            and detection_result.get("total_objects", -1) >= 0
                        ):
                            card_data = {
                                "total_objects": detection_result.get("total_objects", 0),
                                "class_counts": detection_result.get("class_counts", {}),
                                "annotated_image_url": detection_result.get(
                                    "annotated_image_url", ""
                                ),
                                "annotated_image_base64": detection_result.get(
                                    "annotated_image_base64", ""
                                ),
                                "task_id": detection_result.get("task_id"),
                                "detections": detection_result.get("detections", []),
                                "inference_time": detection_result.get(
                                    "inference_time", 0
                                ),
                            }
                            yield f"data: {json.dumps({'type': 'detection_card', 'data': card_data}, ensure_ascii=False)}\n\n"

                    # 报告 Agent 只在真实检测任务存在时提供真实下载按钮。
                    if node_name == "report":
                        report_task_id = node_output.get("task_id")
                        if report_task_id and node_output.get("report_result"):
                            report_event = {
                                "type": "report_ready",
                                "task_id": report_task_id,
                                "pdf_url": f"/api/reports/{report_task_id}/pdf",
                            }
                            yield f"data: {json.dumps(report_event, ensure_ascii=False)}\n\n"

                    # 最终回复只由 Supervisor 统一回答节点发送
                    if node_name == "supervisor_answer" and node_output.get("final_response"):
                        final_text = node_output["final_response"]
                        knowledge_sources = node_output.get("knowledge_sources", [])
                        has_knowledge = node_output.get("has_knowledge", False)

                        # 只将 Supervisor 的最终回答流式显示到屏幕。
                        chunk_size = 14
                        for index in range(0, len(final_text), chunk_size):
                            chunk_event = {
                                "type": "text_chunk",
                                "content": final_text[index : index + chunk_size],
                            }
                            if index == 0:
                                chunk_event["knowledge_sources"] = knowledge_sources
                                chunk_event["has_knowledge"] = has_knowledge
                            yield f"data: {json.dumps(chunk_event, ensure_ascii=False)}\n\n"
                            await asyncio.sleep(0.018)
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
