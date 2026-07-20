"""
Supervisor 路由 Agent — 胸片X光多智能体系统的任务调度中心

职责：
  根据用户意图将请求路由到最合适的专业 Agent：
  - detection  → 胸片病灶检测（上传图片/要求检测）
  - diagnosis  → 综合诊断分析（结合检测结果+病史）
  - report     → 诊断报告生成
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
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts import SUPERVISOR_ROUTING_PROMPT
from app.core.logger import get_logger

logger = get_logger(__name__)

# Supervisor 可路由的目标节点
AgentRoute = Literal[
    "detection",
    "diagnosis",
    "report",
    "qa",
    "summarize",
    "FINISH",
]


class SupervisorAgent:
    """任务调度 Supervisor — 分析用户意图并决定下一步由哪个 Agent 处理"""

    def __init__(self, llm: ChatOpenAI):
        self.llm = llm

    async def route(self, state: dict) -> dict:
        """分析用户最新消息，返回路由决策

        Args:
            state: MultiAgentState 字典

        Returns:
            {"next_agent": "detection"|"diagnosis"|"report"|"qa"|"summarize"|"FINISH"}
        """
        messages = state.get("messages", [])
        if not messages:
            return {"next_agent": "FINISH"}

        # 获取最后一条用户消息
        last_user_msg = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_user_msg = msg.content
                break

        if not last_user_msg:
            return {"next_agent": "FINISH"}

        # ── 规则快速路由（减少 LLM 调用成本）──
        fast_route = self._fast_route(last_user_msg, state)
        if fast_route:
            logger.info("Supervisor 快速路由: %s → %s", last_user_msg[:50], fast_route)
            return {"next_agent": fast_route}

        # ── LLM 语义路由 ──
        try:
            route = await self._llm_route(last_user_msg)
            logger.info("Supervisor LLM 路由: %s → %s", last_user_msg[:50], route)
            return {"next_agent": route}
        except Exception as e:
            logger.error("Supervisor LLM 路由失败: %s，降级为 summarize", str(e))
            return {"next_agent": "summarize"}

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
            # 如果还没有检测结果，检查是否在回顾历史（由 1.5 处理）
            detection_result = state.get("detection_result", {})
            if not detection_result:
                return "detection"
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

    async def _llm_route(self, message: str) -> AgentRoute:
        """使用 LLM 进行语义路由"""
        prompt = SUPERVISOR_ROUTING_PROMPT.format(user_message=message)

        response = await self.llm.ainvoke([
            SystemMessage(content="你是一个任务路由专家。只回复目标节点名称，不要回复其他内容。"),
            HumanMessage(content=prompt),
        ])

        route_text = (response.content if hasattr(response, "content")
                      else str(response)).strip().lower()

        valid_routes = ["detection", "diagnosis", "report", "qa", "summarize", "finish"]
        for valid in valid_routes:
            if valid in route_text:
                return "FINISH" if valid == "finish" else valid  # type: ignore[return-value]

        logger.warning("LLM 返回未知路由: %s，降级为 summarize", route_text)
        return "summarize"
