"""
Supervisor 路由 Agent — 胸片X光多智能体系统的任务调度中心

职责：
  根据用户意图将请求路由到最合适的专业 Agent：
  - detection  → 胸片病灶检测（上传图片/要求检测）
  - diagnosis  → 综合诊断分析（结合检测结果+病史）
  - report     → 诊断报告生成
  - case_analysis → 结合历史病例形成诊疗计划框架
  - qa         → 医学知识问答（RAG检索增强）
  - summarize  → 直接回复（无需工具调用）

路由规则（按优先级）：
  1. 含附件图片路径  → detection
  2. 含"检测/识别/分析这张"  → detection
  3. 含"再检测/重新检测"  → detection
  4. 含"报告/生成报告"  → report
  5. 含"诊断/分析结果/怎么看/严重吗"  → diagnosis
  6. 含医学知识问题关键词 → qa
  7. 默认 → summarize（直接对话）
"""

import json
import re
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts import SUPERVISOR_ROUTING_PROMPT
from app.core.logger import get_logger

logger = get_logger(__name__)

# Supervisor 可路由的目标节点
AgentRoute = Literal[
    "detection",
    "diagnosis",
    "report",
    "case_analysis",
    "qa",
    "summarize",
    "FINISH",
]


class SupervisorAgent:
    """任务调度与统一回复 Supervisor。

    专业 Agent 只负责产生结构化结果，所有面向用户的最终回复均由本类完成，
    从而确保回复始终使用同一份完整会话历史。
    """

    def __init__(self, llm: ChatOpenAI):
        self.llm = llm

    _attachment_pattern = re.compile(
        r"\[附件(?:图片|多张图片|视频|ZIP)路径:\s*.*?\]"
    )
    _server_path_pattern = re.compile(
        r"(?:(?:/tmp|/app|/var/tmp)/[^\s`，。；、）》）\]]+)"
    )

    async def route(self, state: dict) -> dict:
        """分析用户最新消息，返回路由决策

        Args:
            state: MultiAgentState 字典

        Returns:
            {"next_agent": "detection"|"diagnosis"|"report"|"qa"|"summarize"|"FINISH"}
        """
        messages = state.get("messages", [])
        if not messages:
            return {"next_agent": "FINISH", "routed_agent": "FINISH"}

        # 获取最后一条用户消息
        last_user_msg = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_user_msg = msg.content
                break

        if not last_user_msg:
            return {"next_agent": "FINISH", "routed_agent": "FINISH"}

        # ── 规则快速路由（减少 LLM 调用成本）──
        fast_route = self._fast_route(last_user_msg, state)
        if fast_route:
            logger.info("Supervisor 快速路由: %s → %s", last_user_msg[:50], fast_route)
            return {"next_agent": fast_route, "routed_agent": fast_route}

        # ── LLM 语义路由 ──
        try:
            route = await self._llm_route(messages, last_user_msg)
            logger.info("Supervisor LLM 路由: %s → %s", last_user_msg[:50], route)
            return {"next_agent": route, "routed_agent": route}
        except Exception as e:
            logger.error("Supervisor LLM 路由失败: %s，降级为 summarize", str(e))
            return {"next_agent": "summarize", "routed_agent": "summarize"}

    def _fast_route(self, message: str, state: dict) -> AgentRoute | None:
        """基于规则的快速路由，无需 LLM 调用

        返回 None 表示无法快速判断，需走 LLM 路由
        """
        msg_lower = message.lower()

        # 1. 含附件 → detection
        if any(kw in message for kw in [
            "[附件图片路径:", "[附件多张图片路径:",
            "[附件视频路径:", "[附件ZIP路径:",
        ]):
            return "detection"

        # 治疗/诊疗/随访计划必须先读取当前患者历史病例。
        case_analysis_keywords = [
            "治疗计划", "治疗方案", "诊疗计划", "诊疗方案", "用药计划",
            "随访计划", "康复计划", "制定方案", "怎么治疗", "如何治疗",
            "后续治疗", "治疗建议", "下一步治疗", "处置计划", "复查计划",
            "如何处置", "后续怎么处理",
        ]
        if any(kw in message for kw in case_analysis_keywords):
            return "case_analysis"

        # ══════════════════════════════════════════════════════
        # 1.5 回顾性查询 → diagnosis（优先于 detection 判断）
        #   用户明确在引用之前的检测结果，不应重新检测
        # ══════════════════════════════════════════════════════
        retrospective_keywords = [
            "刚才", "刚刚", "我的结果", "检测结果", "我的检测",
            "我的胸片", "我的报告", "上次", "之前的",
            "据此", "基于此", "根据结果", "结合结果",
            "对我", "生活建议", "注意事项", "从检测",
            "从结果", "根据检测", "我的病情",
            "患有什么", "得了什么", "什么病", "需要注意",
        ]
        if any(kw in message for kw in retrospective_keywords):
            # 检测 state 中是否有结果；即便没有也走 diagnosis
            # （diagnosis_node 有 DB 兜底逻辑）
            detection_result = state.get("detection_result", {})
            if detection_result and detection_result.get("total_objects", -1) >= 0:
                return "diagnosis"
            # 无 state 结果但消息明显在回顾 → 走 LLM 语义路由进一步判断
            # 不放行到 detection，避免误判
            if any(kw in message for kw in ["检测结果", "从检测", "从结果", "根据检测", "我的结果"]):
                return "diagnosis"
            # 其他回顾类关键词走 LLM 路由

        # 2. 检测相关关键词 → detection
        detection_keywords = [
            "检测", "识别", "看看这张", "分析这张", "帮我看看",
            "再检测", "重新检测", "再试", "再分析", "检测一下",
            "看看胸片", "分析胸片", "拍个片",
        ]
        if any(kw in message for kw in detection_keywords):
            # 但如果已有检测结果，可能需要诊断
            detection_result = state.get("detection_result", {})
            if detection_result and detection_result.get("total_objects", -1) >= 0:
                # 有检测结果了，看是否问"严重吗"类问题
                if any(kw in message for kw in ["严重吗", "怎么看", "什么意思", "诊断"]):
                    return "diagnosis"
                if any(kw in message for kw in ["报告", "出报告"]):
                    return "report"
            return "detection"

        # 3. 报告生成关键词 → report
        report_keywords = ["生成报告", "出报告", "写报告", "诊断报告", "影像报告"]
        if any(kw in message for kw in report_keywords):
            return "report"

        # 4. 诊断分析关键词 → diagnosis
        diagnosis_keywords = [
            "诊断", "严重吗", "怎么看", "什么意思", "分析结果",
            "病情", "风险评估", "预后", "怎么办", "要紧吗",
            "需不需要", "治疗建议", "下一步",
        ]
        if any(kw in message for kw in diagnosis_keywords):
            # 新一轮 state 通常不会直接携带上一轮检测结果；diagnosis 节点会按
            # 当前用户从数据库恢复最近检测，不能因为 state 为空就误路由为重新检测。
            return "diagnosis"

        # 5. 知识问答关键词 → qa
        qa_keywords = [
            "什么是", "什么叫", "解释", "定义", "原理",
            "肺不张", "钙化", "实变", "胸腔积液", "肺气肿",
            "纤维化", "骨折", "肿块", "结节", "气胸",
            "X光", "胸片", "影像", "放射", "读片",
            "鉴别", "区别", "特征", "表现",
        ]
        if any(kw in message for kw in qa_keywords):
            return "qa"

        # 无法快速判断
        return None

    async def _llm_route(self, messages: list, message: str) -> AgentRoute:
        """使用 LLM 结合历史对话进行语义路由。"""
        prompt = SUPERVISOR_ROUTING_PROMPT.format(user_message=message)

        history = list(messages[:-1])[-16:]
        response = await self.llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是 ChestVision 的任务路由 Supervisor。"
                        "你可以阅读完整历史，但只回复目标节点名称，不回答用户问题。"
                    )
                ),
                *history,
                HumanMessage(content=prompt),
            ]
        )

        route_text = (response.content if hasattr(response, "content")
                      else str(response)).strip().lower()

        valid_routes = [
            "detection", "diagnosis", "report", "case_analysis", "qa", "summarize", "finish"
        ]
        for valid in valid_routes:
            if valid in route_text:
                return "FINISH" if valid == "finish" else valid  # type: ignore[return-value]

        logger.warning("LLM 返回未知路由: %s，降级为 summarize", route_text)
        return "summarize"

    async def answer(self, state: dict) -> dict:
        """基于完整历史和专业 Agent 结果，由 Supervisor 统一生成最终回复。"""
        messages = self._sanitize_messages(list(state.get("messages", [])))
        routed_agent = state.get("routed_agent") or state.get("next_agent", "summarize")
        agent_context = self._build_agent_context(state, routed_agent)

        # 已完成的检测必须依据真实结构化结果直接输出完成态，不能让模型把同步
        # 检测误写成“正在处理”“请稍候”或编造预计耗时。
        detection = state.get("detection_result", {}) or {}
        if (
            routed_agent == "detection"
            and "error" not in detection
            and detection.get("total_objects", -1) >= 0
        ):
            final_response = self._completed_detection_response(state)
            return {
                "final_response": final_response,
                "knowledge_sources": state.get("knowledge_sources", []),
                "has_knowledge": state.get("has_knowledge", False),
            }

        supervisor_prompt = SystemMessage(
            content=(
                "你是 ChestVision 的 Supervisor，也是唯一面向用户作最终回答的助手。"
                "你必须结合下方完整对话历史回答最新一条用户消息，并延续用户此前自述的"
                "姓名、职称、专长、偏好以及当前登录身份。不得声称自己没有历史记录，"
                "不得以权限限制为由否认历史中已经提供的信息。\n"
                "专业 Agent 的输出只是本轮可引用的事实和草稿，不是最终回答；请由你统一"
                "组织语言，不要向用户提及路由、子 Agent、内部状态或服务器文件路径。"
                "本轮专业 Agent 结果均已执行完毕，禁止使用‘正在处理’‘请稍候’‘预计几秒’"
                "等未完成状态措辞。"
                "禁止自行编造附件名、下载链接或声称已生成不存在的 PDF。"
                "只有本轮专业结果中 report_download_available 为 true 时，"
                "才可告知用户可通过消息下方的按钮下载 PDF。"
                "若专业结果与明确的历史事实冲突，以系统身份信息、真实检测数据和数据库"
                "上下文为准。医学结论须说明仅供辅助参考，最终诊断由临床医生结合实际判断。"
                "当 routed_agent 为 case_analysis 时，只能使用 case_analysis_result 中的病例事实，"
                "不得新增药品名称、剂量或频次，并须明确指出仍需补充或由医生复核的信息。"
            )
        )
        result_prompt = SystemMessage(
            content=f"[本轮专业 Agent 结果，仅供 Supervisor 组织最终回答]\n{agent_context}"
        )

        if messages:
            answer_messages = [supervisor_prompt, *messages[:-1], result_prompt, messages[-1]]
        else:
            answer_messages = [supervisor_prompt, result_prompt]

        try:
            response = await self.llm.ainvoke(answer_messages)
            final_response = (
                response.content if hasattr(response, "content") else str(response)
            )
            final_response = self._sanitize_user_visible_text(final_response)
        except Exception as e:
            logger.error("Supervisor 统一回复失败: %s", str(e), exc_info=True)
            final_response = self._fallback_response(state)

        logger.info(
            "Supervisor 统一回复完成: route=%s, text_len=%d",
            routed_agent,
            len(final_response),
        )
        return {
            "final_response": final_response,
            "knowledge_sources": state.get("knowledge_sources", []),
            "has_knowledge": state.get("has_knowledge", False),
        }

    @classmethod
    def _sanitize_messages(cls, messages: list) -> list:
        """移除仅供工具使用的附件路径，避免最终回答模型看到或复述。"""
        sanitized = []
        for message in messages:
            content = getattr(message, "content", "")
            if not isinstance(content, str):
                sanitized.append(message)
                continue
            clean_content = cls._attachment_pattern.sub(
                "[本轮用户已上传胸片，检测已由专业 Agent 完成]",
                content,
            )
            clean_content = cls._server_path_pattern.sub("[内部路径已隐藏]", clean_content)
            if isinstance(message, HumanMessage):
                sanitized.append(HumanMessage(content=clean_content))
            elif isinstance(message, AIMessage):
                sanitized.append(AIMessage(content=clean_content))
            elif isinstance(message, SystemMessage):
                sanitized.append(SystemMessage(content=clean_content))
            else:
                sanitized.append(message)
        return sanitized

    @classmethod
    def _sanitize_user_visible_text(cls, text: str) -> str:
        text = cls._attachment_pattern.sub("已上传胸片", text)
        return cls._server_path_pattern.sub("内部路径已隐藏", text)

    @classmethod
    def _completed_detection_response(cls, state: dict) -> str:
        """由 Supervisor 根据真实结果生成确定性的检测完成回复。"""
        detection = state.get("detection_result", {}) or {}
        diagnosis = state.get("diagnosis_result", {}) or {}
        total = detection.get("total_objects", 0)
        class_counts = detection.get("class_counts", {}) or {}
        inference_time = detection.get("inference_time", 0) or 0

        if total > 0:
            lesion_summary = "、".join(
                f"{name}×{count}" for name, count in sorted(class_counts.items())
            ) or "详见检测结果卡片"
            response = (
                f"## 🔬 胸片检测完成\n\n"
                f"共检出 **{total}** 个病灶：{lesion_summary}。\n\n"
                f"推理耗时：{inference_time:.0f} ms。"
            )
        else:
            response = (
                "## ✅ 胸片检测完成\n\n"
                f"本次未检出明显病灶。推理耗时：{inference_time:.0f} ms。"
            )

        findings = diagnosis.get("findings", "")
        if findings:
            risk_labels = {
                "critical": "极高风险",
                "high": "高风险",
                "medium": "中风险",
                "low": "低风险",
                "none": "未见明显风险",
            }
            risk = risk_labels.get(
                diagnosis.get("risk_level", ""), diagnosis.get("risk_level", "")
            )
            response += f"\n\n## 📋 AI 辅助分析\n\n{findings}"
            if risk:
                response += f"\n\n**风险评级：{risk}**"

        response += "\n\n> ⚠️ 本结果仅供辅助参考，最终诊断请由临床医生结合实际情况判断。"
        return cls._sanitize_user_visible_text(response)

    @staticmethod
    def _build_agent_context(state: dict, routed_agent: str) -> str:
        detection = state.get("detection_result", {}) or {}
        safe_detection = {
            "total_objects": detection.get("total_objects"),
            "class_counts": detection.get("class_counts", {}),
            "detections": detection.get("detections", []),
            "inference_time": detection.get("inference_time"),
            "task_id": detection.get("task_id"),
            "risk_level": detection.get("risk_level"),
            "error": detection.get("error"),
        }
        context = {
            "routed_agent": routed_agent,
            "detection_result": safe_detection,
            "diagnosis_result": state.get("diagnosis_result", {}),
            "report_result": state.get("report_result", ""),
            "report_download_available": bool(
                state.get("report_result")
                and (
                    state.get("task_id")
                    or detection.get("task_id")
                )
            ),
            "qa_result": state.get("qa_result", ""),
            "case_analysis_result": state.get("case_analysis_result", {}),
            "knowledge_sources": [
                {
                    "source": source.get("source"),
                    "similarity": source.get("similarity"),
                }
                for source in state.get("knowledge_sources", [])
                if isinstance(source, dict)
            ],
        }
        return json.dumps(context, ensure_ascii=False, default=str)

    @staticmethod
    def _fallback_response(state: dict) -> str:
        case_analysis = state.get("case_analysis_result", {}) or {}
        if case_analysis.get("analysis"):
            return case_analysis["analysis"]
        if state.get("report_result"):
            return state["report_result"]
        if state.get("qa_result"):
            return state["qa_result"]
        diagnosis = state.get("diagnosis_result", {}) or {}
        if diagnosis.get("findings"):
            return diagnosis["findings"]
        detection = state.get("detection_result", {}) or {}
        if detection.get("total_objects", -1) >= 0:
            return f"本次共检测到 {detection.get('total_objects', 0)} 个病灶，请结合检测结果卡片查看详情。"
        return "暂时无法生成完整回复，请稍后重试。"
