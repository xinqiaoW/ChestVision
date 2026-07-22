"""
Redis 客户端封装 — 支持开发环境内存降级

职责：
  - 提供统一的 Redis 操作接口
  - Redis 不可用时自动降级到内存字典
  - 支持 TTL 过期
"""

import time
import threading

from app.config.settings import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Redis 客户端（带内存降级）"""

    def __init__(self):
        self._client = None
        self._memory_cache = {}  # 内存降级缓存
        self._memory_expire = {}  # 过期时间记录
        self._lock = threading.Lock()
        self._init_client()

    def _init_client(self):
        """尝试连接 Redis"""
        try:
            import redis
            self._client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._client.ping()
            logger.info("Redis 连接成功: %s:%d", settings.REDIS_HOST, settings.REDIS_PORT)
        except Exception as e:
            logger.warning("Redis 连接失败，使用内存缓存降级: %s", str(e))
            self._client = None

    def _clean_expired(self):
        """清理过期的内存缓存条目"""
        now = time.time()
        expired_keys = [
            k for k, exp in self._memory_expire.items()
            if exp is not None and now > exp
        ]
        for k in expired_keys:
            self._memory_cache.pop(k, None)
            self._memory_expire.pop(k, None)

    def get(self, key: str):
        """获取值"""
        if self._client:
            try:
                return self._client.get(key)
            except Exception:
                pass

        # 内存降级
        with self._lock:
            self._clean_expired()
            return self._memory_cache.get(key)

    def set(self, key: str, value: str, expire: int = None):
        """设置值（支持过期时间）"""
        if self._client:
            try:
                self._client.set(key, value, ex=expire)
                return
            except Exception:
                pass

        # 内存降级
        with self._lock:
            self._memory_cache[key] = value
            if expire:
                self._memory_expire[key] = time.time() + expire
            else:
                self._memory_expire[key] = None

    def delete(self, key: str):
        """删除键"""
        if self._client:
            try:
                self._client.delete(key)
                return
            except Exception:
                pass

        with self._lock:
            self._memory_cache.pop(key, None)
            self._memory_expire.pop(key, None)

    def lpush(self, key: str, value: str):
        """列表左侧推入"""
        if self._client:
            try:
                self._client.lpush(key, value)
                return
            except Exception:
                pass

        with self._lock:
            if key not in self._memory_cache:
                self._memory_cache[key] = []
            self._memory_cache[key].insert(0, value)

    def lrange(self, key: str, start: int, end: int):
        """获取列表范围"""
        if self._client:
            try:
                return self._client.lrange(key, start, end)
            except Exception:
                pass

        with self._lock:
            lst = self._memory_cache.get(key, [])
            if not isinstance(lst, list):
                return []
            if end == -1:
                return lst[start:]
            return lst[start:end + 1]

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        if self._client:
            try:
                return bool(self._client.exists(key))
            except Exception:
                pass

        with self._lock:
            self._clean_expired()
            return key in self._memory_cache

    def expire(self, key: str, seconds: int):
        """设置键过期时间"""
        if self._client:
            try:
                self._client.expire(key, seconds)
                return
            except Exception:
                pass

        with self._lock:
            if key in self._memory_cache:
                self._memory_expire[key] = time.time() + seconds

    def increment(self, key: str, expire: int | None = None) -> int:
        """原子计数；首次计数时设置 TTL，Redis 不可用则使用进程内计数。"""
        if self._client:
            try:
                value = int(self._client.incr(key))
                if value == 1 and expire:
                    self._client.expire(key, expire)
                return value
            except Exception:
                pass

        with self._lock:
            self._clean_expired()
            value = int(self._memory_cache.get(key, 0)) + 1
            self._memory_cache[key] = value
            if value == 1:
                self._memory_expire[key] = (
                    time.time() + expire if expire else None
                )
            return value


# 全局单例
redis_client = RedisClient()
