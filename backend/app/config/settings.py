"""
全局配置模块
使用 pydantic-settings 管理所有配置项，支持从 .env 文件和环境变量读取
加载优先级：环境变量（系统级别）> .env 文件 > 代码中的默认值
"""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用全局配置"""

    # ── 训练配置 ──────────────────────────────────────
    TRAIN_OUTPUT_DIR: str = "runs/train"  # 训练输出目录（模型权重、日志等）
    DATASET_BASE_DIR: str = "datasets"  # 数据集根目录

    # ── 应用基础配置 ──────────────────────────────────
    APP_NAME: str = "ChestX AI Platform"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    # 在 Settings 类中添加日志相关配置（已有 LOG_LEVEL，再补充文件配置）

    # ── 日志配置 ──────────────────────────────────────
    # LOG_LEVEL: str = "INFO"           # 已有，日志级别
    LOG_DIR: str = "logs"  # 日志目录（相对于 backend/）
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 单文件最大 10MB
    LOG_BACKUP_COUNT: int = 5  # 保留 5 份历史日志

    # ── 数据库配置 ────────────────────────────────────
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "chestx_agent"
    DB_USER: str = "chestx_admin"
    DB_PASSWORD: str = "chestx_admin"
    DATABASE_URL_OVERRIDE: str = ""

    @property
    def DATABASE_URL(self) -> str:
        """优先使用显式连接串，否则构造 PostgreSQL 连接字符串。"""
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # ── Redis 配置 ────────────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @property
    def REDIS_URL(self) -> str:
        """构造 Redis 连接字符串"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # ── MinIO 配置 ────────────────────────────────────
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "chestx-agent-images"
    MINIO_SECURE: bool = False

    # ── JWT 认证配置 ──────────────────────────────────
    JWT_SECRET_KEY: str = "your-super-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ── 邮箱验证码（QQ SMTP 默认配置）────────────────
    EMAIL_VERIFICATION_REQUIRED: bool = True
    SMTP_HOST: str = "smtp.qq.com"
    SMTP_PORT: int = 465
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "ChestVision"
    SMTP_USE_SSL: bool = True
    SMTP_USE_STARTTLS: bool = False
    SMTP_TIMEOUT_SECONDS: int = 15
    EMAIL_CODE_EXPIRE_MINUTES: int = 10
    EMAIL_CODE_RESEND_SECONDS: int = 60
    EMAIL_CODE_MAX_ATTEMPTS: int = 5
    EMAIL_CODE_MAX_PER_EMAIL_PER_HOUR: int = 5
    EMAIL_CODE_MAX_PER_IP_PER_HOUR: int = 20

    # ── 训练配置 ──────────────────────────────────────
    TRAIN_OUTPUT_DIR: str = "runs/train"  # 训练输出目录（模型权重、日志等）
    DATASET_BASE_DIR: str = "datasets"  # 数据集根目录

    # ── CORS 配置 ────────────────────────────────────
    ALLOWED_ORIGINS: str = (
        "http://localhost:3000,http://localhost:5173,http://localhost:8080"
    )

    # ── LLM 配置（Day 8 新增）─────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"

    # ── RAG / Embedding 配置（Day 11 新增）─────────────
    EMBEDDING_MODEL: str = "text-embedding-v3"   # 通义千问 Embedding 模型
    EMBEDDING_DIM: int = 1024                     # 向量维度（text-embedding-v3=1024）
    RAG_CHUNK_SIZE: int = 500                     # 文档分块大小
    RAG_CHUNK_OVERLAP: int = 50                   # 分块重叠字符数
    RAG_TOP_K: int = 3                            # 检索返回 Top-K 条
    RAG_SIMILARITY_THRESHOLD: float = 0.35        # 知识库检索相似度阈值（统一配置）

    @property
    def cors_origins_list(self) -> list:
        """将 CORS 配置字符串转为列表"""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        # 始终从 backend/.env 加载，不受 CWD 影响
        env_file = str(Path(__file__).resolve().parent.parent.parent / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"


# 创建全局单例，其他模块直接 import 使用
settings = Settings()
