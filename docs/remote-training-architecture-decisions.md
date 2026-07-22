# 远程训练架构决策记录

本文记录远程训练方案中的关键产品与部署决策。目标是避免后续实现时重新暴露已经决定隐藏的底层细节，或把高负载训练任务部署回 Web 服务环境。

## ADR-001：训练位置与计算设备不应暴露给普通用户

状态：已采纳

日期：2026-07-18

### 决策

训练对用户表现为统一的业务能力，不区分“本地训练”“远程训练”“本地 CPU”“本地 GPU”。

前端不再提供以下用户可选项：

- 本地 CPU 训练
- 本地 GPU 训练
- 远程训练
- 具体设备 ID，例如 `cpu`、`0`、`1`

用户只选择业务相关参数：

- 数据集
- 模型规格或模型大小
- 训练轮数
- 图像尺寸
- batch size
- 数据增强等训练参数

训练实际运行位置、CPU/GPU 规格、PAI-DLC 资源类型由后端调度策略决定。

### 理由

普通用户关心训练结果、耗时、成本和状态，不应该理解训练任务实际运行在哪台机器、哪个容器、哪个云资源上。

如果把“本地/远程/CPU/GPU”暴露为产品选项，会造成几个问题：

- 用户需要理解底层基础设施，增加使用成本。
- 未来调度策略变化会反向影响前端交互。
- 训练资源迁移到 PAI-DLC 后，旧的本地设备选项会误导用户。
- 本地训练与远程训练的状态、指标、模型版本应尽量共用同一套业务视图。

### 实现影响

后端仍然保留内部字段，用于调度和排障：

- `training_backend`: `pai_dlc`、`local_dev` 等
- `compute_profile`: `auto`、`gpu_standard`、`gpu_high`、`cpu_preprocess` 等
- `remote_training_jobs.remote_status`
- PAI-DLC 的 `ecs_spec`、`image_uri`、`resource_id`

现有 `device` 字段可暂时保留兼容旧接口，但不再作为用户输入的核心字段。生产路径中，训练设备由后端根据策略生成。

前端训练页面应删除设备选择控件，训练列表也不应把 `device` 作为主要展示列。可以展示更业务化的信息，例如“训练中”“排队中”“资源准备中”“已完成”。

## ADR-002：训练任务不部署在 Web 服务同一运行环境

状态：已采纳

日期：2026-07-18

### 决策

生产环境中，训练任务不在网站后端所在的同一个容器、物理机器或长期运行环境中执行。

服务器侧只保留：

- Web/API 服务
- 认证、业务状态、数据库访问
- OSS 预签名 URL 签发
- PAI-DLC 任务编排与状态同步
- 小文件指标解析与模型版本登记
- 推理服务和默认模型缓存

数据预处理和模型训练交给 PAI-DLC 执行。

### 理由

训练任务会显著影响 CPU、GPU、内存、磁盘 IO、网络带宽和进程稳定性。如果训练任务与 Web 服务部署在同一个环境中，而系统又没有采用 Kubernetes 这类可按需水平扩展和资源隔离的运行平台，会带来较高风险：

- 训练占满资源导致 API 响应变慢或不可用。
- 训练进程异常影响 Web 服务稳定性。
- 大文件解压、数据转换和日志写入挤占磁盘。
- GPU/CPU 驱动、CUDA、PyTorch、Ultralytics 依赖污染 Web 服务镜像。
- 多用户同时训练时缺少可靠的资源隔离与排队机制。

因此，训练工作负载应被视为异步计算任务，由 PAI-DLC 这类远程计算服务承载。网站后端只负责创建任务、记录状态、同步产物和对外提供统一接口。

### 实现影响

生产环境训练入口应统一走远程训练编排：

```text
Frontend
  -> Backend API
  -> OSS dataset/artifacts
  -> PAI-DLC train jobs
  -> Backend sync metrics/model versions
```

本地训练能力只允许作为开发、调试或应急工具存在，不作为普通用户可选生产能力。

训练产物的权威位置应是 OSS 和数据库元数据：

- 数据集原始压缩包：OSS raw 前缀
- 训练任务数据准备报告：OSS training output 前缀下的 `dataset/validation_report.json`
- 训练结果：OSS training output 前缀
- 指标查询：数据库中的 `training_metrics`
- 模型版本：数据库中的 `model_versions` + OSS artifact key

服务器本地只保留推理所需的模型缓存，例如默认模型 `models/best.pt`。该文件是可再生成缓存，不是远程训练产物的唯一权威副本。

### 非目标

该决策不要求立刻删除现有本地训练代码。短期内可以保留本地训练服务用于开发环境验证，但需要避免继续在产品层面把它作为用户可选能力扩展。

## ADR-003：数据集格式校验并入训练任务第一阶段

状态：已采纳

日期：2026-07-20

### 决策

前端默认使用 OSS 分片上传。上传完成后，客户端 complete 信号只作为 UI 参考；后端只有收到 OSS `oss:ObjectCreated:CompleteMultipartUpload` 事件经 FC/EventBridge 转发后的内部回调，才把数据集状态推进为 `UPLOADED`。

后端暂时不做 OSS `HeadObject` 级别的对象复核，也不启动独立的数据集格式验证、FC 快速检查或 PAI-DLC 预处理任务。`HeadObject` 校验能力保留，后续需要更严格上传确认时再恢复。

YOLO 数据集格式校验、ZIP 安全解压和 `data.yaml` 修正作为 PAI-DLC 训练任务的第一阶段执行：

```text
raw dataset.zip
  -> 训练容器启动
  -> 安全解压
  -> YOLO 格式校验
  -> validation_report.json
  -> 校验成功后训练
  -> 校验失败则训练任务 failed
```

### 理由

数据集格式是否能训练，是训练任务契约的一部分。将校验拆成独立服务会带来额外状态、回调、权限和失败补偿逻辑，同时仍然不能替代训练容器内的最终校验。

把校验并入训练任务可以保证：

- 用户看到的是一个完整训练任务：成则继续训练，败则中断并返回原因。
- Web 后端不承担大 ZIP 解压和图片扫描压力。
- 不需要为 FC 快速检查维护额外依赖、权限和回调链路。
- 训练容器内的真实运行环境负责最终判断，避免“校验环境通过、训练环境失败”的不一致。

### 实现影响

- `dataset_uploads.status=CLIENT_COMPLETED` 表示客户端报告上传完成，但不能提交训练。
- `dataset_uploads.status=UPLOADED` 表示服务端已收到 OSS `CompleteMultipartUpload` 事件，可以提交训练。
- 不再要求数据集进入 `READY` 后才能训练。
- 训练脚本必须在调用 Ultralytics 前写出 `training/jobs/{task_uuid}/dataset/validation_report.json`。
- 后端同步训练结果时，`validation_report.ok=false` 应直接映射为 `training_tasks.status=failed`。
- 原 `datasets/processed/` 只作为后续可选缓存，不是第一期训练前置条件。
