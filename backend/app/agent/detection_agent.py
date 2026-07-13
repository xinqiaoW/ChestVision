"""
胸片检测智能体 — ReAct Agent + 检测工具绑定

职责：
  - 创建 LangChain ReAct Agent
  - 绑定胸片检测工具（单图/批量/ZIP）
  - 处理 SSE 流式输出 Agent 的思考过程和结果
"""

import json
from typing import AsyncGenerator

from app.config.settings import settings
from app.core.logger import get_logger
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════
# 一、定义胸片检测工具
# ══════════════════════════════════════════════════════════════


@tool
def detect_single_image(image_path: str, conf: float = 0.25, iou: float = 0.45) -> str:
    """
    检测单张胸片图像中的病灶。支持10种胸部病变：肺不张、钙化、实变、积液、
    肺气肿、纤维化、骨折、肿块、结节、气胸。

    Args:
        image_path: 胸片图像文件路径
        conf: 置信度阈值，默认 0.25
        iou: NMS IoU 阈值，默认 0.45

    Returns:
        JSON 字符串，包含检测到的病灶列表和统计信息
    """
    from app.services.detection_service import detection_service

    result = detection_service.detect_single(image_path, conf=conf, iou=iou)
    # 把完整结果存下来（含 base64），供前端卡片使用
    DetectionAgent._last_result = result
    # 返回给 LLM 的摘要（去掉大体积的 base64）
    summary = {
        "total_objects": result["total_objects"],
        "class_counts": result["class_counts"],
        "inference_time": result["inference_time"],
    }
    return json.dumps(summary, ensure_ascii=False)


@tool
def detect_batch_images(image_paths: list[str], conf: float = 0.25) -> str:
    """
    批量检测多张胸片图像中的病灶。

    Args:
        image_paths: 胸片图像文件路径列表
        conf: 置信度阈值，默认 0.25

    Returns:
        JSON 字符串，包含每张胸片的检测结果汇总
    """
    from app.services.detection_service import detection_service

    result = detection_service.detect_batch(image_paths, conf=conf)
    DetectionAgent._last_result = result
    summary = {
        "total_images": result.get("total_images", 0),
        "total_objects": result["total_objects"],
        "class_counts": result["class_counts"],
        "total_inference_time": result.get("total_inference_time", 0),
    }
    return json.dumps(summary, ensure_ascii=False)


@tool
def detect_zip_file(zip_path: str, conf: float = 0.25) -> str:
    """
    解压 ZIP 文件并批量检测其中所有胸片图像的病灶。

    Args:
        zip_path: ZIP 文件路径
        conf: 置信度阈值，默认 0.25

    Returns:
        JSON 字符串，包含 ZIP 内所有胸片的检测结果汇总
    """
    from app.services.detection_service import detection_service

    result = detection_service.detect_zip(zip_path, conf=conf)
    DetectionAgent._last_result = result
    summary = {
        "total_images": result.get("total_images", 0),
        "total_objects": result["total_objects"],
        "class_counts": result["class_counts"],
        "total_inference_time": result.get("total_inference_time", 0),
    }
    return json.dumps(summary, ensure_ascii=False)


DETECTION_TOOLS = [detect_single_image, detect_batch_images, detect_zip_file]


# ══════════════════════════════════════════════════════════════
# 二、创建 LLM 实例
# ══════════════════════════════════════════════════════════════


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

    return ChatOpenAI(
        model=model_name,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=0.1,
    )


# ══════════════════════════════════════════════════════════════
# 三、创建 ReAct Agent
# ══════════════════════════════════════════════════════════════


class DetectionAgent:
    """胸片检测智能体（懒加载 LLM）"""

    _last_result: dict = None  # 存储最近一次检测完整结果（含 base64）

    def __init__(self):
        self.llm = None
        self.executor = None

    def _ensure_initialized(self):
        """延迟初始化 LLM 和 AgentExecutor"""
        if self.executor is not None:
            return

        qwen_key = getattr(settings, "QWEN_API_KEY", "")
        if qwen_key and qwen_key not in ("sk-your-qwen-api-key", ""):
            api_key = qwen_key
            base_url = getattr(
                settings,
                "QWEN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
            model = getattr(settings, "QWEN_MODEL", "qwen-plus")
        else:
            api_key = getattr(settings, "OPENAI_API_KEY", "")
            base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

        if not api_key or api_key in (
            "sk-your-api-key-here",
            "sk-your-qwen-api-key",
            "",
        ):
            raise ValueError(
                "未配置有效的 LLM API Key。请在 .env 中设置 QWEN_API_KEY。"
                "快捷按钮通道不需要 LLM，仍可正常使用。"
            )

        self.llm = ChatOpenAI(
            model=model,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=0.1,
        )

        system_prompt = """你是一个专业的胸部X光影像AI辅助诊断助手。支持的10种胸部病变：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸。当用户消息含 [附件图片路径: xxx] 时直接用它调用检测工具。用中文回复。"""

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        agent = create_openai_tools_agent(
            llm=self.llm, tools=DETECTION_TOOLS, prompt=prompt
        )
        self.executor = AgentExecutor(
            agent=agent,
            tools=DETECTION_TOOLS,
            verbose=True,
            max_iterations=5,
            return_intermediate_steps=True,
        )
        logger.info("DetectionAgent 初始化完成")

    async def chat(self, message: str, image_path: str = None) -> dict:
        """处理用户对话消息"""
        self._ensure_initialized()
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
            return {"output": f"抱歉，处理出错：{str(e)}", "intermediate_steps": []}

    async def chat_stream(self, message: str, image_path: str = None) -> AsyncGenerator:
        """流式处理对话消息（用于 SSE）"""
        self._ensure_initialized()
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"
        try:
            async for event in self.executor.astream_events(
                {"input": message}, version="v2"
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if hasattr(chunk, "content") and chunk.content:
                        yield {"type": "text_chunk", "content": chunk.content}
                elif kind == "on_tool_start":
                    yield {
                        "type": "tool_call",
                        "tool": event["name"],
                        "input": event["data"].get("input", {}),
                    }
                elif kind == "on_tool_end":
                    tool_data = event.get("data", {})
                    yield {
                        "type": "tool_result",
                        "tool": event.get("name", ""),
                        "result": str(tool_data.get("output", "")),
                    }
                    # 如果有完整检测结果（含 base64 图），单独发给前端
                    if DetectionAgent._last_result:
                        yield {
                            "type": "detection_card",
                            "data": DetectionAgent._last_result,
                        }
                        DetectionAgent._last_result = None
        except Exception as e:
            logger.error("Agent 流式异常: %s", str(e), exc_info=True)
            yield {"type": "error", "content": f"处理出错：{str(e)}"}


detection_agent = DetectionAgent()
