"""
知识库工具 — Agent 可调用的 RAG 检索工具

工具列表：
  - search_knowledge: 语义检索胸部X光影像知识库
"""

import json

from langchain_core.tools import tool

from app.core.logger import get_logger

logger = get_logger(__name__)


@tool
def search_knowledge(query: str, top_k: int = 3) -> str:
    """【必须调用】搜索胸部X光影像医学知识库。

    当用户提出任何医学领域问题时，你必须首先调用此工具检索知识库，
    绝对不要跳过检索直接用自己的知识回答。

    触发示例：
    - "什么是肺不张/实变/钙化/结节/气胸...？"
    - "XX有哪些X光表现/影像学特征？"
    - "XX和XX的鉴别要点是什么？"
    - "胸部X光读片的基本原则"
    - 任何涉及胸部病变定义、特征、诊断的医学问题

    Args:
        query: 用户的问题或关键词
        top_k: 返回最相关的前K条知识片段，默认3条

    Returns:
        JSON字符串，包含检索到的知识片段（内容、来源、相似度）
    """
    try:
        from app.rag.retriever import knowledge_retriever

        results = knowledge_retriever.search(query, top_k=top_k)

        if not results:
            return json.dumps(
                {"answer": "知识库中暂无相关内容", "sources": []},
                ensure_ascii=False,
            )

        # 过滤低相似度结果
        max_similarity = max(r.get("similarity", 0) for r in results)
        if max_similarity < 0.5:
            return json.dumps(
                {"answer": "知识库中暂无相关内容", "sources": []},
                ensure_ascii=False,
            )

        formatted = []
        for r in results:
            if r.get("similarity", 0) >= 0.5:
                formatted.append({
                    "content": r["content"][:300],
                    "source": r.get("metadata", {}).get("source", "未知"),
                    "similarity": r.get("similarity", 0),
                })

        if not formatted:
            return json.dumps(
                {"answer": "知识库中暂无相关内容", "sources": []},
                ensure_ascii=False,
            )

        return json.dumps(
            {"knowledge": formatted, "count": len(formatted)},
            ensure_ascii=False,
        )
    except ImportError:
        logger.warning("RAG模块未安装，使用降级模式")
        return json.dumps(
            {"answer": "知识库服务暂不可用，请稍后重试", "sources": []},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error("知识检索失败: %s", str(e))
        return json.dumps({"error": f"检索失败: {str(e)}"}, ensure_ascii=False)


# 知识工具列表
KNOWLEDGE_TOOLS = [
    search_knowledge,
]
