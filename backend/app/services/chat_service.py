"""
聊天会话 & 消息服务层
提供 ChatSession / ChatMessage 的 CRUD 操作
"""

import uuid
from datetime import datetime
from typing import Optional

from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import ChatMessage, ChatSession
from app.entity.schemas import ChatMessageRequest, ChatSessionCreate
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import desc

logger = get_logger(__name__)


def create_session(
    user_id: int,
    title: Optional[str] = None,
    session_uuid: Optional[str] = None,
) -> ChatSession:
    """为指定用户创建一个新会话"""
    db = SessionLocal()
    try:
        session = ChatSession(
            user_id=user_id,
            session_uuid=session_uuid or str(uuid.uuid4()),
            title=title or "新对话",
            status="active",
            message_count=0,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session
    finally:
        db.close()


def get_or_create_session(
    user_id: int,
    session_id: Optional[int] = None,
    title: Optional[str] = None,
) -> ChatSession:
    """获取或创建会话

    查找优先级：
      1. 有合法 session_id → 直接返回
      2. 无 session_id → 自动续接用户最近一条活跃会话（修复页面刷新丢上下文）
      3. 无活跃会话 → 创建新会话
    """
    db = SessionLocal()
    try:
        if session_id:
            session = (
                db.query(ChatSession)
                .filter(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                )
                .first()
            )
            if session:
                return session

        # ── 自动续接最近活跃会话 ──
        last_session = (
            db.query(ChatSession)
            .filter(
                ChatSession.user_id == user_id,
                ChatSession.status == "active",
            )
            .order_by(desc(ChatSession.last_message_at))
            .first()
        )
        if last_session:
            logger.info(
                "自动续接会话: user=%d, session=%d, title=%s",
                user_id, last_session.id, last_session.title,
            )
            return last_session

        return create_session(user_id, title)
    finally:
        db.close()


def save_message(
    session_id: int,
    role: str,
    content: str,
    agent_used: Optional[str] = None,
    tool_calls: Optional[list] = None,
    tool_result: Optional[str] = None,
    tokens_used: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> ChatMessage:
    """保存一条消息到指定会话"""
    db = SessionLocal()
    try:
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            agent_used=agent_used,
            tool_calls=tool_calls,
            tool_result=tool_result,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
        )
        db.add(msg)

        # 同步更新会话统计
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session:
            session.message_count = (session.message_count or 0) + 1
            session.last_message_at = datetime.now()
            # 取用户第一条消息的前30字作为标题
            if session.title == "新对话" and role == "user":
                session.title = content[:30]

        db.commit()
        db.refresh(msg)
        return msg
    finally:
        db.close()


def get_user_sessions(
    user_id: int,
    status: Optional[str] = "active",
    limit: int = 50,
    offset: int = 0,
) -> list[ChatSession]:
    """获取用户的会话列表"""
    db = SessionLocal()
    try:
        query = db.query(ChatSession).filter(ChatSession.user_id == user_id)
        if status:
            query = query.filter(ChatSession.status == status)
        return (
            query.order_by(desc(ChatSession.last_message_at))
            .offset(offset)
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def get_session_messages(
    session_id: int,
    user_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[ChatMessage]:
    """获取指定会话的消息列表（校验归属用户）"""
    db = SessionLocal()
    try:
        # 先校验会话归属
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
            .first()
        )
        if not session:
            return []

        return (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
            .offset(offset)
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def get_recent_messages(
    session_id: int,
    user_id: int,
    limit: int = 20,
) -> list[ChatMessage]:
    """获取最近 N 条消息（用于构建 LangChain chat_history）"""
    return get_session_messages(session_id, user_id, limit=limit)


def delete_session(session_id: int, user_id: int) -> bool:
    """删除指定会话（仅所有者可删除）"""
    db = SessionLocal()
    try:
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
            .first()
        )
        if not session:
            return False
        db.delete(session)
        db.commit()
        return True
    finally:
        db.close()


def archive_session(session_id: int, user_id: int) -> Optional[ChatSession]:
    """归档指定会话"""
    db = SessionLocal()
    try:
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
            .first()
        )
        if session:
            session.status = "archived"
            db.commit()
            db.refresh(session)
        return session
    finally:
        db.close()


def build_langchain_history(
    session_id: int,
    user_id: int,
    max_messages: int = 20,
) -> list:
    """从数据库加载最近消息，转换为 LangChain 消息格式

    返回: [HumanMessage, AIMessage, HumanMessage, AIMessage, ...]
    用于传入 Agent 的 chat_history 参数，实现多轮记忆
    """
    messages = get_recent_messages(session_id, user_id, limit=max_messages)
    langchain_msgs = []
    for m in messages:
        if m.role == "user":
            langchain_msgs.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            langchain_msgs.append(AIMessage(content=m.content))
        # system/tool 角色暂不传入，避免干扰
    return langchain_msgs
