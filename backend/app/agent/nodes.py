"""
Multi-Agent 节点实现 — 胸片X光智能分析系统

各专业 Agent 节点：
  - detection_node:  病灶检测 Agent → 调用 YOLO 模型检测胸片
  - diagnosis_node:  综合诊断 Agent → 结合检测结果 + 病史 + 知识库给出诊断意见
  - report_node:     报告生成 Agent → 生成结构化诊断报告
  - qa_node:         知识问答 Agent → RAG 检索 + LLM 生成回答
  - summarize_node:  汇总输出 Agent → 将各节点结果整合为最终回复

节点是 LangGraph 中的可调用单元，接收 state 并返回 state 的部分更新。
"""

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts import (
    CHESTX_DIAGNOSIS_PROMPT,
    CHESTX_QA_PROMPT,
    CHESTX_REPORT_PROMPT,
)
from app.agent.tools.detection_tool import (
    DETECTION_TOOLS,
    clear_last_result,
    get_last_result,
)
from app.config.settings import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════
# 病灶英文→中文名称映射 & 临床紧急程度权重
# ══════════════════════════════════════════════════════════════

LESION_CN_MAP: dict[str, str] = {
    "Atelectasis": "肺不张",
    "Calcification": "钙化",
    "Consolidation": "实变",
    "Effusion": "胸腔积液",
    "Emphysema": "肺气肿",
    "Fibrosis": "纤维化",
    "Fracture": "骨折",
    "Mass": "肿块",
    "Nodule": "结节",
    "Pneumothorax": "气胸",
}

# 临床紧急程度权重（值越大越紧急）
LESION_URGENCY: dict[str, int] = {
    "Pneumothorax": 100,   # 气胸 — 最紧急
    "Effusion": 90,        # 胸腔积液
    "Mass": 80,            # 肿块 — 高恶性风险
    "Fracture": 70,        # 骨折
    "Nodule": 60,          # 结节
    "Consolidation": 50,   # 实变
    "Atelectasis": 40,     # 肺不张
    "Fibrosis": 30,        # 纤维化
    "Emphysema": 20,       # 肺气肿
    "Calcification": 10,   # 钙化 — 通常良性
}

# 统一 RAG 相似度阈值
RAG_THRESHOLD = settings.RAG_SIMILARITY_THRESHOLD


def _pick_primary_lesion(class_counts: dict[str, int]) -> str | None:
    """按临床紧急程度选择最关键的病灶类型进行 RAG 检索

    优先选择紧急程度高且数量>0的病灶。同紧急程度时选数量多的。
    """
    if not class_counts:
        return None
    # 按 (紧急权重, 数量) 降序排列
    sorted_lesions = sorted(
        class_counts.items(),
        key=lambda kv: (LESION_URGENCY.get(kv[0], 0), kv[1]),
        reverse=True,
    )
    return sorted_lesions[0][0]


def _load_detection_from_db(state: dict) -> dict | None:
    """从数据库加载用户最近一次完成的检测结果（跨轮次兜底）

    当 state 中没有 detection_result 但用户消息明显在引用历史检测时，
    diagnosis_node 和 summarize_node 可调用此函数从 DB 恢复数据。
    """
    user_id = state.get("user_id", 0)
    task_id = state.get("task_id")
    if not user_id:
        return None

    try:
        from app.database.session import SessionLocal
        from app.entity.db_models import DetectionResult, DetectionTask

        db = SessionLocal()
        try:
            query = db.query(DetectionTask).filter(
                DetectionTask.user_id == user_id,
                DetectionTask.status == "completed",
            )
            if task_id:
                query = query.filter(DetectionTask.id == task_id)
            last_task = query.order_by(DetectionTask.created_at.desc()).first()

            if not last_task or not last_task.total_objects:
                return None

            details = (
                db.query(DetectionResult)
                .filter(DetectionResult.task_id == last_task.id)
                .all()
            )
            class_counts: dict[str, int] = {}
            detections_list: list[dict] = []
            for r in details:
                cls_name = r.class_name or "Unknown"
                class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
                detections_list.append({
                    "class_name": cls_name,
                    "class_name_cn": r.class_name_cn or cls_name,
                    "confidence": r.confidence or 0,
                })

            return {
                "total_objects": last_task.total_objects,
                "class_counts": class_counts,
                "inference_time": last_task.total_inference_time or 0,
                "status": "completed",
                "task_id": last_task.id,
                "detections": detections_list,
                "risk_level": last_task.risk_level or "unknown",
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning("从DB加载检测结果失败: %s", str(e))
        return None


def _build_user_identity_context(state: dict) -> str:
    """构建用户身份上下文，供 summarize_node 回答"我是谁"等问题

    从 state 和 DB 中提取：
      - 登录用户名
      - 绑定的患者档案（patient_code）
      - 用户角色（医生/患者/管理员）
    """
    user_id = state.get("user_id", 0)
    patient_profile_id = state.get("patient_profile_id")
    if not user_id:
        return ""

    try:
        from app.database.session import SessionLocal
        from app.entity.db_models import PatientProfile, User

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return ""

            parts = ["[系统数据] 当前用户信息："]
            parts.append(f"- 用户名：{user.username}")

            # 角色
            role_labels = {"admin": "管理员", "doctor": "医生", "patient": "患者"}
            role_cn = role_labels.get(user.user_type, user.user_type or "未知")
            parts.append(f"- 角色：{role_cn}")

            # 患者档案
            profile = None
            if patient_profile_id:
                profile = db.query(PatientProfile).filter(
                    PatientProfile.id == patient_profile_id
                ).first()
            elif user.user_type == "patient":
                profile = db.query(PatientProfile).filter(
                    PatientProfile.user_id == user_id
                ).first()

            if profile:
                parts.append(f"- 关联患者编号：{profile.patient_code}")
                if profile.real_name:
                    parts.append(f"- 患者姓名：{profile.real_name}")
                if profile.gender:
                    parts.append(f"- 性别：{profile.gender}")
                if profile.age:
                    parts.append(f"- 年龄：{profile.age}岁")

            return "\n".join(parts)
        finally:
            db.close()
    except Exception as e:
        logger.warning("构建用户身份上下文失败: %s", str(e))
        return ""


def _build_conversation_context(state: dict, max_turns: int = 5) -> str:
    """从 state.messages 中提取最近 N 轮对话上下文

    供 summarize_node 兜底分支使用，实现多轮对话检索。
    过滤掉系统注入的上下文标记和附件路径标记。
    """
    messages = state.get("messages", [])
    if not messages:
        return ""

    from langchain_core.messages import AIMessage, HumanMessage

    # 只取最近的 N 轮（一轮 = 用户消息 + AI 回复）
    recent_pairs: list[tuple[str, str]] = []
    temp_user = ""
    for msg in reversed(messages):
        if len(recent_pairs) >= max_turns:
            break
        if isinstance(msg, HumanMessage):
            content = msg.content if hasattr(msg, "content") else str(msg)
            # 过滤系统注入的上下文（[系统上下文]、[系统数据] 等标记）
            if content.startswith("[系统") or content.startswith("「"):
                # 尝试提取用户真正的问题
                for marker in ["现在用户问：「", "用户当前询问：「", "用户问：「"]:
                    if marker in content:
                        idx = content.find(marker) + len(marker)
                        end_idx = content.find("」", idx)
                        if end_idx > idx:
                            content = content[idx:end_idx]
                        else:
                            content = content[idx:]
                        break
            # 清理附件路径标记
            import re
            content = re.sub(r'\[附件(?:图片|多张图片|视频|ZIP)路径:[^\]]*\]', '', content).strip()
            if content:
                temp_user = content
        elif isinstance(msg, AIMessage):
            content = msg.content if hasattr(msg, "content") else str(msg)
            content = content.strip()
            if content and temp_user:
                recent_pairs.append((temp_user, content[:200]))  # AI 回复截断
                temp_user = ""

    if not recent_pairs:
        return ""

    # 反转回时间顺序
    recent_pairs.reverse()
    lines = []
    for i, (user_msg, ai_msg) in enumerate(recent_pairs, 1):
        lines.append(f"第{i}轮 - 用户: {user_msg}")
        lines.append(f"第{i}轮 - AI: {ai_msg}")
    return "\n".join(lines)


def _check_medical_context(conversation_context: str) -> bool:
    """检查对话上下文中是否包含医学/检测相关的话题

    如果对话历史中涉及胸片、检测、诊断等，则保持医学助手模式。
    """
    medical_keywords = [
        "胸片", "检测", "病灶", "诊断", "骨折", "结节", "肿块",
        "肺", "X光", "影像", "钙化", "气胸", "积液", "实变",
        "患者", "医生", "报告", "病史", "风险", "治疗",
    ]
    return any(kw in conversation_context for kw in medical_keywords)


# ══════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════


def _get_user_message(state: dict) -> str:
    """从 state 中提取最后一条用户消息"""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _get_patient_context(state: dict, db_session=None) -> str:
    """从数据库加载患者病史上下文"""
    patient_profile_id = state.get("patient_profile_id")
    user_id = state.get("user_id", 0)
    if not patient_profile_id:
        return ""

    try:
        if db_session is None:
            from app.database.session import SessionLocal
            db = SessionLocal()
        else:
            db = db_session
        try:
            from app.entity.db_models import (
                DetectionTask,
                MedicalRecord,
                PatientProfile,
            )

            profile = db.query(PatientProfile).filter(
                PatientProfile.id == patient_profile_id
            ).first()
            if not profile:
                return ""

            parts = [f"## 患者信息\n- 编号: {profile.patient_code}"]
            if profile.real_name:
                parts.append(f"- 姓名: {profile.real_name}")
            if profile.gender:
                parts.append(f"- 性别: {profile.gender}")
            if profile.age:
                parts.append(f"- 年龄: {profile.age}岁")
            parts.append("")

            # 历史病例
            records = (
                db.query(MedicalRecord)
                .filter(MedicalRecord.patient_profile_id == profile.id)
                .order_by(MedicalRecord.visit_date.desc().nullslast())
                .limit(5)
                .all()
            )
            if records:
                parts.append("## 历史病例")
                for r in records:
                    parts.append(
                        f"- [{r.visit_date}] {r.record_type}: "
                        f"主诉={r.chief_complaint or '无'}, "
                        f"诊断={r.diagnosis or '无'}"
                    )
                parts.append("")

            # 历史检测
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
            if tasks:
                parts.append("## 历史检测结果")
                for t in tasks:
                    parts.append(
                        f"- 检测ID={t.id} ({t.created_at}): "
                        f"检出{t.total_objects}个病灶, "
                        f"风险={t.risk_level or '未评估'}"
                    )
                parts.append("")

            return "\n".join(parts)
        finally:
            if db_session is None:
                db.close()
    except Exception as e:
        logger.warning("加载患者上下文失败: %s", str(e))
        return ""


# ══════════════════════════════════════════════════════════════
# 1. 病灶检测 Agent 节点
# ══════════════════════════════════════════════════════════════


async def detection_node(state: dict, llm: ChatOpenAI = None) -> dict:
    """病灶检测 Agent 节点

    职责：调用 YOLO 模型对上传的胸片进行病灶检测，并持久化到数据库。

    流程：
      1. 从消息中提取图片路径
      2. 调用 detect_single_image / detect_batch_images 工具
      3. 持久化检测任务到 DB
      4. 返回检测摘要
    """
    user_msg = _get_user_message(state)
    detection_result = {}
    task_id = None
    user_id = state.get("user_id", 0)
    patient_profile_id = state.get("patient_profile_id")

    # ── 提取图片路径 ──
    image_path = state.get("image_path", "")
    if not image_path:
        # 从消息中自动提取
        for marker in ["[附件图片路径:", "[附件多张图片路径:"]:
            if marker in user_msg:
                start = user_msg.find(marker) + len(marker)
                end = user_msg.find("]", start)
                if end > start:
                    image_path = user_msg[start:end].strip()
                    break

    if not image_path:
        return {
            "detection_result": {"error": "未找到待检测的胸片，请先上传胸片图像。"},
            "next_agent": "summarize",
        }

    # ── 调用检测工具 ──
    try:
        from app.agent.tools.detection_tool import detect_single_image

        # 执行检测
        result_str = detect_single_image.invoke({"image_path": image_path})
        detection_data = json.loads(result_str) if isinstance(result_str, str) else result_str

        if "error" in detection_data:
            detection_result = detection_data
        else:
            detection_result = {
                "total_objects": detection_data.get("total_objects", 0),
                "class_counts": detection_data.get("class_counts", {}),
                "inference_time": detection_data.get("inference_time", 0),
                "status": "completed",
            }

            # 获取完整结果（含标注图）
            full_result = get_last_result()
            if full_result:
                detection_result["annotated_image_url"] = full_result.get(
                    "annotated_image_url", ""
                )
                detection_result["task_id"] = full_result.get("task_id")
                task_id = full_result.get("task_id")
                detection_result["detections"] = full_result.get("detections", [])

                # ── 持久化检测任务到数据库 ──
                _persist_detection_task(
                    user_id=user_id,
                    patient_profile_id=patient_profile_id,
                    image_path=image_path,
                    detection_result=full_result,
                    task_id=task_id,
                )

        clear_last_result()

        logger.info(
            "检测节点完成: 病灶数=%d, task_id=%s",
            detection_result.get("total_objects", 0),
            task_id,
        )
    except Exception as e:
        logger.error("检测节点异常: %s", str(e), exc_info=True)
        detection_result = {"error": f"检测失败: {str(e)}"}

    return {
        "detection_result": detection_result,
        "task_id": task_id,
        "next_agent": "diagnosis",  # 检测完成后自动进入诊断
    }


def _persist_detection_task(
    user_id: int,
    patient_profile_id: Any,
    image_path: str,
    detection_result: dict,
    task_id: Any = None,
):
    """将检测结果持久化到数据库"""
    try:
        from app.database.session import SessionLocal
        from app.entity.db_models import DetectionScene, DetectionTask, PatientProfile

        db = SessionLocal()
        try:
            # 验证 patient_profile_id
            profile_id = None
            if patient_profile_id:
                profile = db.query(PatientProfile).filter(
                    PatientProfile.id == patient_profile_id
                ).first()
                if profile:
                    profile_id = profile.id

            # ⭐ 关键修复：获取胸片检测场景 ID（scene_id 是 NOT NULL 字段）
            #  之前缺失导致所有检测入库静默失败，二次查询返回了错误的历史记录
            scene = db.query(DetectionScene).filter(
                DetectionScene.name == "chest_xray",
                DetectionScene.is_active == True,
            ).first()
            if not scene:
                # 兜底：取任意 medical 分类的活跃场景
                scene = db.query(DetectionScene).filter(
                    DetectionScene.category == "medical",
                    DetectionScene.is_active == True,
                ).first()
            if not scene:
                # 最终兜底：取第一个活跃场景
                scene = db.query(DetectionScene).filter(
                    DetectionScene.is_active == True,
                ).first()

            scene_id = scene.id if scene else 1

            task = DetectionTask(
                user_id=user_id,
                scene_id=scene_id,  # ⭐ 之前缺失，导致入库静默失败
                patient_profile_id=profile_id,
                task_type="single",
                status="completed",
                total_images=1,
                total_objects=detection_result.get("total_objects", 0),
                total_inference_time=detection_result.get("inference_time", 0),
                risk_level=_estimate_risk_level(detection_result),
                conf_threshold=0.25,
                iou_threshold=0.45,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            logger.info("检测任务已入库: task_id=%d, user=%d, scene=%d", task.id, user_id, scene_id)
        finally:
            db.close()
    except Exception as e:
        logger.warning("检测任务入库失败（不影响主流程）: %s", str(e))


def _estimate_risk_level(detection_result: dict) -> str:
    """根据检测结果估算风险等级"""
    class_counts = detection_result.get("class_counts", {})
    # 危急病灶
    critical_types = {"Pneumothorax": "气胸", "Effusion": "胸腔积液"}
    high_risk_types = {"Mass": "肿块", "Nodule": "结节", "Fracture": "骨折"}

    for ct in critical_types:
        if class_counts.get(ct, 0) > 0:
            return "critical"
    for ht in high_risk_types:
        if class_counts.get(ht, 0) > 0:
            return "high"
    total = detection_result.get("total_objects", 0)
    if total > 3:
        return "medium"
    if total > 0:
        return "low"
    return "none"


# ══════════════════════════════════════════════════════════════
# 2. 综合诊断 Agent 节点
# ══════════════════════════════════════════════════════════════


async def diagnosis_node(state: dict, llm: ChatOpenAI = None) -> dict:
    """综合诊断 Agent 节点

    职责：结合检测结果 + 患者病史 + RAG医学知识，给出综合诊断意见。

    分析维度：
      - 病灶特征总结
      - 结合病史的对比分析
      - RAG知识增强鉴别诊断
      - 风险等级评估
      - 进一步检查建议
    """
    user_msg = _get_user_message(state)
    detection_result = state.get("detection_result", {})
    knowledge_context = ""

    # ── 构建诊断上下文 ──
    # ⭐ 关键修复：detection_result 为空时，尝试从 DB 加载历史检测结果
    if not detection_result or detection_result.get("total_objects", -1) < 0:
        db_detection = _load_detection_from_db(state)
        if db_detection:
            detection_result = db_detection
            logger.info(
                "诊断节点从DB加载历史检测: total=%d, classes=%s",
                detection_result.get("total_objects", 0),
                detection_result.get("class_counts", {}),
            )
        else:
            return {
                "diagnosis_result": {
                    "findings": "尚未进行胸片检测，请先上传胸片进行病灶检测。",
                    "risk_level": "unknown",
                },
                "next_agent": "summarize",
            }

    # 患者病史上下文
    patient_context = _get_patient_context(state)

    # ── RAG 知识增强 ──
    try:
        from app.rag.retriever import knowledge_retriever

        # 根据检出的病灶类型检索相关知识（使用中文名 + 临床紧急程度排序）
        class_counts = detection_result.get("class_counts", {})
        primary_lesion_en = _pick_primary_lesion(class_counts)
        if primary_lesion_en:
            # 使用中文病灶名构建 RAG 查询，提升中文知识库检索效果
            primary_lesion_cn = LESION_CN_MAP.get(primary_lesion_en, primary_lesion_en)
            rag_query = f"{primary_lesion_cn} 胸部X光 影像学特征 诊断 鉴别诊断 临床建议"
            rag_results = knowledge_retriever.search_with_threshold(
                rag_query, top_k=3, threshold=RAG_THRESHOLD,
            )
            if rag_results:
                knowledge_context = "\n\n---\n\n".join(
                    f"[知识库: {r.get('metadata', {}).get('source', '未知')}]\n{r.get('content', '')[:400]}"
                    for r in rag_results
                )
    except Exception as e:
        logger.warning("诊断节点 RAG 检索失败: %s", str(e))

    # 检测结果摘要
    total = detection_result.get("total_objects", 0)
    class_counts = detection_result.get("class_counts", {})
    lesion_summary = "\n".join(
        f"- {name}: {count}处" for name, count in sorted(class_counts.items())
    ) if class_counts else "无病灶检出"

    # ── 构建 Prompt（注入RAG知识）──
    detection_context = f"检出病灶总数: {total}\n病灶分布:\n{lesion_summary}"
    if knowledge_context:
        detection_context += f"\n\n## 相关医学知识（来自知识库）\n{knowledge_context}"

    diagnosis_prompt = CHESTX_DIAGNOSIS_PROMPT.format(
        patient_context=patient_context or "无历史记录",
        detection_summary=detection_context,
        user_question=user_msg,
    )

    # ── 调用 LLM 生成诊断 ──
    diagnosis_text = ""
    try:
        if llm is None:
            from app.agent.detection_agent import create_llm
            llm = create_llm()

        response = await llm.ainvoke([
            SystemMessage(content="你是一位资深放射科主任医师，请给出专业、严谨的诊断意见。"),
            HumanMessage(content=diagnosis_prompt),
        ])
        diagnosis_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error("诊断节点 LLM 调用失败: %s", str(e))
        diagnosis_text = f"诊断分析暂时不可用（{str(e)}），请稍后重试。"

    # ── 提取风险等级（更健壮的匹配逻辑）──
    risk_level = "medium"
    risk_patterns = [
        ("极高风险", "critical"), ("critical", "critical"),
        ("高风险", "high"), ("high", "high"),
        ("中风险", "medium"), ("medium", "medium"),
        ("低风险", "low"), ("low", "low"),
        ("无风险", "none"), ("none", "none"),
    ]
    diagnosis_lower = diagnosis_text.lower()
    for pattern, level in risk_patterns:
        if pattern in diagnosis_lower:
            risk_level = level
            break

    # ── 兜底：如果 LLM 未在文本中标注风险等级，根据检测结果推断 ──
    if risk_level == "medium" and not any(p in diagnosis_text for p in ["中风险", "medium"]):
        risk_level = _estimate_risk_level(detection_result)
        logger.info("诊断节点未显式标注风险，根据检测结果推断为: %s", risk_level)

    logger.info("诊断节点完成: risk=%s, text_len=%d", risk_level, len(diagnosis_text))

    return {
        "diagnosis_result": {
            "findings": diagnosis_text,
            "risk_level": risk_level,
        },
        "next_agent": "summarize",
    }


# ══════════════════════════════════════════════════════════════
# 3. 报告生成 Agent 节点
# ══════════════════════════════════════════════════════════════


async def report_node(state: dict, llm: ChatOpenAI = None) -> dict:
    """报告生成 Agent 节点

    职责：基于检测结果 + 诊断意见 + 患者信息，生成结构化诊断报告。

    报告结构：
      1. 基本信息（患者编号、检查时间等）
      2. 影像所见（病灶详情）
      3. 诊断意见
      4. 风险评级
      5. 建议
    """
    detection_result = state.get("detection_result", {})
    diagnosis_result = state.get("diagnosis_result", {})
    patient_context = _get_patient_context(state)

    if not detection_result or detection_result.get("total_objects", -1) < 0:
        return {
            "report_result": "暂无检测结果，请先进行胸片检测后再生成报告。",
            "next_agent": "summarize",
        }

    # ── 构建报告上下文 ──
    total = detection_result.get("total_objects", 0)
    class_counts = detection_result.get("class_counts", {})
    lesion_detail = "\n".join(
        f"| {name} | {count}处 |"
        for name, count in sorted(class_counts.items())
    ) if class_counts else "| 无异常 | 0处 |"

    diagnosis_text = diagnosis_result.get("findings", "暂无诊断意见")

    report_prompt = CHESTX_REPORT_PROMPT.format(
        patient_context=patient_context or "无患者信息",
        detection_detail=f"检出病灶总数: {total}\n\n| 病灶类型 | 数量 |\n|---------|------|\n{lesion_detail}",
        diagnosis_opinion=diagnosis_text,
    )

    # ── 调用 LLM 生成报告 ──
    report_text = ""
    try:
        if llm is None:
            from app.agent.detection_agent import create_llm
            llm = create_llm()

        response = await llm.ainvoke([
            SystemMessage(content="你是一位放射科报告撰写专家，请生成专业、规范的影像诊断报告。"),
            HumanMessage(content=report_prompt),
        ])
        report_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error("报告节点 LLM 调用失败: %s", str(e))
        report_text = f"# 诊断报告\n\n报告生成失败: {str(e)}"

    logger.info("报告节点完成: text_len=%d", len(report_text))

    return {
        "report_result": report_text,
        "next_agent": "summarize",
    }


# ══════════════════════════════════════════════════════════════
# 4. 知识问答 Agent 节点
# ══════════════════════════════════════════════════════════════


async def qa_node(state: dict, llm: ChatOpenAI = None) -> dict:
    """知识问答 Agent 节点

    职责：使用 RAG 检索医学知识库，结合 LLM 生成专业回答。

    流程：
      1. 语义检索知识库（Pgvector）
      2. 将检索结果注入 Prompt
      3. LLM 基于知识生成回答
    """
    user_msg = _get_user_message(state)
    knowledge_sources = []
    has_knowledge = False

    # ── RAG 检索 ──
    try:
        from app.rag.retriever import knowledge_retriever

        results = knowledge_retriever.search_with_threshold(
            user_msg, top_k=3, threshold=RAG_THRESHOLD,
        )
        if results:
            has_knowledge = True
            knowledge_sources = [
                {
                    "source": r.get("metadata", {}).get("source", "未知"),
                    "content": r.get("content", "")[:300],
                    "similarity": r.get("similarity", 0),
                }
                for r in results
            ]
    except Exception as e:
        logger.warning("RAG 检索失败: %s", str(e))

    # ── 构建 Prompt ──
    if has_knowledge:
        knowledge_context = "\n\n---\n\n".join(
            f"[来源: {s['source']}, 相关度: {s['similarity']:.2f}]\n{s['content']}"
            for s in knowledge_sources
        )
    else:
        knowledge_context = "（知识库中暂无直接相关内容，请基于你的医学知识回答）"

    qa_prompt = CHESTX_QA_PROMPT.format(
        knowledge_context=knowledge_context,
        user_question=user_msg,
    )

    # ── 调用 LLM 生成回答 ──
    answer_text = ""
    try:
        if llm is None:
            from app.agent.detection_agent import create_llm
            llm = create_llm()

        response = await llm.ainvoke([
            SystemMessage(content="你是胸部X光影像医学专家，回答要专业、准确、简洁。"),
            HumanMessage(content=qa_prompt),
        ])
        answer_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error("QA 节点 LLM 调用失败: %s", str(e))
        answer_text = f"抱歉，知识问答服务暂时不可用（{str(e)}）。"

    # 添加知识来源标注
    if has_knowledge:
        answer_text += "\n\n---\n📚 **知识来源**：胸部X光医学知识库"

    logger.info("QA 节点完成: has_knowledge=%s, text_len=%d", has_knowledge, len(answer_text))

    return {
        "qa_result": answer_text,
        "knowledge_sources": knowledge_sources,
        "has_knowledge": has_knowledge,
        "next_agent": "summarize",
    }


# ══════════════════════════════════════════════════════════════
# 5. 汇总输出 Agent 节点
# ══════════════════════════════════════════════════════════════


async def summarize_node(state: dict, llm: ChatOpenAI = None) -> dict:
    """汇总输出节点

    职责：将各 Agent 的产出整合为面向用户的最终回复。

    根据 next_agent 的来源决定输出内容：
      - 来自 detection → 输出检测结果摘要
      - 来自 diagnosis → 输出综合诊断意见
      - 来自 report   → 输出完整报告
      - 来自 qa       → 输出知识问答结果
      - 其他（含身份/简单回顾）→ 由 LLM 根据 state + 用户上下文生成回复
    """
    final_response = ""
    knowledge_sources = state.get("knowledge_sources", [])
    has_knowledge = state.get("has_knowledge", False)

    # 检查各节点的产出，按优先级取
    qa_result = state.get("qa_result", "")
    report_result = state.get("report_result", "")
    diagnosis_result = state.get("diagnosis_result", {})
    detection_result = state.get("detection_result", {})

    if qa_result:
        final_response = qa_result
    elif report_result:
        final_response = report_result
    elif diagnosis_result and diagnosis_result.get("findings"):
        diag_text = diagnosis_result.get("findings", "")
        risk = diagnosis_result.get("risk_level", "medium")
        risk_labels = {"critical": "🔴 极高风险", "high": "🟠 高风险", "medium": "🟡 中风险", "low": "🟢 低风险"}
        risk_label = risk_labels.get(risk, risk)

        # 追加检测摘要
        total = detection_result.get("total_objects", 0)
        if total > 0:
            class_counts = detection_result.get("class_counts", {})
            lesion_str = "、".join(f"{k}×{v}" for k, v in sorted(class_counts.items()))
            final_response = (
                f"## 🔬 检测结果\n"
                f"共检出 **{total}** 个病灶：{lesion_str}\n\n"
                f"## 📋 综合诊断\n{diag_text}\n\n"
                f"**风险评级**: {risk_label}"
            )
        else:
            final_response = f"## 📋 综合诊断\n{diag_text}\n\n**风险评级**: {risk_label}"
    elif detection_result and detection_result.get("total_objects", -1) >= 0:
        total = detection_result.get("total_objects", 0)
        class_counts = detection_result.get("class_counts", {})
        inference_time = detection_result.get("inference_time", 0)

        if total > 0:
            lesion_str = "、".join(f"{k}×{v}" for k, v in sorted(class_counts.items()))
            final_response = (
                f"## 🔬 胸片检测完成\n\n"
                f"检出 **{total}** 个病灶：{lesion_str}\n\n"
                f"⏱️ 推理耗时: {inference_time:.0f}ms\n\n"
                '> 💡 如需进一步诊断分析，请发送"帮我分析诊断"或"生成报告"。'
            )
        else:
            final_response = (
                f"## ✅ 胸片检测完成\n\n"
                f"未检出明显病灶，胸片表现基本正常。\n\n"
                f"⏱️ 推理耗时: {inference_time:.0f}ms\n\n"
                f"> ⚠️ 本结果仅供参考，请结合临床症状综合判断。"
            )
    else:
        # ══════════════════════════════════════════════════════
        # 兜底分支：所有关键词不匹配 → 普通大模型问答模式
        #
        # 设计思路：
        #   1. 判断当前对话是否在医学/检测上下文中
        #      - 有历史检测数据 → 保留医学助手定位
        #      - 纯普通对话 → 切换为通用 AI 助手
        #   2. 始终注入对话历史上下文（多轮对话检索）
        #   3. 始终注入用户身份上下文（回答"我是谁"）
        # ══════════════════════════════════════════════════════
        user_msg = _get_user_message(state)
        user_id = state.get("user_id", 0)

        # ── ① 构建对话历史上下文（多轮对话检索）──
        conversation_context = _build_conversation_context(state)

        # ── ② 构建用户身份上下文 ──
        user_context = _build_user_identity_context(state)

        # ── ③ 尝试从 DB 加载检测结果 ──
        db_detection = _load_detection_from_db(state)
        detection_context = ""
        has_medical_context = False
        if db_detection and db_detection.get("total_objects", 0) > 0:
            has_medical_context = True
            total = db_detection.get("total_objects", 0)
            class_counts = db_detection.get("class_counts", {})
            lesion_items = "、".join(
                f"{LESION_CN_MAP.get(k, k)}×{v}" for k, v in sorted(class_counts.items())
            )
            detection_context = (
                f"\n[系统数据] 用户最近一次胸片检测结果：共{total}个病灶 → {lesion_items}。"
            )

        # ── ④ 检查对话历史中是否有医学检测相关上下文 ──
        if not has_medical_context and conversation_context:
            has_medical_context = _check_medical_context(conversation_context)

        try:
            if llm is None:
                from app.agent.detection_agent import create_llm
                llm = create_llm()

            # ── ⑤ 根据上下文类型选择 System Prompt ──
            if has_medical_context:
                # 医学上下文模式：保持医学助手定位
                system_parts = [
                    "你是胸部X光影像AI诊断助手，同时也能进行一般对话。",
                    "请用中文简洁回复用户。",
                ]
                if user_context:
                    system_parts.append(user_context)
                if detection_context:
                    system_parts.append(detection_context)
                if conversation_context:
                    system_parts.append(f"\n## 对话历史上下文\n{conversation_context}")
                system_parts.append(
                    "如果用户询问身份信息，请根据上下文中的用户信息直接回答。"
                    "如果用户询问检测结果，请基于系统数据中的检测结果简洁列出病灶。"
                    "如果是与医学/检测无关的一般对话，请自然地回答用户问题。"
                )
            else:
                # 纯普通对话模式：切换为通用 AI 助手
                system_parts = [
                    "你是一个智能AI助手，可以进行各种类型的对话和问答。",
                    "请用中文自然、友好地回复用户。",
                    "你有以下对话上下文信息，请在回答时参考：",
                ]
                if user_context:
                    system_parts.append(f"\n## 当前用户信息\n{user_context}")
                if conversation_context:
                    system_parts.append(f"\n## 对话历史\n{conversation_context}")
                system_parts.append(
                    "\n## 回复原则\n"
                    "- 如果用户询问身份信息（如'我是谁'），根据用户信息直接回答。\n"
                    "- 对于一般知识问答、闲聊等，自由自然地回答。\n"
                    "- 不要主动提及医学检测，除非用户明确询问。\n"
                    "- 保持回答简洁、准确、有帮助。"
                )

            system_content = "\n".join(system_parts)

            logger.info(
                "Summarize 兜底模式: medical=%s, user_ctx=%s, conv_ctx=%s, det_ctx=%s",
                has_medical_context,
                bool(user_context),
                bool(conversation_context),
                bool(detection_context),
            )

            response = await llm.ainvoke([
                SystemMessage(content=system_content),
                HumanMessage(content=user_msg),
            ])
            final_response = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error("汇总节点 LLM 调用失败: %s", str(e))
            final_response = "请问有什么可以帮您？您可以上传胸片进行AI辅助检测，或咨询胸部X光相关的医学知识。"

    logger.info("汇总节点完成: text_len=%d", len(final_response))

    return {
        "final_response": final_response,
        "knowledge_sources": knowledge_sources,
        "has_knowledge": has_knowledge,
    }
