# Remote Training Backend Plan: OSS + PAI-DLC

本文档设计服务端后端改造方案：在现有本地 YOLOv11 训练能力之外，新增基于阿里云 OSS 的远程数据集存储与基于 PAI-DLC 的远程训练计算实例。

## 1. 背景与目标

现有后端已经具备：

- `backend/app/api/training.py`：本地训练、任务列表、状态、指标、评估、导出、下载、数据集上传。
- `backend/app/training/training_service.py`：后台线程启动本地 Ultralytics YOLO 训练，解析 `results.csv` 写入 `training_metrics`。
- `training_tasks`、`training_metrics`、`model_versions`：训练任务、训练指标、模型版本三张核心表。
- `MinIOClient`：用于本地/私有对象存储的模型和检测产物上传。

远程训练新增目标：

- 大数据集不经过应用服务器转发，浏览器直传 OSS。
- 后端只创建上传会话、初始化 OSS multipart、按批签发 part URL、记录客户端完成信号、接收 OSS `CompleteMultipartUpload` 事件并驱动状态流转；长期 AccessKey 只保存在后端。当前代码默认 multipart，保留短期 PUT 预签名 URL 作为测试/兼容路径。OSS `HeadObject` 校验能力保留，但当前阶段暂不启用。
- 使用 EventBridge/FC 和服务端定时 Worker 处理事件遗漏、重复通知和僵死状态。
- 数据集格式校验、ZIP 安全解压和 YOLO 训练作为同一个 PAI-DLC 训练任务的连续阶段执行；校验成功才进入训练，校验失败则训练任务直接失败。
- 继续复用现有 `training_tasks`、`training_metrics`、`model_versions` 作为业务主视图，避免前端训练页大改。

非目标：

- 不在第一期实现前端上传组件。
- 不把 OSS 临时文件下载回应用服务器处理。
- 不让 OSS Callback 直接执行重型业务逻辑。
- 不再设置独立的数据集格式验证服务、FC 快速检查或上传后预处理 Job。
- 不在本方案中实现 EAS 部署。

## 2. 外部约束与设计依据

- OSS 支持浏览器/客户端直传，目标是绕过应用服务器，减少带宽和服务器负载。
- 客户端上传使用后端生成的短期 part PUT 预签名 URL，不能把长期 AccessKey 或 STS token 暴露给浏览器。
- 大文件推荐分片/断点续传。`CompleteMultipartUpload` 完成后会产生完整对象。
- OSS Callback 可在对象上传成功后通知应用服务器，但回调需要在短时间内返回，失败不会影响对象上传，也不会自动重试。因此它只适合轻量确认，不适合作为唯一可靠事件源。
- EventBridge 支持 OSS 事件，包括 `oss:ObjectCreated:CompleteMultipartUpload`，适合作为服务端异步编排入口。
- PAI-DLC 可创建单机或分布式训练任务，CreateJob 支持指定 `JobType`、`JobSpecs`、`UserCommand`、数据源等配置。

参考文档见本文末尾。

## 3. 总体架构

推荐采用两段式架构：

1. 上传段：后端创建 upload_id 和固定 OSS Object Key，浏览器使用 OSS SDK 分片直传。
2. 训练段：用户选择已上传的数据集后，后端创建 `training_tasks`，提交 PAI-DLC 训练任务；训练容器先做 ZIP 安全解压和 YOLO 数据集格式校验，成功后继续 Ultralytics 训练，失败则中断并回写失败原因。

建议部署组件：

```text
Browser
  -> Backend API: 创建上传会话、获取上传凭据、complete、查询状态
  -> OSS: 分片上传 dataset.zip

OSS
  -> OSS Callback: 可选，轻量通知 Backend
  -> EventBridge: raw ObjectCreated:CompleteMultipartUpload / training _SUCCESS

EventBridge
  -> FC Orchestrator: 校验事件、调用 Backend 内部 API、提交或补偿任务

Backend
  -> PostgreSQL: 任务状态、幂等事件、指标、模型版本
  -> OSS: 签发上传凭据、读取 validation_report、下载小型结果 JSON；HeadObject 能力保留
  -> PAI-DLC: CreateJob、GetJob、Stop/DeleteJob
  -> Redis/Worker: 周期对账、状态推进、锁

PAI-DLC Train Job
  -> OSS: 读取 raw dataset.zip
  -> Container: 安全解压、YOLO 格式校验、修正 data.yaml
  -> Ultralytics: 校验通过后训练
  -> OSS: 写 validation_report.json、results.csv、best.pt、metrics.json、_SUCCESS
  -> Backend callback: 训练结束主动通知
```

## 4. OSS Key 规范

所有对象 Key 必须由后端生成，前端不得传入任意路径。

```text
{oss_prefix}/{user_id}/{upload_id}/dataset.zip
{oss_prefix}/{user_id}/{upload_id}/client.json

train/jobs/{task_uuid}/
  code/                  # 可选：训练脚本快照
  logs/
  dataset/
    validation_report.json
  results.csv
  weights/best.pt
  weights/last.pt
  metrics.json
  eval_report.json
  _SUCCESS

{oss_prefix}/models/{scene_name}/{version}/
  best.pt
  eval_report.json
```

Key 设计原则：

- `upload_id`、`dataset_id`、`task_uuid` 使用 UUID 或数据库主键派生值，避免覆盖。
- raw 区只允许 ZIP 或约定归档文件。
- `datasets/processed/` 不再作为训练前置目录；如后续为了调试或复用缓存写入 processed 副本，也不能作为用户启动训练的前置状态。
- 训练输出也使用 `_SUCCESS`，防止只看到部分产物就误判完成。
- `validation_report.json` 记录训练任务第一阶段的数据集校验结果；校验失败时也应写入，便于后端同步失败原因。

## 5. 状态机设计

### 5.1 数据集上传状态

新增 `dataset_uploads.status`：

```text
INITIATED
  -> UPLOADING              # 可选，仅由前端心跳展示，不作为可信状态
  -> CLIENT_COMPLETED       # 前端调用 complete，仍不可信
  -> UPLOADED               # 仅由 OSS CompleteMultipartUpload 事件推进，可作为训练输入
  -> FAILED
  -> EXPIRED
  -> CANCELLED
```

可信流转规则：

- `UPLOADING` 只由心跳更新，用于 UI 进度，不驱动后续业务。
- `CLIENT_COMPLETED` 不代表 OSS 对象可信存在，也不能启动训练。
- 当前阶段不信任客户端 complete 信号推进 `UPLOADED`。
- `UPLOADED` 只由 OSS `CompleteMultipartUpload` 事件经 FC/EventBridge 内部回调推进，不表示后端已通过 OSS `HeadObject` 复核，也不表示 YOLO 格式正确。
- `HeadObject` 校验字段和 OSS 网关能力保留，后续需要更严格上传确认时可恢复。
- YOLO 格式校验归属训练任务第一阶段，不再把数据集单独推进到 `READY`。

### 5.2 上传确认原则

客户端上传 SDK 成功只说明客户端本地流程走完，不能证明 OSS 中的对象已经按后端指定 key 完整落盘，也不能防止恶意客户端伪造完成请求。因此 `/uploads/{id}/complete` 只记录 `CLIENT_COMPLETED`，用于前端显示“等待服务器确认”。

服务端只信任来自 OSS 侧的完成事件。生产前端默认使用分片上传，后端只处理 `oss:ObjectCreated:CompleteMultipartUpload`：EventBridge 触发 FC，FC 使用 `Authorization: Bearer <REMOTE_TRAINING_CALLBACK_SECRET>` 调用后端内部接口，后端按数据库中保存的 `bucket + raw_object_key` 精确匹配上传会话，匹配成功后才幂等推进到 `UPLOADED`。回调逻辑不写死测试 bucket、接入点 alias 或 object key 前缀。

### 5.3 远程训练状态

继续使用 `training_tasks.status` 作为业务主状态：

```text
pending       # 本地/远程任务均可复用
submitted     # 远程新增：DLC CreateJob 成功，尚未运行
queued        # 远程新增：DLC 排队
running
completed
failed
cancelled
```

如果不希望修改现有枚举说明，可以让 `training_tasks.status` 仍使用 `pending/running/completed/failed/cancelled`，把 PAI-DLC 细状态放在 `remote_training_jobs.remote_status`。推荐后者，兼容现有前端。

`remote_training_jobs.remote_status`：

```text
CREATED
SUBMITTED
QUEUED
RUNNING
SUCCEEDED
FAILED
STOPPED
UNKNOWN
```

## 6. 数据库迁移计划

### 6.1 扩展现有表

对 `training_tasks` 增加少量通用字段：

```text
training_backend      VARCHAR(20)  DEFAULT 'local'  # local / pai_dlc
remote_dataset_id     INTEGER NULL
remote_output_prefix  VARCHAR(500) NULL
last_synced_at        DATETIME NULL
```

也可以不改 `training_tasks`，将全部远程字段放入 `remote_training_jobs`。第一期建议至少加 `training_backend`，便于列表页区分。

对 `model_versions` 增加 OSS URL 字段，避免把阿里云 OSS 写入 `minio_url`：

```text
oss_model_key         VARCHAR(500) NULL
oss_model_url         VARCHAR(1000) NULL
artifact_manifest     JSON NULL
```

### 6.2 新增 dataset_uploads

```text
dataset_uploads
  id                  BIGSERIAL PK
  upload_uuid         VARCHAR(64) UNIQUE NOT NULL
  dataset_uuid        VARCHAR(64) UNIQUE NULL
  user_id             INTEGER NOT NULL FK users.id
  scene_id            INTEGER NULL FK detection_scenes.id
  dataset_name        VARCHAR(100) NOT NULL
  status              VARCHAR(30) NOT NULL

  bucket              VARCHAR(128) NOT NULL
  raw_object_key      VARCHAR(500) NOT NULL
  processed_prefix    VARCHAR(500) NULL   # 兼容旧设计/可选缓存，不作为训练前置条件
  manifest_key        VARCHAR(500) NULL   # 兼容旧设计/可选缓存
  success_key         VARCHAR(500) NULL   # 兼容旧设计/可选缓存

  original_filename   VARCHAR(255) NOT NULL
  content_type        VARCHAR(100) NULL
  expected_size       BIGINT NULL
  actual_size         BIGINT NULL
  etag                VARCHAR(128) NULL
  checksum_sha256     VARCHAR(128) NULL
  metadata            JSON NULL

  client_progress     INTEGER DEFAULT 0
  client_completed_at DATETIME NULL
  server_verified_at  DATETIME NULL       # 当前记录收到 OSS 完成事件时间；后续也可记录 HeadObject 校验通过时间
  ready_at            DATETIME NULL       # 兼容旧设计；新流程一般不使用

  preprocessing_job_id VARCHAR(128) NULL  # 兼容旧设计；新流程一般不提交独立预处理 Job
  error_message       TEXT NULL
  expires_at          DATETIME NOT NULL
  created_at          DATETIME NOT NULL
  updated_at          DATETIME NOT NULL
```

索引：

- `upload_uuid` 唯一索引。
- `(user_id, status, created_at)`。
- `(bucket, raw_object_key)` 唯一索引。
- `preprocessing_job_id` 普通索引。

### 6.3 新增 remote_training_jobs

```text
remote_training_jobs
  id                  BIGSERIAL PK
  task_id             INTEGER UNIQUE NOT NULL FK training_tasks.id
  dataset_upload_id   INTEGER NOT NULL FK dataset_uploads.id

  provider            VARCHAR(20) DEFAULT 'aliyun'
  workspace_id        VARCHAR(128) NOT NULL
  resource_id         VARCHAR(128) NULL
  dlc_job_id          VARCHAR(128) UNIQUE NULL
  remote_status       VARCHAR(30) NOT NULL

  region              VARCHAR(64) NOT NULL
  image_uri           VARCHAR(500) NOT NULL
  job_type            VARCHAR(50) DEFAULT 'PyTorchJob'
  ecs_spec            VARCHAR(100) NULL
  pod_count           INTEGER DEFAULT 1
  user_command        TEXT NOT NULL
  envs                JSON NULL

  input_dataset_prefix VARCHAR(500) NOT NULL
  output_prefix        VARCHAR(500) NOT NULL
  success_key          VARCHAR(500) NULL
  results_csv_key      VARCHAR(500) NULL
  best_weight_key      VARCHAR(500) NULL

  callback_token_hash  VARCHAR(128) NOT NULL
  submitted_at         DATETIME NULL
  completed_at         DATETIME NULL
  last_synced_at       DATETIME NULL
  error_message        TEXT NULL
  created_at           DATETIME NOT NULL
  updated_at           DATETIME NOT NULL
```

### 6.4 后续可选新增 remote_events

用于 OSS Callback、EventBridge、FC、DLC callback 的幂等去重和审计。当前最小实现尚未创建该表，OSS multipart 事件先写入 `dataset_uploads.metadata`，后续需要完整审计时再补。

```text
remote_events
  id                  BIGSERIAL PK
  event_uuid          VARCHAR(128) UNIQUE NOT NULL
  source              VARCHAR(50) NOT NULL   # oss_callback / eventbridge / dlc_callback / reconciler
  event_type          VARCHAR(100) NOT NULL
  bucket              VARCHAR(128) NULL
  object_key          VARCHAR(500) NULL
  related_type        VARCHAR(30) NULL       # upload / dataset / training
  related_id          INTEGER NULL
  payload             JSON NOT NULL
  status              VARCHAR(30) NOT NULL   # received / processed / ignored / failed
  error_message       TEXT NULL
  received_at         DATETIME NOT NULL
  processed_at        DATETIME NULL
```

幂等键建议：

- EventBridge：使用事件 `id`。
- OSS Callback：使用 `bucket + object + etag + operation`。
- DLC Callback：使用 `dlc_job_id + status + completed_at`。

## 7. 配置项

当前实现为减少对旧 `Settings` 的侵入，远程训练模块直接读取环境变量；后续配置稳定后可再收敛进统一 Settings。核心变量如下：

```text
# Aliyun common fallback
ALIBABA_CLOUD_ACCESS_KEY_ID=
ALIBABA_CLOUD_ACCESS_KEY_SECRET=
ALIBABA_CLOUD_SECURITY_TOKEN=

# OSS
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_SECURITY_TOKEN=
OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
OSS_REGION=cn-beijing
OSS_BUCKET=
REMOTE_TRAIN_OSS_PREFIX=remote-training
OSS_UPLOAD_URL_EXPIRES_SECONDS=900
REMOTE_TRAINING_CALLBACK_SECRET=

# PAI-DLC
PAI_ACCESS_KEY_ID=
PAI_ACCESS_KEY_SECRET=
PAI_SECURITY_TOKEN=
PAI_REGION_ID=cn-beijing
PAI_DLC_ENDPOINT=
PAI_WORKSPACE_ID=
PAI_RESOURCE_ID=
PAI_IMAGE_URI=
PAI_JOB_TYPE=PyTorchJob
PAI_ECS_SPEC=
PAI_POD_COUNT=1
PAI_JOB_MAX_RUNNING_MINUTES=180
PAI_DATASET_MOUNT_PATH=/mnt/dataset
PAI_OUTPUT_MOUNT_PATH=/mnt/output

# ACR
ACR_DOCKER_REGISTRY=
ACR_USERNAME=
ACR_PASSWORD=
```

凭证要求：

- 应用服务器使用 RAM 用户或 RAM Role，权限最小化，负责生成上传凭据。
- 浏览器只拿短期上传凭据，不能拿 AccessKey、SecretKey 或 STS token。
- 每个上传凭据只绑定单个 `{oss_prefix}/{user_id}/{upload_id}/dataset.zip` object key。
- PAI-DLC 运行角色需要读 raw dataset.zip，写 training output 和 models 前缀。

## 8. 后端模块划分

新增模块：

```text
backend/app/storage/oss_client.py
  - init_multipart_upload(upload)
  - create_presigned_part_urls(upload, part_numbers)
  - complete_multipart_upload(upload, parts)
  - abort_multipart_upload(upload)
  - create_presigned_put_url(upload)
  - head_object(bucket, key)
  - get_object_json(bucket, key)
  - list_objects(prefix)
  - build_signed_get_url(key)

backend/app/cloud/pai_dlc_client.py
  - create_job(payload)
  - get_job(job_id)
  - stop_job(job_id)
  - normalize_status(raw_status)

backend/app/training/dataset_upload_service.py
  - initiate_upload()
  - record_client_heartbeat()
  - client_complete()
  - verify_uploaded_object()
  - mark_uploaded()

backend/app/training/remote_training_service.py
  - start_remote_training()
  - build_dlc_train_payload()
  - handle_dlc_callback()
  - poll_remote_job()
  - sync_training_artifacts()
  - export_remote_model_version()

backend/app/api/remote_training.py
  - 上传会话 API
  - 远程训练 API
  - OSS/EventBridge/DLC 回调 API

backend/app/workers/remote_reconcile_worker.py
  - reconcile_uploads()
  - reconcile_training_jobs()

backend/scripts/dlc/train_yolo_remote.py
  - 数据集安全解压、YOLO 格式校验、DLC 训练入口
```

`backend/main.py` 注册：

```python
from app.api.remote_training import router as remote_training_router
app.include_router(remote_training_router)
```

## 9. API 设计

### 9.1 创建上传会话

`POST /api/training/remote/uploads`

请求：

```json
{
  "dataset_name": "chest_xray_v2",
  "scene_id": 2,
  "filename": "dataset.zip",
  "content_type": "application/zip",
  "expected_size": 2147483648,
  "upload_mode": "multipart",
  "part_size": 33554432
}
```

响应：

```json
{
  "upload_id": "upl_xxx",
  "bucket": "bucket-name",
  "object_key": "{oss_prefix}/{user_id}/{upload_id}/dataset.zip",
  "expires_at": "2026-07-18T10:00:00+08:00",
  "upload_mode": "multipart",
  "server_confirm_event": "oss:ObjectCreated:CompleteMultipartUpload",
  "multipart": {
    "oss_upload_id": "oss-multipart-upload-id",
    "part_size": 33554432,
    "max_parts": 10000,
    "max_parts_per_sign": 50,
    "headers": {},
    "sign_parts_endpoint": "/api/training/remote/uploads/upl_xxx/multipart/parts/sign",
    "complete_endpoint": "/api/training/remote/uploads/upl_xxx/multipart/complete",
    "abort_endpoint": "/api/training/remote/uploads/upl_xxx/multipart/abort"
  },
  "upload": null
}
```

说明：默认 `upload_mode = multipart`。当前代码仍保留 `upload_mode = presigned_put` 和 HTTP `PUT` 预签名 URL，便于小文件和真实 OSS 连通性测试；服务端上传状态确认逻辑只按 `CompleteMultipartUpload` 事件推进。

服务端行为：

- 校验 `dataset_name`，仅允许字母、数字、下划线、短横线。
- 校验文件类型、大小上限。
- 创建 `dataset_uploads`，状态为 `INITIATED`。
- 生成固定 Object Key。
- 后端调用 OSS `InitiateMultipartUpload`，把 OSS `upload_id` 和 part_size 写入 `dataset_uploads.metadata`。
- 返回分片上传参数和分片签名接口地址。
- 前端根据 `total_parts = ceil(file_size / part_size)` 计算总分片数。
- 每个 part 都需要单独签名，签名 URL 绑定 `object_key + uploadId + partNumber`。
- Object Key 由创建上传会话时生成并保存到数据库；FC 回调时按 bucket + object_key 精确匹配，不在回调接口中写死测试前缀。

### 9.2 批量签发分片 URL

`POST /api/training/remote/uploads/{upload_id}/multipart/parts/sign`

请求：

```json
{
  "part_numbers": [1, 2, 3],
  "expires_seconds": 900
}
```

响应：

```json
{
  "upload_id": "upl_xxx",
  "upload_mode": "multipart",
  "oss_upload_id": "oss-multipart-upload-id",
  "part_size": 33554432,
  "parts": [
    {
      "part_number": 1,
      "method": "PUT",
      "url": "https://bucket.oss-cn-hangzhou.aliyuncs.com/...?partNumber=1&uploadId=...",
      "headers": {},
      "expires_seconds": 900
    }
  ]
}
```

规则：

- 单次最多签 `50` 个 part，避免响应过大和 URL 过早过期。
- 前端建议每批请求 `20-50` 个 part URL，并发上传 `3-6` 个 part。
- part 上传成功后，前端必须保存 OSS 响应头里的 `ETag`。
- URL 过期或某个 part 失败时，只重新请求失败 part 的签名 URL。

### 9.3 合并分片

`POST /api/training/remote/uploads/{upload_id}/multipart/complete`

请求：

```json
{
  "parts": [
    {"part_number": 1, "etag": "\"etag-part-1\""},
    {"part_number": 2, "etag": "\"etag-part-2\""}
  ]
}
```

服务端行为：

- 校验上传会话是 multipart 模式。
- 如果 `expected_size` 存在，按 `ceil(expected_size / part_size)` 校验 part 数量。
- 后端调用 OSS `CompleteMultipartUpload` 合并分片，客户端不直接持有合并权限。
- 合并接口成功后只把状态记为 `CLIENT_COMPLETED`。
- 等待 OSS `oss:ObjectCreated:CompleteMultipartUpload` 事件经 FC/EventBridge 回调后，才推进 `UPLOADED`。

### 9.4 取消分片上传

`POST /api/training/remote/uploads/{upload_id}/multipart/abort`

用途：

- 用户取消上传或前端确认不再续传时，后端调用 OSS `AbortMultipartUpload` 清理未合并分片。
- 状态变为 `CANCELLED`。
- 如果已经 `UPLOADED/READY`，不允许取消。

### 9.5 上传进度心跳

`POST /api/training/remote/uploads/{upload_id}/heartbeat`

请求：

```json
{
  "progress": 37,
  "uploaded_bytes": 807403520,
  "client_upload_id": "oss-sdk-multipart-id"
}
```

说明：

- 心跳不可信，只更新 UI 进度。
- 不触发 `UPLOADED`，也不触发训练任务创建。

### 9.6 前端上传完成确认

`POST /api/training/remote/uploads/{upload_id}/complete`

请求：

```json
{
  "etag": "optional",
  "size": 2147483648
}
```

服务端行为：

- 如果上传会话已是 `FAILED/EXPIRED/CANCELLED`，返回 400，不允许客户端信号复活终态记录。
- 状态更新为 `CLIENT_COMPLETED`。
- 当前阶段不调用 `HeadObject` 校验对象。
- 不进入 `UPLOADED`。
- 不在该接口中做 YOLO 格式检查、解压或提交预处理 Job。
- 等待 OSS `CompleteMultipartUpload` 事件经 FC/EventBridge 回调后推进 `UPLOADED`。

响应：

```json
{
  "upload_id": "upl_xxx",
  "status": "CLIENT_COMPLETED",
  "message": "客户端上传完成，等待 OSS 分片上传完成事件确认"
}
```

说明：multipart 默认不需要调用该接口；前端应调用 `/multipart/complete`。该接口主要保留给 SDK 已自行完成上传的场景和兼容测试。

### 9.7 查询上传/数据集状态

`GET /api/training/remote/uploads/{upload_id}`

响应：

```json
{
  "upload_id": "upl_xxx",
  "status": "UPLOADED",
  "dataset_id": "ds_xxx",
  "dataset_name": "chest_xray_v2",
  "raw_object_key": "{oss_prefix}/{user_id}/{upload_id}/dataset.zip",
  "actual_size": 2147483648,
  "etag": "...",
  "client_completed_at": "2026-07-20T10:00:12+08:00",
  "server_verified_at": "2026-07-20T10:00:18+08:00",
  "error_message": null
}
```

此接口只反映上传对象是否已被 OSS 分片完成事件确认。数据集格式是否合法由训练任务第一阶段给出，结果写入训练任务的 `validation_report.json` 和失败原因。

### 9.8 OSS Multipart 完成回调接口

`POST /api/training/remote/callbacks/oss/multipart-complete`

用途：

- 由 FC/EventBridge 在收到 `oss:ObjectCreated:CompleteMultipartUpload` 后调用。
- 只确认 raw ZIP 对象上传完成，不做数据集格式校验。
- 按数据库中保存的 `bucket + raw_object_key` 精确匹配上传会话，不在接口中写死测试前缀。
- 幂等推进 `dataset_uploads.status = UPLOADED`。

请求：

```json
{
  "event_id": "event-id",
  "event_type": "oss:ObjectCreated:CompleteMultipartUpload",
  "bucket": "your-runtime-bucket-or-access-point-alias",
  "object_key": "runtime/generated/raw/object/key/dataset.zip",
  "size": 2147483648,
  "etag": "...",
  "event_time": "2026-07-20T10:00:00Z"
}
```

认证：

- FC 调用时使用 `Authorization: Bearer <REMOTE_TRAINING_CALLBACK_SECRET>`。
- `REMOTE_TRAINING_CALLBACK_SECRET` 必须在后端和 FC 环境变量中配置为同一个随机长字符串。

### 9.9 EventBridge/FC 事件接口

生产第一期只要求 FC/EventBridge 调用 9.8 的 multipart 完成回调接口。后续如果要统一处理 training `_SUCCESS` 等事件，可以再增加通用事件入口。

当前 FC Python 3.10 函数代码存放在：

```text
backend/tools/fc_oss_multipart_complete_handler.py
```

部署入口为 `fc_oss_multipart_complete_handler.handler`。如果部署时改名为 `main.py`，入口为 `main.handler`。

用途：

- 由 FC Orchestrator 调用后端内部 API。
- 支持事件类型：
  - `oss:ObjectCreated:CompleteMultipartUpload`
  - training `_SUCCESS` 的对象创建事件

认证：

- 当前实现使用 `Authorization: Bearer <REMOTE_TRAINING_CALLBACK_SECRET>`。
- 后续如需更强安全性，可升级为 HMAC Header：`X-Remote-Event-Signature`、`X-Remote-Event-Timestamp`。

行为：

- 当前最小实现把 OSS 事件 ID、事件类型和事件时间写入 `dataset_uploads.metadata`；后续增加 `remote_events` 后再统一做事件审计。
- 根据 object key 反查 upload/job。
- 对 raw zip：只处理 `oss:ObjectCreated:CompleteMultipartUpload`，按 bucket + object_key 精确匹配上传记录后幂等推进到 `UPLOADED`。
- 对 training `_SUCCESS`：同步结果、指标和模型版本。

### 9.10 启动远程训练

`POST /api/training/remote/start`

请求：

```json
{
  "dataset_id": "ds_xxx",
  "scene_id": 2,
  "model_name": "yolo11n",
  "epochs": 100,
  "img_size": 640,
  "batch_size": 16,
  "optimizer": "SGD",
  "lr0": 0.01,
  "ecs_spec": "ecs.gn7i-c8g1.2xlarge",
  "pod_count": 1,
  "set_default_on_success": false
}
```

服务端行为：

- 校验 `dataset_uploads.status == UPLOADED`。
- 创建 `training_tasks`，`training_backend='pai_dlc'`，状态 `pending`。
- 创建 `remote_training_jobs`，状态 `CREATED`。
- 构建 PAI-DLC `CreateJob` 请求：
  - `JobType='PyTorchJob'`。
  - `JobSpecs` 指定镜像、资源、PodCount。
  - `UserCommand` 指向训练脚本，传入 raw ZIP 对象、输出前缀、训练参数、callback URL/token。
  - 环境变量注入任务 ID、OSS endpoint、region、输出前缀等。
- CreateJob 成功后写入 `dlc_job_id`，状态变为 `SUBMITTED`。
- `training_tasks.status` 对外返回 `pending` 或 `running`，前端可继续用现有 `/status/{task_id}` 轮询。

响应：

```json
{
  "id": 123,
  "task_uuid": "abcd1234",
  "status": "pending",
  "training_backend": "pai_dlc",
  "remote_job_id": "dlc_xxx",
  "message": "远程训练任务已提交"
}
```

### 9.11 远程任务回调

`POST /api/training/remote/callbacks/dlc`

请求由 DLC 训练脚本主动发出：

```json
{
  "task_uuid": "abcd1234",
  "dlc_job_id": "dlc_xxx",
  "status": "SUCCEEDED",
  "output_prefix": "train/jobs/abcd1234/",
  "results_csv_key": "train/jobs/abcd1234/results.csv",
  "best_weight_key": "train/jobs/abcd1234/weights/best.pt",
  "metrics_summary": {
    "map50": 0.73,
    "map50_95": 0.41,
    "precision": 0.76,
    "recall": 0.69
  }
}
```

认证：

- 每个 `remote_training_jobs` 生成一次性 callback token，只保存 hash。
- 回调 Header 使用 `Authorization: Bearer <token>` 或 HMAC 签名。

行为：

- 校验 token 与 `dlc_job_id/task_uuid`。
- 当前最小实现先同步任务状态；后续增加 `remote_events` 后再写入统一事件表。
- 不仅信任 callback，还要读取 OSS `validation_report.json`、`_SUCCESS`、`results.csv`、`metrics.json` 校验。
- 成功后更新 `training_tasks.status='completed'`，`progress=100`，写入 `training_metrics`，创建/更新 `model_versions`。

## 10. 详细流程

### 10.1 上传到 UPLOADED

```text
1. 前端请求 POST /api/training/remote/uploads。
2. 后端创建 upload_id、固定 Object Key，写 dataset_uploads(INITIATED)。
3. 后端调用 OSS InitiateMultipartUpload，返回 OSS upload_id、part_size 和签名接口。
4. 前端根据 `ceil(file_size / part_size)` 计算 total_parts。
5. 前端按批调用 `/multipart/parts/sign` 获取 part URL。
6. 浏览器并发 PUT 每个 part 到 OSS，并保存每片 ETag。
7. 前端定期调用 heartbeat，后端只记录非可信进度。
8. 前端把全部 `part_number + etag` 提交到 `/multipart/complete`。
9. 后端调用 OSS CompleteMultipartUpload 合并分片，状态先记为 CLIENT_COMPLETED。
10. OSS 完成分片合并后产生 `oss:ObjectCreated:CompleteMultipartUpload` 事件。
11. EventBridge 触发 FC，FC 调用后端内部回调，传入 bucket、object_key、size、etag。
12. 后端按 bucket + object_key 精确匹配上传记录，状态改为 UPLOADED。
13. 前端轮询看到上传已确认，可点击开始训练。
```

### 10.2 UPLOADED 到训练完成

```text
1. 前端选择 UPLOADED 数据集，调用 /api/training/remote/start。
2. 后端创建 training_tasks 和 remote_training_jobs。
3. 后端提交 PAI-DLC 训练任务。
4. DLC 容器启动后先读取 raw dataset.zip。
5. DLC 安全解压 ZIP 到容器临时工作目录。
6. DLC 执行 YOLO 数据集格式检查并写 validation_report.json。
7. 如果校验失败，DLC 写失败报告并退出非零状态，后端同步为 failed。
8. 如果校验成功，DLC 修正 data.yaml 的 path，继续 Ultralytics 训练。
9. DLC 训练脚本周期性写 metrics.jsonl 或 results.csv 到 OSS。
10. Worker 增量读取 metrics，写入 training_metrics。
11. DLC 完成后写 best.pt、last.pt、results.csv、eval_report.json。
12. DLC 最后写 train/jobs/{task_uuid}/_SUCCESS。
13. DLC callback 与 _SUCCESS 事件都触发后端同步。
14. 后端验证 validation_report、best.pt、results.csv、_SUCCESS，更新 training_tasks completed。
15. 后端创建 model_versions，写 oss_model_key/oss_model_url。
16. 若用户要求设为默认模型，更新默认模型记录；是否下载到本地推理按后续策略处理。
```

## 11. PAI-DLC Job 设计

### 11.1 训练 Job 的数据准备阶段

不再创建独立的数据处理 Job。数据集格式检查、ZIP 安全解压和训练在同一个 PAI-DLC Job 内连续执行。

输入：

```text
RAW_OBJECT_KEY={oss_prefix}/{user_id}/{upload_id}/dataset.zip
OUTPUT_PREFIX=train/jobs/{task_uuid}/
DATASET_ID=...
UPLOAD_ID=...
TASK_UUID=...
CALLBACK_URL=...
CALLBACK_TOKEN=...
```

数据准备职责：

- 下载或通过 OSS 挂载读取 raw ZIP。
- 防 Zip Slip：拒绝绝对路径、`..`、软链接等危险条目。
- 限制解压后总大小、文件数量、单文件大小和允许后缀。
- 解压到容器本地工作目录，不写回应用服务器。
- 识别 YOLO 数据集结构。
- 如缺少 `data.yaml`，按当前本地上传逻辑推断类别并生成；如果推断失败则训练失败。
- 校验图片和标签数量、空标签、类别 ID 范围、损坏图片。
- 写入 `train/jobs/{task_uuid}/dataset/validation_report.json`。
- 校验失败时主动回调服务端，任务退出非零状态。
- 校验成功后把本地 `data.yaml` 的 `path` 修正为容器内实际路径，进入训练阶段。

`validation_report.json` 示例：

```json
{
  "dataset_id": "ds_xxx",
  "upload_id": "upl_xxx",
  "task_uuid": "abcd1234",
  "ok": true,
  "source": {
    "bucket": "bucket-name",
    "object_key": "{oss_prefix}/{user_id}/{upload_id}/dataset.zip",
    "etag": "..."
  },
  "format": "yolo",
  "classes": ["class_0", "class_1"],
  "splits": {
    "train": {"images": 1200, "labels": 1200},
    "val": {"images": 200, "labels": 200}
  },
  "errors": [],
  "created_at": "2026-07-17T10:00:00+08:00",
  "processor_version": "remote-train-0.2.0"
}
```

### 11.2 训练阶段

Job 类型：

- `PyTorchJob`。
- 单机 GPU 为第一期默认；多机多卡作为第二期。

输入环境变量：

```text
TASK_ID=123
TASK_UUID=abcd1234
RAW_OBJECT_KEY={oss_prefix}/{user_id}/{upload_id}/dataset.zip
OUTPUT_PREFIX=train/jobs/{task_uuid}/
MODEL_NAME=yolo11n
EPOCHS=100
IMG_SIZE=640
BATCH_SIZE=16
OPTIMIZER=SGD
LR0=0.01
CALLBACK_URL=https://api.example.com/api/training/remote/callbacks/dlc
CALLBACK_TOKEN=...
```

训练脚本职责：

- 从 OSS 读取/挂载 raw dataset.zip。
- 执行 11.1 的安全解压和 YOLO 格式校验。
- 修正 `data.yaml` 的 `path` 为容器内实际路径。
- 下载基础权重 `yolo11n.pt` 或从 OSS/镜像内读取。
- 调用 Ultralytics 训练。
- 每个 epoch 后追加 `metrics.jsonl` 或定期同步 `results.csv`。
- 完成后上传 `results.csv`、`weights/best.pt`、`weights/last.pt`、评估图表、`eval_report.json`。
- 最后写 `_SUCCESS`。
- 主动回调服务端。

PAI-DLC `CreateJob` 请求核心字段：

```json
{
  "WorkspaceId": "...",
  "ResourceId": "...",
  "DisplayName": "chestx-train-abcd1234",
  "JobType": "PyTorchJob",
  "JobSpecs": [
    {
      "Type": "Worker",
      "Image": "registry-vpc.cn-hangzhou.aliyuncs.com/xxx/chestx-train:0.1.0",
      "PodCount": 1,
      "EcsSpec": "ecs.gn7i-c8g1.2xlarge"
    }
  ],
  "UserCommand": "python /workspace/train_yolo_remote.py",
  "Envs": {
    "TASK_UUID": "abcd1234",
    "RAW_OBJECT_KEY": "{oss_prefix}/{user_id}/{upload_id}/dataset.zip",
    "OUTPUT_PREFIX": "train/jobs/{task_uuid}/"
  },
  "JobMaxRunningTimeMinutes": 720
}
```

实际字段以当前 SDK 版本模型为准，后端封装层负责适配。

## 12. Worker 与对账机制

必须实现定时对账，不能依赖单一回调。

### 12.1 上传对账

周期扫描：

- `INITIATED/UPLOADING` 且 `expires_at < now`：标记 `EXPIRED`，可调用 AbortMultipartUpload 或依赖生命周期清理。
- `CLIENT_COMPLETED` 超过阈值：保持等待 OSS `CompleteMultipartUpload` 事件；可提示用户“等待 OSS 确认”或标记为待对账，不能直接推进 `UPLOADED`。
- `UPLOADED` 保持可训练状态，不再自动提交预处理 Job。
- 如果用户删除或替换 raw 对象，Worker 通过 HeadObject/ETag 对账后标记 FAILED 或要求重新上传。

### 12.2 训练对账

周期扫描：

- `remote_training_jobs.remote_status in (SUBMITTED, QUEUED, RUNNING)`。
- 调用 PAI-DLC GetJob 同步状态。
- 若 `validation_report.json` 已写出且 `ok=false`，立即同步失败原因并标记训练失败。
- 若输出前缀下有新的 `metrics.jsonl/results.csv`，增量写 `training_metrics`。
- 若 DLC 已终态但后端未终态，验证 `_SUCCESS` 和产物后推进。
- 若超过最大运行时间，标记失败或触发 StopJob。

### 12.3 幂等原则

- 所有状态推进函数都必须可重复调用。
- 回调、EventBridge、Worker 竞争时，用数据库事务和行级锁保护。
- 只允许从旧状态向新状态推进，不允许回退。
- 对同一 upload/job 的重复事件应保持幂等；当前通过状态判断和 metadata 覆盖实现，后续可用 `remote_events` 做严格去重。

## 13. 安全设计

### 13.1 上传安全

- Object Key 后端生成，前端不能指定任意 key。
- 预签名 URL 限定 HTTP 方法、bucket、object key、headers 和过期时间。
- 上传对象 metadata 写入：
  - `x-oss-meta-upload-id`
  - `x-oss-meta-user-id`
  - `x-oss-meta-dataset-name`
  - `x-oss-meta-expected-size`
- 当前阶段不执行 `HeadObject` 校验；metadata、size、etag 字段保留给后续恢复可信上传确认。
- 如果前端提供 sha256，训练 Job 的数据准备阶段在解压前计算并校验。

### 13.2 回调安全

- OSS Callback 验签。
- FC -> Backend 使用 HMAC Header。
- DLC -> Backend 使用 per-job callback token。
- 回调接口不使用用户 JWT，但必须有机器认证。

### 13.3 ZIP 安全

- 防 Zip Slip。
- 限制解压后总大小、文件数量、单文件大小。
- 限制后缀白名单。
- 拒绝隐藏可执行文件和脚本，除非明确支持。
- 训练容器的数据准备阶段使用非 root 用户运行，解压只落到容器临时工作目录。

### 13.4 云权限最小化

- 后端 RAM：PutObject/HeadObject/GetObject/ListObjects/DeleteObject 小范围、PAI-DLC Create/Get/StopJob。
- 浏览器：只持有短期上传凭据；当前兼容测试路径是 PUT 预签名 URL，不持有任何云账号凭证。
- DLC Role：读 raw dataset.zip，写 training output/models。
- EventBridge/FC：只允许触发指定函数和调用后端内部事件入口。

## 14. 与现有本地训练兼容

兼容策略：

- 保留 `POST /api/training/start` 作为本地训练入口。
- 新增 `POST /api/training/remote/start` 作为远程训练入口。
- `GET /api/training/tasks` 返回中增加 `training_backend`，不破坏原字段。
- `GET /api/training/status/{task_id}` 内部判断 backend：
  - local：使用现有 `_running_tasks` 和本地 metrics。
  - pai_dlc：读取 `remote_training_jobs` 和远程同步 metrics。
- `GET /api/training/metrics/{task_id}` 继续只读 `training_metrics`。
- `POST /api/training/export/{task_id}`：
  - local：沿用当前本地文件导出。
  - pai_dlc：从 OSS 产物创建 `model_versions`，可选生成下载签名 URL。

## 15. 第一阶段落地任务

### 阶段 A：基础表和配置

- 新增 Alembic 迁移：
  - `dataset_uploads`
  - `remote_training_jobs`
  - `remote_events` 可作为后续增强表，不阻塞第一阶段最小闭环
  - `training_tasks.training_backend`
  - `model_versions.oss_model_key/oss_model_url/artifact_manifest`
- 新增 Settings 配置项。
- 新增 `OssClient` 和 `PaiDlcClient` 空封装，先支持 mock/dev 模式。

验收：

- 后端启动正常。
- `/docs` 能看到远程训练 API。
- 单元测试覆盖状态机基础流转。

### 阶段 B：OSS 直传闭环

- `POST /remote/uploads` 创建上传会话。
- 默认返回 multipart 上传凭据；当前兼容测试路径可返回 PUT 预签名 URL。
- `multipart/parts/sign` 按批签发 part URL。
- `multipart/complete` 合并分片后进入 `CLIENT_COMPLETED`。
- `heartbeat`、`complete`、`status` API。
- FC/EventBridge multipart 完成回调进入 `UPLOADED`。
- OSS Callback/EventBridge 事件入口，当前把 multipart 事件信息写入 `dataset_uploads.metadata`；后续可升级为写 `remote_events`。

验收：

- 浏览器或脚本可直传 OSS。
- 前端 multipart complete 后，服务端只记录 `CLIENT_COMPLETED`。
- OSS `CompleteMultipartUpload` 事件到达后，服务端进入 `UPLOADED`。
- 重复 complete / 重复事件不产生错误状态。

### 阶段 C：远程训练 Job 与数据准备阶段

- 编写 `scripts/dlc/train_yolo_remote.py`。
- 在训练脚本开头实现 raw ZIP 下载/挂载读取、安全解压、YOLO 格式校验和 `validation_report.json` 输出。
- `remote_training_service.start_remote_training()` 接受 `UPLOADED` 数据集并提交 PAI-DLC。
- PAI-DLC CreateJob/GetJob/StopJob 封装。
- 训练状态对账 Worker。
- 远程指标同步到 `training_metrics`。

验收：

- UPLOADED 数据集可提交远程训练。
- 错误 ZIP 会让训练任务进入 FAILED，并保存用户可理解的失败原因。
- 正确 ZIP 会写出 `validation_report.json` 并继续训练。
- `/training/status/{task_id}` 可看到远程进度。
- `/training/metrics/{task_id}` 可画 loss 曲线。
- 训练完成后写入 `completed`。

### 阶段 D：模型版本与下载

- 远程训练完成后创建/更新 `model_versions`。
- `download` 对远程模型返回 OSS 签名下载 URL 或代理下载。
- `export` 支持远程模型。
- 可选：设为默认模型时下载 `best.pt` 到本地 `models/best.pt` 并热重载检测服务。

验收：

- 远程训练产物可下载。
- 模型版本页/检测服务能引用远程模型产物。
- 失败任务不会生成 active 模型版本。

## 16. 测试计划

单元测试：

- Object Key 生成不可穿越目录。
- dataset_upload 状态机合法流转。
- repeated EventBridge event 幂等。
- complete 幂等路径。
- multipart 完成回调幂等路径。
- HeadObject 校验失败路径作为后续增强测试。
- `validation_report.json` 成功/失败解析逻辑。
- PAI-DLC 状态映射。

集成测试：

- mock OSS：客户端 complete 后进入 CLIENT_COMPLETED，multipart 完成回调后进入 UPLOADED。
- mock DLC train：先写 validation_report，再写 results/best/_SUCCESS 后进入 completed。
- mock DLC train：validation_report 中 `ok=false` 时任务进入 failed。
- 断点：complete 到达但 EventBridge 丢失，Worker 能补偿。
- 断点：DLC callback 丢失，_SUCCESS 事件或 Worker 能补偿。

安全测试：

- 上传凭据不能上传到其他 key；当前兼容测试路径验证 PUT 预签名 URL，生产路径验证分片上传凭据。
- 回调签名错误返回 401/403。
- ZIP Slip 被拒绝。
- 超大 ZIP、超多文件被拒绝。

## 17. 关键决策

1. 当前阶段上传确认只信任 OSS `CompleteMultipartUpload` 事件；前端 complete 只作为 UI 参考，服务端 `HeadObject` 复核作为后续增强保留。
2. OSS Callback 只做轻量确认，不作为唯一可靠事件源。
3. EventBridge + FC + 定时 Worker 三重保障，处理遗漏事件和僵死状态。
4. 数据集格式校验不做独立服务；它是训练任务的数据准备阶段，校验失败即训练失败。
5. 训练 completed 以 DLC 终态、`validation_report.ok=true`、`_SUCCESS`、关键产物存在四者交叉校验为准。
6. `training_tasks` 保持业务主表，远程特有字段放到 `remote_training_jobs`。
7. 第一阶段只做单机单卡/单 Worker 远程训练，多机分布式作为后续增强。

## 18. 风险与待确认项

- PAI-DLC 训练容器如何访问 OSS：优先使用 PAI 数据源挂载；若挂载不满足需求，脚本内使用 OSS SDK 读写。
- DLC Job 日志是否需要回传到本系统：第一期只同步状态和指标，日志链接后续补充。
- 默认模型是否必须落本地：现有检测服务读取本地 `models/best.pt`，远程模型设默认时可能需要下载到本地或改造检测服务支持 OSS 拉取缓存。
- 是否保留 `datasets/processed/` 缓存：第一期不作为训练前置条件；后续如果要复用解压结果，需要单独设计缓存一致性和权限。
- 现有 `settings.py` 中训练配置重复定义，后续改造时可顺手清理，但不作为远程训练第一阶段必要项。

## 19. 官方参考

- OSS 直传：<https://www.alibabacloud.com/help/en/oss/user-guide/uploading-objects-to-oss-directly-from-clients/>
- OSS Callback：<https://www.alibabacloud.com/help/en/oss/developer-reference/callback>
- OSS EventBridge 事件类型：<https://www.alibabacloud.com/help/en/eventbridge/user-guide/oss-events>
- OSS HeadObject：<https://www.alibabacloud.com/help/en/oss/developer-reference/headobject>
- OSS 分片上传与未完成分片清理：<https://www.alibabacloud.com/help/en/oss/user-guide/multipart-upload>
- PAI-DLC CreateJob API：<https://www.alibabacloud.com/help/en/pai/developer-reference/api-pai-dlc-2020-12-03-createjob>
- PAI-DLC 创建训练任务：<https://www.alibabacloud.com/help/en/pai/create-a-training-task>
