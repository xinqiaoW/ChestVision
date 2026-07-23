"""
Multi-Agent 状态定义 — 胸片X光智能分析系统

定义 LangGraph 多智能体协作的状态结构，贯穿整个 Agent 工作流。

Agent 协作流程：
  用户请求 → Supervisor 路由 → Detection/Diagnosis/Report/QA Agent
                                  ↓
                      Supervisor 统一回答
"""

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import add_messages


class MultiAgentState(TypedDict):
    """多智能体协作状态

    该状态在 LangGraph 图的各节点间流转，每个节点可以读写状态字段。
    """

    # ── 消息流 ──
    messages: Annotated[list, add_messages]
    """对话消息列表，使用 add_messages 累加而非覆盖"""

    # ── 路由控制 ──
    next_agent: str
    """Supervisor 指定的下一个 Agent：
       - "detection"  → 病灶检测 Agent
       - "diagnosis"   → 综合诊断 Agent
       - "report"      → 报告生成 Agent
       - "case_analysis" → 历史病例分析 Agent
       - "qa"          → 医学知识问答 Agent
       - "summarize"   → 汇总输出
       - "FINISH"      → 结束"""

    routed_agent: str
    """Supervisor 为本轮选择的专业 Agent；最终回答阶段仍保留该值。"""

    # ── 各 Agent 产出 ──
    detection_result: dict
    """检测 Agent 产出：
       {total_objects, class_counts, annotated_image_url, ...}"""

    diagnosis_result: dict
    """诊断 Agent 产出：
       {findings, risk_level, differential_diagnosis, recommendations, ...}"""

    report_result: str
    """报告 Agent 产出：结构化的 Markdown 诊断报告"""

    qa_result: str
    """知识问答 Agent 产出：基于 RAG 的知识回答"""

    case_analysis_result: dict
    """历史病例分析 Agent 产出：授权病例、检测历史及诊疗计划框架"""

    # ── 汇总输出 ──
    final_response: str
    """最终返回给用户的完整回复"""

    knowledge_sources: list[dict]
    """引用的知识来源列表 [{source, content, similarity}, ...]"""

    has_knowledge: bool
    """是否使用了知识库检索"""

    # ── 上下文信息 ──
    user_id: int
    """当前用户 ID"""

    session_id: str
    """当前会话 ID"""

    patient_profile_id: Optional[int]
    """关联的患者档案 ID（医生/管理员指定时传入）"""

    image_path: Optional[str]
    """附件图片路径"""

    task_id: Optional[int]
    """最近一次检测任务 ID（供报告生成等后续节点使用）"""

    # ── 错误处理 ──
    error: Optional[str]
    """错误信息，非空时表示某个节点执行失败"""
