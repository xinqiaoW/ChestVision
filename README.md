# 🫁 ChestVision — 胸片X光智能分析系统

> 基于 YOLOv11 的胸部 X 光 AI 辅助诊断平台 | 医患协同 · 病史感知 · 智能报告

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![Vue](https://img.shields.io/badge/Vue-3.5-green)](https://vuejs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1)](https://postgresql.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 📖 项目简介

ChestVision 是一个**医患协同的胸部 X 光 AI 辅助诊断平台**。系统支持三种用户角色（管理员/医生/病人），集成 YOLOv11 深度学习模型自动检测 10 种常见胸部病变，并通过大语言模型（通义千问）结合患者历史病例进行综合分析，生成结构化诊断报告。

**检测类别**：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸

---

## 👥 三种用户角色

| 角色 | 核心能力 |
|------|----------|
| ⚙️ **管理员** | 管理用户、分配医患关系、查看全部数据、统计分析 |
| 👨‍⚕️ **医生** | 管理分配的 patients、编辑病例病史、AI 综合分析、生成报告 |
| 👤 **病人** | 上传个人胸片、查看检测结果和 AI 分析、查看自己的病例和报告 |

---

## 🎯 功能矩阵

### 🩻 病灶检测
| 功能 | 说明 |
|------|------|
| 单图检测 | 上传一张胸片，YOLOv11 自动识别病变并绘制标注框 |
| 批量检测 | 多张胸片批量处理，汇总统计 |
| ZIP 检测 | 上传 ZIP 压缩包，自动解压并批量检测 |
| 标注可视化 | 检测框叠加原图，病灶类别/置信度/位置一目了然 |

### 🤖 AI 智能分析
| 功能 | 说明 |
|------|------|
| 病史感知分析 | 检测时自动拉取患者历史病例 + 历史检测，AI 综合对比判断 |
| 风险评级 | 自动评估 low / medium / high / critical 四级风险 |
| 智能对话 | 自然语言提问，AI 实时回答（支持 SSE 流式输出） |
| 上下文感知 | 对话自动注入患者病史，追问时 AI 结合历史信息回答 |
| 系统查询 | 询问"有几个病人""我的病人有哪些"，AI 按权限回答 |
| AI 医生推荐 | 结合病灶、对话身份信息、医生自述与历史记录匹配医生 |
| 管理员审核 | 用户选择医生后进入待确认队列，管理员确认后才建立医患关系 |

### 📋 病例管理
| 功能 | 说明 |
|------|------|
| 结构化病例 | 主诉、现病史、既往史、家族史、体格检查、诊断、治疗方案 |
| 病例编辑 | 医生创建/编辑所管病人病例，支持草稿/完成/审核状态切换 |
| 病例查看 | 病人可查看自己的全部病例记录 |

### 📊 数据统计
| 功能 | 说明 |
|------|------|
| 总览卡片 | 检测总次数、病灶总数、平均耗时、高风险案例数 |
| 趋势图表 | 近 7 天检测量折线图 |
| 病灶分布 | 10 种病变检出占比饼图 |
| 风险分布 | 风险等级柱状图 |
| 医生工作量 | 每位医生的病人数、检测数、检出病灶数（管理员可见） |

### 📝 检测报告
| 功能 | 说明 |
|------|------|
| 一键生成 | 对话中说"生成报告"，自动生成结构化诊断报告 |
| 报告内容 | 患者信息 + 检测结果表 + AI 综合分析 + 风险评级 + 建议 |

### 🔐 权限与安全
| 功能 | 说明 |
|------|------|
| 三种用户类型 | 公开注册仅允许 doctor / patient；admin 由系统初始化或管理员创建 |
| RBAC 权限 | 角色-权限精细化控制 |
| 医患关系 | 管理员分配病人给医生，数据按权限隔离 |
| 侧边栏差异化 | 不同角色看到不同菜单 |
| JWT 认证 | Token 登录，自动续期 |

---

## 🏗️ 技术栈

```
前端    Vue 3 + Element Plus + Pinia + ECharts + Markdown
后端    FastAPI + SQLAlchemy 2.0 + Alembic + LangChain
AI      YOLOv11 (Ultralytics) + 通义千问 (Qwen-plus)
数据库   PostgreSQL 15 + Pgvector
缓存    Redis 7
存储    MinIO
部署    Docker Compose
```

---

## 🗄️ 数据库（25 张表）

| 模块 | 表 |
|------|-----|
| 用户与角色 | `users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `doctor_patient_relations`, `doctor_recommendations` |
| 检测业务 | `detection_scenes`, `detection_tasks`, `detection_results` |
| 模型管理 | `training_tasks`, `training_metrics`, `model_versions` |
| 智能对话 | `chat_sessions`, `chat_messages`, `chat_message_attachments` |
| Agent 管理 | `agent_registry`, `agent_invocations`, `agent_teams` |
| 患者与病例 | `patient_profiles`, `medical_records`, `medical_record_attachments`, `cxr_images`, `detection_reports` |
| 系统运维 | `operation_logs` |

---

## 🚀 快速启动

### 环境要求

- Windows 10/11
- Docker Desktop（WSL 2 后端）

无需在 Windows 单独安装 Python、Node.js、PostgreSQL、Redis 或 MinIO。

### 一键启动

双击项目根目录的 `start.bat`。首次启动会自动完成：

1. 生成本地 `backend/.env` 和随机 JWT 密钥；
2. 构建前端与后端镜像；
3. 启动 PostgreSQL、Redis、MinIO、FastAPI 和 Vue/Nginx；
4. 执行数据库迁移，初始化管理员、检测场景和本地模型记录；
5. 打开 http://localhost:5173 。

默认本地管理员：`admin` / `admin123`。首次启动后请及时修改密码。

常用地址：

- Web：http://localhost:5173
- API 文档：http://localhost:8000/docs
- MinIO 控制台：http://localhost:9001

停止服务请双击 `stop.bat`，数据库与对象存储数据会保留。

### 模型与 LLM

- 检测模型放置于 `backend/models/best.pt`，启动时会自动登记到数据库。
- 对话功能需要在 `backend/.env` 中填写 `QWEN_API_KEY` 或 `OPENAI_API_KEY`。
- 单图/批量/ZIP 检测不依赖 LLM，但必须存在模型权重。

### 命令行方式

```powershell
# 启动或更新
powershell -ExecutionPolicy Bypass -File scripts/start.ps1

# 查看实时日志
powershell -ExecutionPolicy Bypass -File scripts/logs.ps1

# 停止（保留数据）
powershell -ExecutionPolicy Bypass -File scripts/stop.ps1
```

### Linux 服务器用户态部署

当账号没有 Docker 权限时，可使用 SQLite 与内存缓存启动完整网页；生产数据量较大时仍建议由服务器管理员授予 Docker 权限，改用 PostgreSQL/Redis/MinIO：

```bash
# 首次安装依赖、构建前端并初始化数据库
bash scripts/server-install.sh

# 启动 FastAPI/Vue 与 Cloudflare HTTPS 临时隧道
bash scripts/server-start.sh

# 停止服务
bash scripts/server-stop.sh
```

`server-start.sh` 返回的 `trycloudflare.com` 地址可直接从网络访问，但属于临时审核地址，隧道重启后可能变化。正式长期部署应配置自有域名或由服务器管理员开放反向代理端口。

### QQ 邮箱注册验证码

公开注册默认要求邮箱验证码。先在 QQ 邮箱设置中开启 SMTP 服务并生成授权码，随后在 `backend/.env` 填写：

```env
EMAIL_VERIFICATION_REQUIRED=true
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=your-account@qq.com
SMTP_PASSWORD=your-smtp-authorization-code
SMTP_FROM_EMAIL=your-account@qq.com
SMTP_FROM_NAME=ChestVision
SMTP_USE_SSL=true
```

授权码不是 QQ 登录密码，不应提交到 Git。修改配置后需要重启后端服务。

## 📁 项目结构

```
ChestVision/
├── backend/
│   ├── main.py                  # FastAPI 入口
│   ├── app/
│   │   ├── agent/               # 智能体（ReAct Agent + 工具）
│   │   ├── api/                 # API 路由
│   │   │   ├── auth.py          # 认证（注册/登录）
│   │   │   ├── chat.py          # 对话（SSE 流式 + 病史注入）
│   │   │   ├── detection.py     # 检测（YOLO + 入库 + AI分析）
│   │   │   ├── patient.py       # 患者管理
│   │   │   ├── medical_record.py # 病例管理
│   │   │   ├── dashboard.py     # 统计看板
│   │   │   └── report.py        # 报告生成
│   │   ├── config/              # 全局配置
│   │   ├── core/                # 安全/日志/异常
│   │   ├── database/            # 数据库连接
│   │   ├── entity/              # ORM 模型 + Schema
│   │   ├── services/            # 检测服务 + 用户服务
│   │   └── storage/             # MinIO 客户端
│   ├── alembic/                 # 数据库迁移
│   ├── models/                  # YOLO 权重
│   └── tools/                   # 工具脚本
├── frontend/
│   └── src/
│       ├── views/
│       │   ├── ChatPage.vue           # 智能对话（核心页面）
│       │   ├── DetectionPage.vue      # 检测工作台
│       │   ├── HistoryPage.vue        # 历史记录
│       │   ├── DashboardPage.vue      # 数据看板
│       │   ├── PatientManagePage.vue  # 患者管理
│       │   ├── MedicalRecordPage.vue  # 病例管理
│       │   ├── RegisterPage.vue       # 注册
│       │   └── LoginPage.vue          # 登录
│       ├── components/          # 组件
│       ├── stores/              # Pinia 状态
│       └── utils/               # 工具函数
├── docker-compose.yml
└── yolo11x_train/               # 训练实验
```

---

## 📊 数据集

本项目基于 [ChestX-Det10](https://arxiv.org/abs/2006.10550) 数据集训练，包含 3,543 张胸部 X 光图像，由 3 位认证放射科医师标注。

---

## 👥 团队

西安交通大学 · 胸片X光多智能体分析系统小组
