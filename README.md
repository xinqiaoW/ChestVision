# 🫁 ChestVision — 胸片X光智能分析系统

> 基于 YOLOv11 的胸部 X 光病灶检测智能体平台 | 西安交通大学

[![Tech](https://img.shields.io/badge/YOLO-v11x-00ADD8)](https://github.com/ultralytics/ultralytics)
[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![Vue](https://img.shields.io/badge/Vue-3.5-green)](https://vuejs.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## 📖 项目简介

ChestVision 是一个以**对话为核心交互方式**的胸部 X 光 AI 辅助诊断平台。用户通过自然语言或快捷按钮上传胸片，系统自动调用 YOLOv11x 模型检测 10 种常见胸部病变，并生成可视化标注结果。

**检测类别（10 种）** ：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸

## 🎯 核心功能

| 功能 | 说明 |
|------|------|
| 🧠 **AI 病灶检测** | 上传胸片，YOLOv11x 自动识别 10 种病变，返回标注图 + 统计 |
| 💬 **智能对话** | 自然语言触发检测 + LLM 分析解读（双通道架构） |
| ⚡ **快捷检测** | 点击按钮直调 API，零延迟，不依赖 LLM |
| 📊 **检测工作台** | 独立检测页面，可视化病灶列表 + 置信度 |
| 🏋️ **模型训练** | 支持在线训练、评估、导出 YOLO 模型 |
| 🔐 **用户权限** | JWT 认证 + RBAC 角色权限管理 |

## 🏗️ 技术栈

```
前端：Vue 3 + Element Plus + Pinia + ECharts
后端：FastAPI + SQLAlchemy + PostgreSQL + Redis + MinIO
AI：  YOLOv11x + LangChain + 通义千问(LLM)
基础设施：Docker Compose (PostgreSQL, Redis, MinIO)
```

## 🚀 快速启动

### 环境要求

- Python 3.11+
- Node.js 18+
- Docker Desktop

### 1. 启动基础设施

```bash
docker-compose up -d
```

### 2. 初始化数据库

```bash
cd backend
pip install -r requirements.txt
alembic upgrade head
python tools/init_db.py
```

### 3. 配置 LLM（可选，用于智能对话）

编辑 `backend/.env`，填入通义千问 API Key：

```env
QWEN_API_KEY=你的API密钥
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

> 快捷检测通道不需要 LLM，可跳过此步。

### 4. 放置模型权重

将训练好的 `best.pt` 放入 `backend/models/`，然后注册：

```bash
python tools/register_trained_model.py
```

### 5. 启动服务

```bash
# 终端1：后端
cd backend && python main.py

# 终端2：前端
cd frontend && npm install && npm run dev
```

访问 http://localhost:5173 ，默认管理员账号：`admin` / `admin123`

## 📁 项目结构

```
ChestVision/
├── backend/                  # FastAPI 后端
│   ├── app/
│   │   ├── agent/           # 智能体模块（ReAct Agent）
│   │   ├── api/             # API 路由（auth/chat/detection/training）
│   │   ├── config/          # 全局配置
│   │   ├── database/        # 数据库连接
│   │   ├── entity/          # ORM 模型 + Pydantic Schema
│   │   ├── middleware/       # 请求日志中间件
│   │   ├── services/        # 业务逻辑（检测/用户）
│   │   ├── storage/         # MinIO 客户端
│   │   └── training/        # 训练服务
│   ├── alembic/             # 数据库迁移
│   ├── models/              # YOLO 模型权重
│   └── tools/               # 工具脚本（数据转换/初始化/注册）
├── frontend/                # Vue 3 前端
│   └── src/
│       ├── api/             # API 封装
│       ├── components/      # 组件（布局/检测卡片）
│       ├── router/          # 路由
│       ├── stores/          # Pinia 状态管理
│       ├── utils/           # 工具函数（SSE/Markdown）
│       └── views/           # 页面（对话/检测/训练/历史/看板）
├── docker-compose.yml       # Docker 基础服务
└── yolo11x_train/           # 训练实验（已 .gitignore）
```

## 📊 数据集

本项目使用 [ChestX-Det10](https://arxiv.org/abs/2006.10550) 数据集，包含 3,543 张胸部 X 光图像，由 3 位认证放射科医师标注 10 种常见胸部病变的边界框。

```bibtex
@misc{liu2020chestxdet10,
    title={ChestX-Det10: Chest X-ray Dataset on Detection of Thoracic Abnormalities},
    author={Jingyu Liu and Jie Lian and Yizhou Yu},
    year={2020},
    eprint={2006.10550v3},
    archivePrefix={arXiv},
    primaryClass={eess.IV}
}
```

## 👥 团队

- 西安交通大学 · 胸片X光多智能体分析系统小组



```
  lianjie@deepwise.com
```

