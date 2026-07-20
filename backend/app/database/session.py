"""
数据库连接与会话管理
- 创建 SQLAlchemy 引擎和会话工厂
- 提供 get_db 依赖注入函数，供 API 层使用
"""

from app.config.settings import settings
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# 创建数据库引擎。服务器无 Docker 权限时可通过
# DATABASE_URL_OVERRIDE=sqlite:///... 使用单机 SQLite 部署。
engine_options = {
    "pool_pre_ping": True,
    "echo": settings.DEBUG,
}
if settings.DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    engine_options.update({"pool_size": 10, "max_overflow": 20})

engine = create_engine(settings.DATABASE_URL, **engine_options)

# 会话工厂
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# ORM 模型的基类，所有模型都继承自它
Base = declarative_base()


def get_db():
    """
    获取数据库会话的依赖注入函数
    在 FastAPI 路由中通过 Depends(get_db) 使用

    用法示例：
        @router.get("/xxx")
        def my_api(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
