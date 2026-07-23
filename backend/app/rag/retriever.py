"""
语义检索器 — 将 RAG 流程串联为完整管道

职责：
  - 知识库初始化（加载文档 → 分块 → 向量化 → 存储）
  - 语义检索（查询 → 向量化 → 检索 → 格式化结果）
  - 知识库管理（重建索引、清空、统计）

使用方式：
  from app.rag.retriever import knowledge_retriever

  # 初始化知识库（首次启动时调用）
  knowledge_retriever.build_index()

  # 检索
  results = knowledge_retriever.search("什么是肺不张？", top_k=3)
"""

from app.config.settings import settings
from app.core.logger import get_logger
from app.rag.document_loader import document_loader
from app.rag.embedding import embedding_service
from app.vectorstore.pgvector_client import pgvector_client

logger = get_logger(__name__)

# 统一使用配置中的相似度阈值
RAG_THRESHOLD = settings.RAG_SIMILARITY_THRESHOLD


class KnowledgeRetriever:
    """知识检索器"""

    def __init__(self):
        self._index_built = False

    def build_index(self, force_rebuild: bool = False):
        """构建知识库索引：加载文档 → 文本分块 → 向量化 → 存储"""
        # 检查是否已有索引数据（无论 _index_built 状态，避免重启后重复构建）
        if not force_rebuild:
            count = pgvector_client.count()
            if count > 0:
                self._index_built = True
                logger.info("知识库索引已存在 (%d条)，跳过构建", count)
                return

        if not embedding_service.is_available:
            logger.warning("Embedding服务不可用，跳过知识库索引构建")
            return

        logger.info("开始构建知识库索引...")

        pgvector_client.init_table()

        if force_rebuild:
            pgvector_client.clear()

        documents = document_loader.load_documents()
        if not documents:
            logger.warning("知识库中没有文档")
            return

        chunks = document_loader.split_documents(documents)
        if not chunks:
            logger.warning("文档分块后为空")
            return

        texts = [chunk["content"] for chunk in chunks]
        metadatas = [chunk["metadata"] for chunk in chunks]
        embeddings = embedding_service.embed_texts(texts)

        valid_indices = [i for i, emb in enumerate(embeddings) if emb]
        if not valid_indices:
            logger.error("所有文本向量化均失败")
            return

        valid_texts = [texts[i] for i in valid_indices]
        valid_embeddings = [embeddings[i] for i in valid_indices]
        valid_metadatas = [metadatas[i] for i in valid_indices]

        pgvector_client.insert_embeddings(valid_texts, valid_embeddings, valid_metadatas)

        self._index_built = True
        logger.info("知识库索引构建完成: %d个文档 → %d个文本块 → %d条向量",
                    len(documents), len(chunks), len(valid_texts))

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """语义检索"""
        if not self._index_built:
            self.build_index()

        if not embedding_service.is_available:
            logger.warning("Embedding服务不可用，无法检索")
            return []

        query_embedding = embedding_service.embed_query(query)
        if not query_embedding:
            logger.error("查询向量化失败: %s", query)
            return []

        results = pgvector_client.search(query_embedding, top_k=top_k)
        return results

    def format_context(self, results: list[dict]) -> str:
        """将检索结果格式化为 LLM 可用的上下文文本"""
        if not results:
            return "（知识库中暂无相关内容）"

        parts = []
        for i, r in enumerate(results, 1):
            source = r.get("metadata", {}).get("source", "未知")
            similarity = r.get("similarity", 0)
            parts.append(
                f"[知识片段 {i}] (来源: {source}, 相似度: {similarity:.2f})\n"
                f"{r['content']}"
            )

        return "\n\n---\n\n".join(parts)

    def search_with_threshold(
        self, query: str, top_k: int = 3, threshold: float = None
    ) -> list[dict]:
        """语义检索并自动过滤低于阈值的结果（供 Agent 节点使用）

        Args:
            query: 查询文本
            top_k: 返回最大条数
            threshold: 相似度阈值，默认使用全局配置 RAG_SIMILARITY_THRESHOLD

        Returns:
            已过滤的检索结果列表
        """
        if threshold is None:
            threshold = RAG_THRESHOLD

        results = self.search(query, top_k=top_k)
        if not results:
            return []

        # 过滤低于阈值的结果
        filtered = [
            r for r in results
            if r.get("similarity", 0) >= threshold
        ]
        return filtered

    def get_stats(self) -> dict:
        """获取知识库统计信息"""
        count = pgvector_client.count()
        return {
            "total_chunks": count,
            "index_built": self._index_built or count > 0,
            "embedding_available": embedding_service.is_available,
        }


knowledge_retriever = KnowledgeRetriever()
