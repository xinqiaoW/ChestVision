"""
Pgvector 客户端 — 基于 PostgreSQL pgvector 扩展的向量存储

职责：
  - 管理 knowledge_embeddings 表
  - 插入向量数据
  - 执行余弦相似度检索

注意：
  - 需要 PostgreSQL 安装 pgvector 扩展
  - 如果 pgvector 不可用，使用内存向量存储降级
"""

from typing import Optional

from app.config.settings import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# 默认向量维度（通义千问 text-embedding-v3）
EMBEDDING_DIM = getattr(settings, "EMBEDDING_DIM", 1024)


class InMemoryVectorStore:
    """内存向量存储（降级方案）"""

    def __init__(self):
        self._data = []  # [(content, metadata, embedding), ...]

    def insert(self, contents, embeddings, metadatas=None):
        for i in range(len(contents)):
            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            self._data.append((contents[i], meta, embeddings[i]))

    def search(self, query_embedding, top_k=3):
        if not self._data or not query_embedding:
            return []

        import math

        def cosine_similarity(a, b):
            if not a or not b:
                return 0
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            if norm_a == 0 or norm_b == 0:
                return 0
            return dot / (norm_a * norm_b)

        scored = []
        for content, meta, emb in self._data:
            sim = cosine_similarity(query_embedding, emb)
            scored.append((content, meta, sim))

        scored.sort(key=lambda x: x[2], reverse=True)
        return [
            {"content": s[0], "metadata": s[1], "similarity": round(s[2], 4)}
            for s in scored[:top_k]
        ]

    def count(self):
        return len(self._data)

    def clear(self):
        self._data = []


class PgvectorClient:
    """Pgvector 向量存储客户端（带内存降级）"""

    def __init__(self):
        self._initialized = False
        self._use_pgvector = False
        self._memory_store = InMemoryVectorStore()

    def init_table(self):
        """初始化 pgvector 表和索引"""
        if self._initialized:
            return

        try:
            import psycopg2
            import pgvector.psycopg2

            conn = psycopg2.connect(settings.DATABASE_URL)
            try:
                cur = conn.cursor()
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS knowledge_embeddings (
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        metadata JSONB DEFAULT '{{}}'::jsonb,
                        embedding vector({EMBEDDING_DIM}),
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                """)
                # 尝试创建索引（表为空时 ivfflat 会失败，忽略）
                try:
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_vector
                        ON knowledge_embeddings
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100);
                    """)
                except Exception:
                    pass
                conn.commit()
                cur.close()
                self._use_pgvector = True
                self._initialized = True
                logger.info("Pgvector 表和索引初始化完成")
            finally:
                conn.close()
        except ImportError:
            logger.warning("pgvector 库未安装，使用内存向量存储")
            self._initialized = True
        except Exception as e:
            logger.warning("Pgvector 初始化失败，使用内存向量存储: %s", str(e))
            self._initialized = True

    def insert_embeddings(self, contents, embeddings, metadatas=None):
        """插入向量数据（双写：Pgvector + 内存备份，确保降级时内存有数据）"""
        if not contents or not embeddings:
            return

        # 始终写入内存作为备份（确保 pgvector 故障时内存存储有数据可用）
        self._memory_store.insert(contents, embeddings, metadatas)

        if self._use_pgvector:
            try:
                import json
                import psycopg2
                conn = psycopg2.connect(settings.DATABASE_URL)
                try:
                    cur = conn.cursor()
                    for i in range(len(contents)):
                        meta = metadatas[i] if metadatas and i < len(metadatas) else {}
                        embedding_str = "[" + ",".join(str(v) for v in embeddings[i]) + "]"
                        cur.execute(
                            """
                            INSERT INTO knowledge_embeddings (content, metadata, embedding)
                            VALUES (%s, %s::jsonb, %s::vector)
                            """,
                            (contents[i], json.dumps(meta, ensure_ascii=False), embedding_str),
                        )
                    conn.commit()
                    cur.close()
                    logger.info("插入 %d 条向量数据到 Pgvector（内存备份: %d 条）", len(contents), self._memory_store.count())
                    return
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("Pgvector 插入失败，已使用内存存储: %s", str(e))
                self._use_pgvector = False
        else:
            logger.info("插入 %d 条向量数据到内存存储", len(contents))

    def search(self, query_embedding, top_k=3):
        """语义检索"""
        if self._use_pgvector:
            try:
                import numpy as np
                import psycopg2
                import pgvector.psycopg2
                if isinstance(query_embedding, list):
                    query_embedding = np.array(query_embedding, dtype=np.float32)

                conn = psycopg2.connect(settings.DATABASE_URL)
                pgvector.psycopg2.register_vector(conn)
                try:
                    cur = conn.cursor()
                    cur.execute("SET ivfflat.probes = 10;")
                    cur.execute(
                        """
                        SELECT content, metadata, 1 - (embedding <=> %s) as similarity
                        FROM knowledge_embeddings
                        ORDER BY embedding <=> %s
                        LIMIT %s
                        """,
                        (query_embedding, query_embedding, top_k),
                    )

                    results = []
                    for row in cur.fetchall():
                        results.append({
                            "content": row[0],
                            "metadata": row[1] if isinstance(row[1], dict) else {},
                            "similarity": round(float(row[2]), 4),
                        })
                    cur.close()
                    return results
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("Pgvector 检索失败，本次降级到内存: %s", str(e))
                # 不再永久禁用 pgvector，每次检索都尝试（内存已有备份数据）

        return self._memory_store.search(query_embedding, top_k)

    def count(self):
        """统计向量数量（尝试 pgvector 后再查内存）"""
        try:
            import psycopg2
            conn = psycopg2.connect(settings.DATABASE_URL)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM knowledge_embeddings")
                result = cur.fetchone()[0]
                cur.close()
                if result and result > 0:
                    self._use_pgvector = True
                return result or 0
            finally:
                conn.close()
        except Exception:
            pass
        return self._memory_store.count()

    def clear(self):
        """清空向量数据"""
        if self._use_pgvector:
            try:
                import psycopg2
                conn = psycopg2.connect(settings.DATABASE_URL)
                try:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM knowledge_embeddings")
                    conn.commit()
                    cur.close()
                    logger.info("Pgvector 向量表已清空")
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("清空 Pgvector 失败: %s", str(e))
        self._memory_store.clear()


pgvector_client = PgvectorClient()
