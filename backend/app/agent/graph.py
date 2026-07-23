"""
LangGraph 多 Agent 工作流 — 胸片X光智能分析系统

构建基于 LangGraph 的多智能体协作图（StateGraph）。

工作流拓扑：
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  Supervisor │  ← 任务路由
                    └──────┬──────┘
                           │
           ┌───────┬───────┼───────┬───────┐
           ▼       ▼       ▼       ▼       ▼
       ┌──────┐┌──────┐┌──────┐┌──────┐┌──────────┐
       │detect││diagno││report││  qa  ││ direct   │
       └──┬───┘└──┬───┘└──┬───┘└──┬───┘└────┬─────┘
          │       │       │       │         │
          └───────┴───────┴───────┴─────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ Supervisor  │  ← 完整历史 + 专业结果
                    │   Answer    │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │    END      │
                    └─────────────┘

路由规则：
  - detection → diagnosis（自动衔接）
  - diagnosis → supervisor_answer
  - report    → supervisor_answer
  - qa        → supervisor_answer
  - direct    → supervisor_answer
  - FINISH    → END

使用方式：
  from app.agent.graph import get_agent_graph
  graph = get_agent_graph()
  result = await graph.ainvoke(initial_state)
"""

import asyncio
from typing import AsyncGenerator, Literal, Optional

from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    case_analysis_node,
    detection_node,
    diagnosis_node,
    qa_node,
    report_node,
)
from app.agent.state import MultiAgentState
from app.agent.supervisor import SupervisorAgent
from app.core.logger import get_logger

logger = get_logger(__name__)

# 全局单例
_agent_graph = None


def get_agent_graph():
    """获取多 Agent 协作图单例（延迟初始化）"""
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph()
    return _agent_graph


def build_agent_graph(llm=None):
    """构建多 Agent 协作图

    Args:
        llm: LLM 实例（可选，不传则各节点内部自行创建）

    Returns:
        编译后的 LangGraph StateGraph
    """
    if llm is None:
        from app.agent.detection_agent import create_llm
        llm = create_llm()

    supervisor = SupervisorAgent(llm)

    # ── 创建状态图 ──
    workflow = StateGraph(MultiAgentState)

    # ── 注册节点 ──
    # Supervisor 路由节点
    workflow.add_node("supervisor", supervisor.route)

    # 专业 Agent 节点
    workflow.add_node("detection", _make_node_async(detection_node, llm))
    workflow.add_node("diagnosis", _make_node_async(diagnosis_node, llm))
    workflow.add_node("report", _make_node_async(report_node, llm))
    workflow.add_node("case_analysis", _make_node_async(case_analysis_node, llm))
    workflow.add_node("qa", _make_node_async(qa_node, llm))
    # 专业 Agent 只产出结果，最终回复由读取完整历史的 Supervisor 统一生成。
    workflow.add_node("supervisor_answer", supervisor.answer)

    # ── 设置入口 ──
    workflow.set_entry_point("supervisor")

    # ── 设置条件路由边 ──
    # Supervisor → 各专业 Agent（条件路由）
    workflow.add_conditional_edges(
        "supervisor",
        _route_decision,
        {
            "detection": "detection",
            "diagnosis": "diagnosis",
            "report": "report",
            "case_analysis": "case_analysis",
            "qa": "qa",
            "summarize": "supervisor_answer",
            "FINISH": "supervisor_answer",
        },
    )

    # ── 各 Agent → 下一节点 ──
    # detection → diagnosis（检测完自动诊断）
    workflow.add_edge("detection", "diagnosis")
    # 所有专业结果均回到 Supervisor，由其读取完整历史后统一回答。
    workflow.add_edge("diagnosis", "supervisor_answer")
    workflow.add_edge("report", "supervisor_answer")
    workflow.add_edge("case_analysis", "supervisor_answer")
    workflow.add_edge("qa", "supervisor_answer")
    workflow.add_edge("supervisor_answer", END)

    # ── 编译图 ──
    compiled_graph = workflow.compile()
    logger.info("Multi-Agent LangGraph 工作流构建完成")
    return compiled_graph


def _route_decision(state: dict) -> Literal[
    "detection", "diagnosis", "report", "case_analysis", "qa", "summarize", "FINISH"
]:
    """从 state 中提取 Supervisor 的路由决策"""
    next_agent = state.get("next_agent", "summarize")
    valid_routes = [
        "detection", "diagnosis", "report", "case_analysis", "qa", "summarize", "FINISH"
    ]
    if next_agent not in valid_routes:
        logger.warning("未知路由: %s，降级为 summarize", next_agent)
        return "summarize"
    return next_agent  # type: ignore[return-value]


def _make_node_async(node_func, llm):
    """将节点函数包装为 async callable，注入 llm 参数"""
    async def wrapper(state: dict) -> dict:
        return await node_func(state, llm)
    return wrapper


async def run_graph_stream(
    initial_state: dict,
) -> AsyncGenerator[dict, None]:
    """以 SSE 兼容的流式方式执行多 Agent 图

    产出事件类型：
      - {"type": "thinking", "content": "..."}    — 路由/节点切换
      - {"type": "detection_card", "data": {...}} — 检测完成（含标注图）
      - {"type": "report_ready", "pdf_url": "..."} — 真实 PDF 已可下载
      - {"type": "text_chunk", "content": "..."}  — 最终回复文本片段
      - {"type": "done"}                           — 完成（调用方补充 session 信息）
      - {"type": "error", "content": "..."}        — 错误
    """
    graph = get_agent_graph()

    NODE_LABELS = {
        "supervisor": "正在分析您的意图...",
        "detection": "正在进行胸片病灶检测...",
        "diagnosis": "正在进行综合诊断分析...",
        "report": "正在生成诊断报告...",
        "case_analysis": "正在分析历史病例并拟定诊疗计划框架...",
        "qa": "正在检索医学知识库...",
        "supervisor_answer": "Supervisor 正在结合对话历史组织回复...",
    }

    # 用 updates 模式获取每个节点的输出
    accumulated_state = dict(initial_state)
    final_response = ""

    try:
        async for chunk in graph.astream(initial_state, stream_mode="updates"):
            # chunk 格式: {node_name: node_output_dict}
            for node_name, node_output in chunk.items():
                if not isinstance(node_output, dict):
                    continue

                # 合并到累积状态
                accumulated_state.update(node_output)

                # 发送节点切换 thinking 事件
                label = NODE_LABELS.get(node_name, f"正在执行 {node_name}...")
                yield {"type": "thinking", "content": label}

                # ── 检测节点完成 → 发送检测卡片 ──
                if node_name == "detection":
                    det_result = node_output.get("detection_result", {})
                    if det_result and det_result.get("total_objects", -1) >= 0:
                        # 优先使用 state 中已存储的检测数据（detection_node 已持久化）
                        card_data = {
                            "total_objects": det_result.get("total_objects", 0),
                            "class_counts": det_result.get("class_counts", {}),
                            "annotated_image_url": det_result.get("annotated_image_url", ""),
                            "annotated_image_base64": det_result.get("annotated_image_base64", ""),
                            "task_id": det_result.get("task_id"),
                            "detections": det_result.get("detections", []),
                            "inference_time": det_result.get("inference_time", 0),
                        }
                        # 如果 state 中没有完整的标注图数据，尝试从全局缓存获取
                        from app.agent.tools.detection_tool import get_last_result, clear_last_result as clr
                        last = get_last_result()
                        if last:
                            card_data["annotated_image_url"] = last.get("annotated_image_url", card_data["annotated_image_url"])
                            card_data["detections"] = last.get("detections", card_data["detections"])
                            clr()
                        yield {"type": "detection_card", "data": card_data}

                # ── 报告节点完成 → 只在有真实检测任务时附加 PDF 下载地址 ──
                if node_name == "report":
                    report_task_id = (
                        node_output.get("task_id")
                        or accumulated_state.get("task_id")
                        or (accumulated_state.get("detection_result") or {}).get("task_id")
                    )
                    if report_task_id and node_output.get("report_result"):
                        yield {
                            "type": "report_ready",
                            "task_id": report_task_id,
                            "pdf_url": f"/api/reports/{report_task_id}/pdf",
                        }

                # ── Supervisor 最终回答节点 → 收集最终回复 ──
                if node_name == "supervisor_answer":
                    final_response = node_output.get("final_response", "")
                    if not final_response:
                        final_response = accumulated_state.get("final_response", "")

        # ── 流式输出最终回复 ──
        if final_response:
            chunk_size = 15
            for i in range(0, len(final_response), chunk_size):
                yield {
                    "type": "text_chunk",
                    "content": final_response[i:i + chunk_size],
                }
                await asyncio.sleep(0.018)
        else:
            # 检查是否有其他产出（qa_result, report_result 等）
            alt_response = (
                accumulated_state.get("qa_result")
                or accumulated_state.get("report_result")
                or (accumulated_state.get("case_analysis_result") or {}).get("analysis")
                or accumulated_state.get("final_response")
                or ""
            )
            if alt_response:
                chunk_size = 15
                for i in range(0, len(alt_response), chunk_size):
                    yield {"type": "text_chunk", "content": alt_response[i:i + chunk_size]}
                    await asyncio.sleep(0.018)
            else:
                yield {
                    "type": "text_chunk",
                    "content": "请问有什么可以帮您？您可以上传胸片进行AI辅助检测，或咨询胸部X光相关的医学知识。",
                }

    except Exception as e:
        logger.error("多 Agent 图执行异常: %s", str(e), exc_info=True)
        error_msg = str(e)
        if "Access denied" in error_msg and "overdue" in error_msg.lower():
            error_msg = "大模型服务访问受限，请检查您的阿里云账号状态（可能余额不足或服务过期）"
        elif "401" in error_msg or "Unauthorized" in error_msg:
            error_msg = "大模型认证失败，请检查 API Key 配置"
        elif "Connection refused" in error_msg:
            error_msg = "无法连接到大模型服务，请检查网络配置"
        elif "timeout" in error_msg.lower():
            error_msg = "大模型请求超时，请稍后重试"
        yield {"type": "error", "content": error_msg}
        return

    yield {"type": "done"}
