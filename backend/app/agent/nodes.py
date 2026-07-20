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
from app.core.logger import get_logger

logger = get_logger(__name__)

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
        from app.entity.db_models import DetectionTask, PatientProfile

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

            task = DetectionTask(
                user_id=user_id,
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
            logger.info("检测任务已入库: task_id=%d, user=%d", task.id, user_id)
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
    if not detection_result or detection_result.get("total_objects", -1) < 0:
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

        # 根据检出的病灶类型检索相关知识
        class_counts = detection_result.get("class_counts", {})
        lesion_types = list(class_counts.keys())
        if lesion_types:
            # 检索最突出的病灶相关知识
            primary_lesion = max(class_counts, key=class_counts.get)
            rag_query = f"{primary_lesion} 胸部X光 诊断 鉴别诊断 临床建议"
            rag_results = knowledge_retriever.search(rag_query, top_k=3)
            if rag_results:
                knowledge_context = "\n\n---\n\n".join(
                    f"[知识库: {r.get('metadata', {}).get('source', '未知')}]\n{r.get('content', '')[:300]}"
                    for r in rag_results if r.get("similarity", 0) >= 0.4
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

    # ── 简单提取风险等级 ──
    risk_level = "medium"
    for level in ["极高风险", "高风险", "中风险", "低风险"]:
        if level in diagnosis_text:
            risk_map = {"极高风险": "critical", "高风险": "high", "中风险": "medium", "低风险": "low"}
            risk_level = risk_map.get(level, "medium")
            break

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

        results = knowledge_retriever.search(user_msg, top_k=3)
        if results:
            max_sim = max(r.get("similarity", 0) for r in results)
            if max_sim >= 0.5:
                has_knowledge = True
                knowledge_sources = [
                    {
                        "source": r.get("metadata", {}).get("source", "未知"),
                        "content": r.get("content", "")[:200],
                        "similarity": r.get("similarity", 0),
                    }
                    for r in results if r.get("similarity", 0) >= 0.5
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
