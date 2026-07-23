"""
胸片检测智能体 — 多工具 Agent + 对话记忆 + 增强 SSE（Day 11 升级版）

升级内容：
  1. Prompt 模板外置到 prompts.py
  2. 工具拆分到 tools/ 目录（检测 + 分析 + 知识库）
  3. 集成对话记忆（Redis缓存 + DB持久化）
  4. SSE 事件协议增强（thinking/tool_start/tool_end/text_chunk/done/error）

架构：
  用户消息 → 加载历史 → Agent（LLM + 7+ 工具）→ 调用工具 → SSE 流式返回
"""

from typing import AsyncGenerator, Optional

import httpx

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from app.agent.memory import conversation_memory
from app.agent.prompts import CHESTX_AGENT_SYSTEM_PROMPT
from app.agent.tools.analysis_tool import SYSTEM_TOOLS
from app.agent.tools.detection_tool import (
    DETECTION_TOOLS,
    clear_last_result,
    get_last_result,
)
from app.agent.tools.knowledge_tool import KNOWLEDGE_TOOLS
from app.config.settings import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def create_llm():
    """根据配置创建 LLM 实例，优先使用通义千问"""
    qwen_api_key = getattr(settings, "QWEN_API_KEY", "")
    if qwen_api_key and qwen_api_key not in ("sk-your-qwen-api-key", ""):
        api_key = qwen_api_key
        base_url = getattr(
            settings,
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model_name = getattr(settings, "QWEN_MODEL", "qwen-plus")
    else:
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        model_name = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    http_client = httpx.Client(proxy=None, timeout=60.0)
    async_http_client = httpx.AsyncClient(proxy=None, timeout=60.0)

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,  # type: ignore[arg-type]
        base_url=base_url,
        temperature=0.1,
        http_client=http_client,
        http_async_client=async_http_client,
    )


# ══════════════════════════════════════════════════════════════
# DetectionAgent 类（Day 11 升级版）
# ══════════════════════════════════════════════════════════════


class DetectionAgent:
    """胸片检测智能体（Day 11 升级版）

    升级要点：
      - 外置 Prompt 模板（prompts.py）
      - 工具拆分到独立模块（tools/）
      - 增强 SSE 事件协议（thinking/tool_start/tool_end/done）
      - 集成对话记忆（Redis缓存层）
      - 绑定 7+ 工具（检测3 + 系统2 + 知识库1+）
    """

    def __init__(self):
        self.llm = None
        self.executor = None
        # 合并所有工具
        self.all_tools = DETECTION_TOOLS + SYSTEM_TOOLS + KNOWLEDGE_TOOLS
        logger.info(
            "DetectionAgent 工具清单: 检测%d + 系统%d + 知识%d = 共%d个",
            len(DETECTION_TOOLS), len(SYSTEM_TOOLS),
            len(KNOWLEDGE_TOOLS), len(self.all_tools),
        )

    def _ensure_initialized(self):
        """延迟初始化 LLM 和 AgentExecutor"""
        if self.executor is not None:
            return

        qwen_key = getattr(settings, "QWEN_API_KEY", "")
        if qwen_key and qwen_key not in ("sk-your-qwen-api-key", ""):
            api_key = qwen_key
            base_url = getattr(
                settings, "QWEN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
            model = getattr(settings, "QWEN_MODEL", "qwen-plus")
        else:
            api_key = getattr(settings, "OPENAI_API_KEY", "")
            base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

        if not api_key or api_key in ("sk-your-api-key-here", "sk-your-qwen-api-key", ""):
            raise ValueError(
                "未配置有效的 LLM API Key。请在 .env 中设置 QWEN_API_KEY。"
                "快捷按钮通道不需要 LLM，仍可正常使用。"
            )

        self.llm = ChatOpenAI(
            model=model,
            api_key=api_key,  # type: ignore[arg-type]
            base_url=base_url,
            temperature=0.1,
            http_client=httpx.Client(proxy=None, timeout=60.0),
            http_async_client=httpx.AsyncClient(proxy=None, timeout=60.0),
        )

        # 使用外置 Prompt 模板
        prompt = ChatPromptTemplate.from_messages([
            ("system", CHESTX_AGENT_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent = create_openai_tools_agent(
            llm=self.llm, tools=self.all_tools, prompt=prompt
        )
        self.executor = AgentExecutor(
            agent=agent,
            tools=self.all_tools,
            verbose=True,
            max_iterations=8,  # 工具增多，适当提高迭代上限
            return_intermediate_steps=True,
        )
        logger.info("DetectionAgent (Day11) 初始化完成: model=%s, tools=%d", model, len(self.all_tools))

    async def chat(self, message: str, image_path: Optional[str] = None) -> dict:
        """非流式对话（兼容旧接口）"""
        self._ensure_initialized()
        assert self.executor is not None
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"
        try:
            result = await self.executor.ainvoke({"input": message})
            return {
                "output": result["output"],
                "intermediate_steps": result.get("intermediate_steps", []),
            }
        except Exception as e:
            logger.error("Agent 执行异常: %s", str(e), exc_info=True)
            return {"output": "抱歉，处理失败，请稍后重试", "intermediate_steps": []}

    async def chat_stream(
        self,
        message: str,
        image_path: Optional[str] = None,
        chat_history: Optional[list] = None,
        user_id: int = 0,
        session_id: str = "default",
    ) -> AsyncGenerator:
        """流式处理对话消息（增强 SSE 协议）

        SSE 事件类型：
          - thinking:    Agent 正在思考
          - tool_start:  开始调用工具
          - tool_end:    工具调用完成
          - text_chunk:  LLM 回复文本片段
          - done:        对话完成
          - error:       出错

        Args:
            message: 用户消息
            image_path: 附件图片路径
            chat_history: 历史消息列表（DB持久化层传入）
            user_id: 用户ID（用于Redis缓存记忆）
            session_id: 会话ID
        """
        self._ensure_initialized()
        assert self.executor is not None
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"

        # ── Step 1: 发送 thinking 事件 ──
        yield {"type": "thinking", "content": "正在分析您的请求..."}

        # ── Step 2: 准备 Agent 输入 ──
        invoke_input = {"input": message}
        if chat_history:
            invoke_input["chat_history"] = chat_history

        full_text = ""

        try:
            async for event in self.executor.astream_events(invoke_input, version="v2"):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        full_text += chunk.content
                        yield {"type": "text_chunk", "content": chunk.content}

                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    tool_input = event["data"].get("input", {})
                    logger.info("工具调用开始: %s", tool_name)
                    yield {
                        "type": "tool_start",
                        "tool": tool_name,
                        "input": {k: str(v)[:100] for k, v in tool_input.items()},
                    }

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    tool_output = str(event.get("data", {}).get("output", ""))
                    summary = tool_output[:100] if tool_output else ""
                    logger.info("工具调用完成: %s", tool_name)
                    yield {
                        "type": "tool_end",
                        "tool": tool_name,
                        "summary": summary,
                        "result": tool_output,
                    }

                    # 如果有完整检测结果（含 base64 图），单独发给前端
                    last_result = get_last_result()
                    if last_result:
                        yield {
                            "type": "detection_card",
                            "data": last_result,
                        }
                        clear_last_result()

        except Exception as e:
            logger.error("Agent 流式异常: %s", str(e), exc_info=True)
            yield {"type": "error", "content": "处理失败，请稍后重试"}

        # ── Step 3: 缓存 AI 回复到 Redis 记忆 ──
        if full_text and user_id:
            try:
                conversation_memory.save_message(user_id, session_id, "ai", full_text)
            except Exception as e:
                logger.warning("Redis缓存AI回复失败: %s", str(e))

        # ── Step 4: 发送 done 事件 ──
        yield {
            "type": "done",
            "full_text": full_text,
        }


# 全局单例
detection_agent = DetectionAgent()
