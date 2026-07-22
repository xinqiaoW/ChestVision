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
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts import (
    CASE_HISTORY_ANALYSIS_PROMPT,
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

            # 未检出病灶也是有效的已完成检测，同样可生成报告。
            if not last_task:
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
                detection_result["annotated_image_base64"] = full_result.get(
                    "annotated_image_base64", ""
                )
                detection_result["detections"] = full_result.get("detections", [])

                # ── 持久化检测任务到数据库 ──
                task_id = _persist_detection_task(
                    user_id=user_id,
                    patient_profile_id=patient_profile_id,
                    image_path=image_path,
                    detection_result=full_result,
                )
                detection_result["task_id"] = task_id
                if task_id:
                    detection_result["annotated_image_url"] = (
                        f"/api/detection/image/{task_id}"
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
):
    """将检测结果持久化到数据库"""
    try:
        from datetime import datetime

        from app.database.session import SessionLocal
        from app.entity.db_models import (
            DetectionResult,
            DetectionScene,
            DetectionTask,
            ModelVersion,
            PatientProfile,
        )

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

            scene = (
                db.query(DetectionScene)
                .filter(DetectionScene.name == "chest_xray")
                .first()
            )
            if not scene:
                logger.warning("检测场景 chest_xray 不存在，跳过检测任务入库")
                return None

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

            task = DetectionTask(
                user_id=user_id,
                scene_id=scene.id,
                model_version_id=model_version.id if model_version else None,
                patient_profile_id=profile_id,
                task_type="single",
                status="completed",
                total_images=1,
                total_objects=detection_result.get("total_objects", 0),
                total_inference_time=detection_result.get("inference_time", 0),
                risk_level=_estimate_risk_level(detection_result),
                conf_threshold=0.25,
                iou_threshold=0.45,
                completed_at=datetime.now(),
            )
            db.add(task)
            db.flush()

            for detected_object in detection_result.get("detections", []):
                db.add(
                    DetectionResult(
                        task_id=task.id,
                        image_path=image_path,
                        annotated_image_url=detection_result.get(
                            "annotated_image_path"
                        ),
                        class_name=detected_object.get("class_name", "Unknown"),
                        class_name_cn=detected_object.get("class_name_cn"),
                        class_id=detected_object.get("class_id", -1),
                        confidence=detected_object.get("confidence", 0),
                        bbox=detected_object.get("bbox", []),
                        inference_time=detection_result.get("inference_time", 0),
                    )
                )

            db.commit()
            db.refresh(task)
            logger.info("检测任务已入库: task_id=%d, user=%d", task.id, user_id)
            return task.id
        finally:
            db.close()
    except Exception as e:
        logger.warning("检测任务入库失败（不影响主流程）: %s", str(e))
        return None


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
        detection_result = _load_detection_from_db(state) or {}

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
        "task_id": detection_result.get("task_id") or state.get("task_id"),
        "next_agent": "summarize",
    }


# ══════════════════════════════════════════════════════════════
# 4. 历史病例分析 Agent 节点
# ══════════════════════════════════════════════════════════════


def _case_analysis_fallback(profile, records: list, tasks: list) -> str:
    """LLM 不可用或输出越界时，基于数据库事实生成安全的计划框架。"""
    timeline = []
    for record in records:
        visit_time = record.visit_date or record.created_at
        date_text = visit_time.strftime("%Y-%m-%d") if visit_time else "日期未记录"
        facts = [
            f"类型：{record.record_type or '未记录'}",
            f"主诉：{record.chief_complaint or '未记录'}",
            f"诊断：{json.dumps(record.diagnosis, ensure_ascii=False) if record.diagnosis else '未记录'}",
        ]
        if record.treatment_plan:
            facts.append(f"历史治疗方案记录：{record.treatment_plan}")
        timeline.append(f"- {date_text}（病例 #{record.id}）：" + "；".join(facts))

    detections = []
    for task in tasks:
        task_time = task.completed_at or task.created_at
        date_text = task_time.strftime("%Y-%m-%d") if task_time else "日期未记录"
        lesion_counts: dict[str, int] = {}
        for result in task.results or []:
            lesion = result.class_name_cn or LESION_CN_MAP.get(
                result.class_name, result.class_name
            )
            lesion_counts[lesion] = lesion_counts.get(lesion, 0) + 1
        lesion_text = "、".join(
            f"{name}×{count}" for name, count in sorted(lesion_counts.items())
        ) or "未记录具体病灶"
        detections.append(
            f"- {date_text}（检测任务 #{task.id}）：{lesion_text}；风险等级：{task.risk_level or '未记录'}"
        )

    timeline_text = "\n".join(timeline) or "- 暂无历史病例记录。"
    detection_text = "\n".join(detections) or "- 暂无历史胸片检测记录。"
    allergies = profile.allergies or "未记录（制定方案前需核实）"
    return (
        "### 病例时间线与已知事实\n"
        f"患者编号：{profile.patient_code}；过敏史：{allergies}。\n\n"
        f"{timeline_text}\n\n历史胸片检测：\n{detection_text}\n\n"
        "### 趋势、风险与冲突\n"
        "现有信息仅能用于整理既往记录；需由医生对照历次症状、影像和检查结果，判断病情变化，"
        "并核实诊断是否重复或冲突。过敏史未记录时，不应直接形成用药决定。\n\n"
        "### 诊疗计划框架\n"
        "1. 先复核当前症状、生命体征和本次就诊目标；\n"
        "2. 将当前影像与历史胸片逐次对照，确认病灶是否新增、扩大或缓解；\n"
        "3. 复核历史方案的执行情况、疗效和不良反应；\n"
        "4. 由临床医生结合面诊和必要检查，与患者共同确定治疗、复查和转诊安排。\n\n"
        "### 需补充或复核的信息\n"
        "需补充当前症状持续时间、基础疾病、完整用药与过敏史、相关化验及既往影像原片。\n\n"
        "### 随访与危险信号\n"
        "建议按临床医生确定的时间随访；若出现突发或加重的呼吸困难、胸痛、咯血、意识改变等，"
        "应立即就医。\n\n"
        "> 本分析仅供临床决策支持，具体方案由有资质医师面诊后确定。"
    )


async def case_analysis_node(state: dict, llm: ChatOpenAI = None) -> dict:
    """读取当前授权患者的病例与检测历史，形成可追溯的诊疗计划参考。"""
    from app.database.session import SessionLocal
    from app.entity.db_models import (
        DetectionTask,
        DoctorPatientRelation,
        MedicalRecord,
        PatientProfile,
        User,
    )

    user_id = state.get("user_id")
    patient_profile_id = state.get("patient_profile_id")
    user_request = _get_user_message(state)
    db = SessionLocal()

    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {
                "case_analysis_result": {
                    "status": "error",
                    "analysis": "当前用户不存在，无法读取病例历史。",
                },
                "next_agent": "summarize",
            }

        # 患者无需手动选择，自动使用自己的档案；医生和管理员必须明确选中患者。
        if not patient_profile_id and user.user_type == "patient":
            own_profile = (
                db.query(PatientProfile)
                .filter(
                    PatientProfile.user_id == user.id,
                    PatientProfile.is_active.is_(True),
                )
                .first()
            )
            patient_profile_id = own_profile.id if own_profile else None

        if not patient_profile_id:
            return {
                "case_analysis_result": {
                    "status": "patient_required",
                    "analysis": "请先在对话页选择患者，再请求基于历史病例制定诊疗计划。",
                },
                "next_agent": "summarize",
            }

        profile = (
            db.query(PatientProfile)
            .filter(
                PatientProfile.id == patient_profile_id,
                PatientProfile.is_active.is_(True),
            )
            .first()
        )
        if not profile:
            return {
                "case_analysis_result": {
                    "status": "not_found",
                    "analysis": "未找到所选患者档案，请重新选择患者。",
                },
                "next_agent": "summarize",
            }

        authorized = user.user_type == "admin" or user.is_superuser
        if user.user_type == "patient":
            authorized = profile.user_id == user.id
        elif user.user_type == "doctor" and not authorized:
            authorized = (
                db.query(DoctorPatientRelation.id)
                .filter(
                    DoctorPatientRelation.doctor_id == user.id,
                    DoctorPatientRelation.patient_id == profile.user_id,
                    DoctorPatientRelation.relation_status == "active",
                )
                .first()
                is not None
            )

        if not authorized:
            logger.warning(
                "用户 %s 尝试读取未授权患者档案 %s", user.id, patient_profile_id
            )
            return {
                "case_analysis_result": {
                    "status": "forbidden",
                    "analysis": "您无权读取该患者的历史病例，请选择已建立医患关系的患者。",
                },
                "next_agent": "summarize",
            }

        records = (
            db.query(MedicalRecord)
            .filter(MedicalRecord.patient_profile_id == profile.id)
            .order_by(
                MedicalRecord.visit_date.desc().nullslast(),
                MedicalRecord.created_at.desc(),
            )
            .limit(8)
            .all()
        )
        tasks = (
            db.query(DetectionTask)
            .filter(
                DetectionTask.patient_profile_id == profile.id,
                DetectionTask.status == "completed",
            )
            .order_by(
                DetectionTask.completed_at.desc().nullslast(),
                DetectionTask.created_at.desc(),
            )
            .limit(5)
            .all()
        )

        fallback = _case_analysis_fallback(profile, records, tasks)
        record_context = [
            {
                "id": record.id,
                "visit_date": (record.visit_date or record.created_at).isoformat()
                if (record.visit_date or record.created_at)
                else None,
                "record_type": record.record_type,
                "chief_complaint": record.chief_complaint,
                "present_illness": record.present_illness,
                "past_history": record.past_history,
                "family_history": record.family_history,
                "physical_examination": record.physical_examination,
                "auxiliary_exams": record.auxiliary_exams,
                "diagnosis": record.diagnosis,
                "historical_treatment_plan": record.treatment_plan,
                "historical_prescription": record.prescription,
                "doctor_notes": record.doctor_notes,
                "status": record.record_status,
            }
            for record in records
        ]
        detection_context = []
        for task in tasks:
            detection_context.append(
                {
                    "task_id": task.id,
                    "completed_at": (task.completed_at or task.created_at).isoformat()
                    if (task.completed_at or task.created_at)
                    else None,
                    "total_objects": task.total_objects,
                    "risk_level": task.risk_level,
                    "analysis_report": task.analysis_report,
                    "analysis_suggestion": task.analysis_suggestion,
                    "lesions": [
                        {
                            "name": result.class_name_cn
                            or LESION_CN_MAP.get(result.class_name, result.class_name),
                            "confidence": result.confidence,
                        }
                        for result in (task.results or [])
                    ],
                }
            )

        patient_context = {
            "patient_code": profile.patient_code,
            "age": profile.age,
            "gender": profile.gender,
            "blood_type": profile.blood_type,
            "allergies": profile.allergies,
            "department": profile.department,
            "notes": profile.notes,
        }
        prompt = CASE_HISTORY_ANALYSIS_PROMPT.format(
            user_request=user_request,
            patient_context=json.dumps(patient_context, ensure_ascii=False, default=str),
            record_context=json.dumps(record_context, ensure_ascii=False, default=str),
            detection_context=json.dumps(detection_context, ensure_ascii=False, default=str),
        )

        analysis = fallback
        try:
            if llm is None:
                from app.agent.detection_agent import create_llm
                llm = create_llm()
            response = await llm.ainvoke(
                [
                    SystemMessage(
                        content="你是历史病例分析 Agent，只能依据给定数据库记录提供临床决策支持。"
                    ),
                    HumanMessage(content=prompt),
                ]
            )
            candidate = (
                response.content if hasattr(response, "content") else str(response)
            )
            required_sections = [
                "病例时间线与已知事实",
                "趋势、风险与冲突",
                "诊疗计划框架",
                "需补充或复核的信息",
                "随访与危险信号",
            ]
            contains_dose = bool(
                re.search(
                    r"\d+(?:\.\d+)?\s*(?:mg|ml|毫克|毫升|片|粒|次/日)",
                    candidate,
                    re.I,
                )
                or re.search(r"建议(?:使用|给予|服用)|首选.{0,12}药", candidate)
            )
            if (
                len(candidate.strip()) >= 300
                and all(section in candidate for section in required_sections)
                and not contains_dose
            ):
                analysis = candidate.strip()
            else:
                logger.warning("历史病例分析输出未通过安全/结构校验，使用事实兜底")
        except Exception as exc:
            logger.error("历史病例分析 Agent 调用失败: %s", str(exc), exc_info=True)

        return {
            "case_analysis_result": {
                "status": "completed",
                "patient_code": profile.patient_code,
                "record_count": len(records),
                "detection_count": len(tasks),
                "referenced_record_ids": [record.id for record in records],
                "referenced_task_ids": [task.id for task in tasks],
                "analysis": analysis,
            },
            "next_agent": "summarize",
        }
    except Exception as exc:
        logger.error("历史病例分析节点失败: %s", str(exc), exc_info=True)
        return {
            "case_analysis_result": {
                "status": "error",
                "analysis": "历史病例读取失败，请稍后重试。",
            },
            "next_agent": "summarize",
        }
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# 5. 知识问答 Agent 节点
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
      - 其他          → 由 LLM 根据 state 自动生成回复
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
        # 无特殊产出，由 LLM 生成通用回复
        user_msg = _get_user_message(state)
        try:
            if llm is None:
                from app.agent.detection_agent import create_llm
                llm = create_llm()

            response = await llm.ainvoke([
                SystemMessage(content=(
                    "你是胸部X光影像AI诊断助手。请用中文简洁回复用户。"
                    "如果用户没有上传胸片，引导用户上传胸片进行检测。"
                )),
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
