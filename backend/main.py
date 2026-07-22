from contextlib import asynccontextmanager
from pathlib import Path

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.dashboard import router as dashboard_router
from app.api.detection import router as detection_router
from app.api.doctor_recommendation import router as doctor_recommendation_router
from app.api.health import router as health_router
from app.api.medical_record import router as medical_record_router
from app.api.patient import router as patient_router
from app.api.profile import router as profile_router
from app.api.report import router as report_router
from app.api.training import router as training_router  # 训练 API 路由
from app.api.knowledge import router as knowledge_router  # Day11: 知识库管理 API
from app.config.settings import settings
from app.core.exceptions import register_exception_handlers
from app.middleware.request_logger import RequestLogMiddleware
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def init_minio():
    """初始化 MinIO 存储桶"""
    from app.storage.minio_client import MinIOClient

    try:
        minio_client = MinIOClient()
        print(f"MinIO 存储桶 '{minio_client.bucket_name}' 初始化完成")
    except Exception as e:
        print(f"MinIO 初始化失败: {e}")


def init_knowledge_base():
    """启动时预构建知识库索引"""
    try:
        from app.rag.retriever import knowledge_retriever
        knowledge_retriever.build_index()
        print("知识库索引初始化完成")
    except Exception as e:
        print(f"知识库索引初始化失败（将在首次检索时重试）: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    print("正在初始化服务...")
    init_minio()
    init_knowledge_base()
    yield
    # 关闭时执行（如果需要）
    print("服务已关闭")


# 创建 FastAPI 实例
app = FastAPI(
    title="ChestX AI 智能分析平台",
    version="0.1.0",
    description="基于 YOLOv11 的胸片X光智能分析系统 API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── 注册全局异常处理器 ─────────────────────────────────
register_exception_handlers(app)

# ── CORS 中间件配置 ──────────────────────────────────
# 允许前端跨域请求后端 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. 请求日志中间件（在 CORS 之后执行）
app.add_middleware(RequestLogMiddleware)

# 注册路由
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(training_router)  # 注册训练 API 路由
app.include_router(detection_router)  # 注册检测 API 路由
app.include_router(doctor_recommendation_router)  # AI 医生推荐
app.include_router(chat_router)  # 注册对话 API 路由
app.include_router(patient_router)  # 注册患者管理 API 路由
app.include_router(medical_record_router)  # 注册病例管理 API 路由
app.include_router(dashboard_router)  # 注册数据看板 API 路由
app.include_router(report_router)  # 注册检测报告 API 路由
app.include_router(profile_router)  # 注册个人中心 API 路由
app.include_router(knowledge_router)  # Day11: 注册知识库管理 API 路由


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
if (FRONTEND_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )


@app.get("/")
def root():
    if FRONTEND_INDEX.is_file():
        return FileResponse(FRONTEND_INDEX)
    return {
        "message": "欢迎使用胸片X光智能分析系统",
        "version": "0.1.0",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_spa(full_path: str):
    """用户态部署时由 FastAPI 同源提供已构建的 Vue 页面。"""
    if full_path.startswith("api/") or not FRONTEND_INDEX.is_file():
        raise HTTPException(status_code=404, detail="Not Found")

    candidate = (FRONTEND_DIST / full_path).resolve()
    try:
        candidate.relative_to(FRONTEND_DIST.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(FRONTEND_INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["app"],
    )
