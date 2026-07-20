"""RAG 检索修复验证脚本"""
import sys
sys.path.insert(0, '.')

from app.rag.retriever import knowledge_retriever
from app.vectorstore.pgvector_client import pgvector_client

# 模拟重启：重置状态
knowledge_retriever._index_built = False

# 应检测到已有索引数据并跳过
print('=== 模拟重启后首次检索 ===')
knowledge_retriever.build_index()
print(f'Pgvector记录: {pgvector_client.count()}, 内存记录: {pgvector_client._memory_store.count()}')

# 检索测试
results = knowledge_retriever.search('什么是肺实变', top_k=3)
print(f'检索结果: {len(results)}条')
for r in results:
    sim = r.get("similarity", 0)
    content = r.get("content", "")[:60]
    print(f'  sim={sim:.4f} | {content}...')

# 模拟 pgvector 故障后的降级
print('\n=== 模拟 pgvector 故障降级 ===')
pgvector_client._use_pgvector = True
results2 = knowledge_retriever.search('气胸的X光表现', top_k=3)
print(f'降级检索结果: {len(results2)}条')
for r in results2:
    sim = r.get("similarity", 0)
    content = r.get("content", "")[:60]
    print(f'  sim={sim:.4f} | {content}...')
