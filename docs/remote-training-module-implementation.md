# 远程训练模块实现说明

本文说明本次新增的远程训练后端模块。目标是把生产训练路径完全转为远程 PAI-DLC，Web 后端只负责编排、状态同步和产物登记，不再在网站服务器中执行高负载训练。

## 1. 本次改动范围

### 新增代码

```text
backend/app/train/
  __init__.py
  remote_train_config.py      # 远程训练环境变量配置
  remote_train_storage.py     # OSS 对象存储网关
  remote_train_dlc.py         # PAI-DLC SDK 网关
  remote_train_service.py     # 远程训练业务编排
  remote_train_router.py      # /api/training/remote 路由
```

Python 模块名不能使用 `-`，因此文件名采用 `remote_train_*.py`。

### 修改代码

```text
backend/main.py
```

新增注册：

```python
app.include_router(remote_training_router)
```

旧的 `/api/training/*` 本地训练接口暂时保留兼容，但生产远程训练入口使用：

```text
/api/training/remote/*
```

### 新增数据库模型

```text
dataset_uploads
remote_training_jobs
model_artifact_locations
```

对应迁移：

```text
backend/alembic/versions/9c0f2d4b6e7a_add_remote_training_tables.py
```

## 2. 核心设计

### 2.1 用户不选择训练位置

用户不再选择：

- 本地 CPU
- 本地 GPU
- 远程训练
- 设备编号

用户只提交业务训练参数：

- 数据集
- 模型名称
- epochs
- img_size
- batch_size
- optimizer
- lr0

后端固定按远程训练路径处理，计算规格、镜像、挂载路径和 PAI-DLC 参数由服务端配置决定。

### 2.2 Web 后端不执行训练

Web 后端只做：

- 创建 OSS 上传会话
- 初始化 OSS multipart，并按批签发 part PUT 预签名 URL；当前兼容测试路径仍可返回短期 PUT 预签名 URL
- 记录客户端上传完成信号
- 接收 FC/EventBridge 转发的 OSS `CompleteMultipartUpload` 事件
- 创建 `training_tasks`
- 创建 `remote_training_jobs`
- 调用 PAI-DLC CreateJob/GetJob/StopJob
- 校验 OSS 训练产物
- 同步 `training_metrics`
- 登记 `model_artifact_locations`

数据集格式校验、ZIP 安全解压和训练都由 PAI-DLC 训练任务执行。后端不再单独编排上传后的数据集格式验证或预处理任务。

## 3. 数据生命周期

### 3.1 数据集

```text
客户端 dataset.zip
  -> OSS raw object
  -> 前端上传 SDK 成功
  -> 后端 complete 记录 CLIENT_COMPLETED
  -> OSS CompleteMultipartUpload 事件
  -> FC/EventBridge 调用后端内部回调
  -> dataset_uploads.status = UPLOADED
```

`CLIENT_COMPLETED` 不能启动训练。`UPLOADED` 表示服务端已经收到 OSS 分片上传完成事件，但不表示后端已通过 OSS `HeadObject` 复核，也不表示 YOLO 格式正确。`HeadObject` 能力保留用于后续恢复可信校验；YOLO 格式检查归属训练任务第一阶段。

### 3.2 训练任务

```text
UPLOADED dataset.zip
  -> POST /api/training/remote/start
  -> training_tasks
  -> remote_training_jobs
  -> PAI-DLC CreateJob
  -> PAI-DLC 读取 raw ZIP
  -> 安全解压 + YOLO 格式校验
  -> 校验失败则 training_tasks.status = failed
  -> 校验成功后 Ultralytics 训练
  -> OSS validation_report.json / results.csv / weights/best.pt / _SUCCESS
  -> 后端 sync 校验产物
  -> training_tasks.status = completed
  -> training_metrics + model_artifact_locations
```

### 3.3 模型产物位置

`model_versions` 仍表示业务模型版本。

`model_artifact_locations` 表示模型和报告的实际存储位置。一个模型版本或训练任务可以同时有多条位置记录，例如：

```text
best_weight  -> oss://bucket/train/jobs/.../weights/best.pt
results_csv  -> oss://bucket/train/jobs/.../results.csv
eval_report  -> oss://bucket/train/jobs/.../eval_report.json
default_cache -> backend/models/best.pt
```

这样可以区分：

- OSS 权威副本
- 服务端本地推理缓存
- MinIO 兼容副本
- 临时下载 URL

## 4. 新增接口

### 4.1 创建上传会话

```text
POST /api/training/remote/uploads
```

请求：

```json
{
  "scene_id": 1,
  "dataset_name": "chest_xray_v2",
  "filename": "dataset.zip",
  "content_type": "application/zip",
  "expected_size": 123456789,
  "upload_mode": "multipart"
}
```

返回：

```json
{
  "upload_id": "upl_xxx",
  "dataset_id": "ds_xxx",
  "status": "INITIATED",
  "object_key": "{REMOTE_TRAIN_OSS_PREFIX}/{user_id}/{upload_id}/dataset.zip",
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
  }
}
```

默认使用 multipart。前端根据 `total_parts = ceil(file_size / part_size)` 计算分片数量，并通过下面的分片签名接口按批获取每片 URL。当前代码仍保留 `upload_mode=presigned_put`，用于小文件和真实 OSS 连通性测试。

### 4.2 批量签发分片 URL

```text
POST /api/training/remote/uploads/{upload_id}/multipart/parts/sign
```

请求：

```json
{
  "part_numbers": [1, 2, 3],
  "expires_seconds": 900
}
```

返回：

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
      "url": "https://...",
      "headers": {},
      "expires_seconds": 900
    }
  ]
}
```

每个 part URL 只允许上传同一个 object key 下指定 `uploadId + partNumber` 的分片。

### 4.3 合并分片

```text
POST /api/training/remote/uploads/{upload_id}/multipart/complete
```

请求：

```json
{
  "parts": [
    {"part_number": 1, "etag": "\"etag-part-1\""}
  ]
}
```

后端调用 OSS `CompleteMultipartUpload`。成功后状态先变为：

```text
CLIENT_COMPLETED
```

真正的 `UPLOADED` 仍只能由 FC/EventBridge 转发的 `oss:ObjectCreated:CompleteMultipartUpload` 事件推进。

### 4.4 取消分片上传

```text
POST /api/training/remote/uploads/{upload_id}/multipart/abort
```

调用 OSS `AbortMultipartUpload`，状态变为 `CANCELLED`。已经 `UPLOADED/READY` 的上传不能取消。

### 4.5 确认上传

```text
POST /api/training/remote/uploads/{upload_id}/complete
```

当前 `complete` 只记录客户端上传 SDK 已完成，状态变为：

```text
CLIENT_COMPLETED
```

真正的 `UPLOADED` 只能由 FC/EventBridge 转发的 `oss:ObjectCreated:CompleteMultipartUpload` 事件推进。

multipart 默认不需要调用这个接口；它主要保留给 SDK 已自行完成上传的兼容场景。

### 4.6 标记数据集 READY（兼容/测试接口）

```text
POST /api/training/remote/uploads/{upload_id}/ready
```

请求：

```json
{
  "processed_prefix": "remote-training/datasets/processed/chest_xray_v2/ds_xxx/",
  "manifest_key": "remote-training/datasets/processed/chest_xray_v2/ds_xxx/manifest.json",
  "success_key": "remote-training/datasets/processed/chest_xray_v2/ds_xxx/_SUCCESS",
  "verify_objects": true
}
```

该接口来自旧的“上传后独立预处理”设计，只作为兼容和测试入口保留。新流程不再依赖它，生产前端不应调用它；训练启动应直接使用 `UPLOADED` 数据集。

### 4.7 启动远程训练

```text
POST /api/training/remote/start
```

请求：

```json
{
  "dataset_id": "ds_xxx",
  "model_name": "yolo11n",
  "epochs": 100,
  "img_size": 640,
  "batch_size": 16,
  "optimizer": "SGD",
  "lr0": 0.01
}
```

接口会创建：

- `training_tasks`
- `remote_training_jobs`

然后调用 PAI-DLC `CreateJob`。

训练容器启动后的第一阶段会读取 raw ZIP、做安全解压和 YOLO 格式校验。校验失败时任务直接失败；校验成功后才进入 Ultralytics 训练。

### 4.8 同步状态

```text
POST /api/training/remote/jobs/{task_id}/sync
GET  /api/training/remote/status/{task_id}
```

同步逻辑：

- 查询 PAI-DLC GetJob。
- 映射远程状态到业务状态。
- 如果远程成功，校验 OSS `validation_report.json`、`_SUCCESS`、`results.csv`、`weights/best.pt`。
- 关键产物齐全后才把 `training_tasks.status` 改为 `completed`。

### 4.9 停止训练

```text
POST /api/training/remote/stop/{task_id}
```

调用 PAI-DLC StopJob，并把业务状态置为 `cancelled`。

### 4.10 DLC 回调

```text
POST /api/training/remote/callbacks/dlc
```

请求：

```json
{
  "dlc_job_id": "dlcxxx",
  "status": "Succeeded",
  "token": "per-job-token"
}
```

回调只作为同步信号。即使回调状态是 `Succeeded`，后端仍需校验 OSS 产物。

### 4.11 查询产物位置

```text
GET /api/training/remote/artifacts/{task_id}
```

返回 `model_artifact_locations` 中登记的所有文件位置。

### 4.12 FC OSS 完成事件函数

FC Python 3.10 函数代码：

```text
backend/tools/fc_oss_multipart_complete_handler.py
```

部署入口：

```text
fc_oss_multipart_complete_handler.handler
```

如果部署时把文件改名为 `main.py`，入口改为：

```text
main.handler
```

函数职责：

- 接收 EventBridge 转发的 OSS 事件。
- 只处理 `oss:ObjectCreated:CompleteMultipartUpload`。
- 从事件中提取 `data.oss.bucket.name`、`data.oss.object.key`、`data.oss.object.size`、`data.oss.object.eTag`。
- 调用后端：

```text
POST /api/training/remote/callbacks/oss/multipart-complete
```

FC 环境变量：

```text
BACKEND_OSS_MULTIPART_CALLBACK_URL=https://api.example.com/api/training/remote/callbacks/oss/multipart-complete
REMOTE_TRAINING_CALLBACK_SECRET=与后端相同的随机长字符串
BACKEND_BUCKET_NAME_OVERRIDE=可选；后端保存接入点 alias 时填写
ALLOWED_SOURCE_BUCKETS=可选；事件源 bucket 白名单，逗号分隔
ALLOWED_OBJECT_PREFIXES=可选；object key 前缀白名单，逗号分隔；例如 REMOTE_TRAIN_OSS_PREFIX=upload 时填 upload/
HTTP_TIMEOUT_SECONDS=10
```

如果后端 `OSS_BUCKET` 填的是接入点 alias，而 EventBridge 事件里的 `bucket.name` 是真实 bucket 名，需要设置 `BACKEND_BUCKET_NAME_OVERRIDE` 为后端数据库中保存的同一个 alias，否则后端会因 `bucket + object_key` 匹配失败返回 400。

如果 FC 配置了 `ALLOWED_OBJECT_PREFIXES`，它必须与后端生成的原始 ZIP Object Key 前缀一致。当前原始 ZIP 路径格式为 `{REMOTE_TRAIN_OSS_PREFIX}/{user_id}/{upload_id}/dataset.zip`，因此不应继续使用旧的 `{REMOTE_TRAIN_OSS_PREFIX}/uploads/raw` 前缀。

## 5. 关键环境变量

### OSS

这些变量可以写在后端启动进程的系统环境变量中，也可以写在 `backend/.env` 中；
系统环境变量优先级高于 `.env`。

```text
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
OSS_SECURITY_TOKEN
OSS_ENDPOINT
OSS_REGION
OSS_BUCKET
PAI_DLC_OSS_ENDPOINT
PAI_DLC_OSS_URI_HOST
REMOTE_TRAIN_OSS_PREFIX
OSS_UPLOAD_URL_EXPIRES_SECONDS
REMOTE_TRAINING_CALLBACK_SECRET
REMOTE_TRAINING_METRICS_CALLBACK_URL
REMOTE_TRAINING_ERROR_CALLBACK_URL
```

普通 bucket 推荐填法：

```text
OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
OSS_REGION=cn-beijing
OSS_BUCKET=你的Bucket名称
```

如果使用 OSS 接入点 alias，当前代码仍通过 `oss2.Bucket(auth, endpoint, bucket)` 访问，
通常填法是：

```text
OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
OSS_REGION=cn-beijing
OSS_BUCKET=你的接入点alias
```

不要把 `OSS_ENDPOINT` 写成某个对象 URL、预签名 URL 或 `oss://bucket/key`。

PAI-DLC `DataSources[].Uri` 会拼为 `oss://{OSS_BUCKET}.{endpoint}/{prefix}`。
`endpoint` 默认取 `OSS_ENDPOINT` 的 host；如果 DLC 需要使用内网 OSS endpoint，可以单独设置
`PAI_DLC_OSS_ENDPOINT=https://oss-cn-beijing-internal.aliyuncs.com`，不影响浏览器上传使用的 `OSS_ENDPOINT`。
如果需要直接使用 OSS 控制台右侧展示的完整 host（bucket/access-point alias + endpoint），可以设置
`PAI_DLC_OSS_URI_HOST=你的完整内网访问域名`，该变量优先级高于 `PAI_DLC_OSS_ENDPOINT`。

远程训练容器会向 `REMOTE_TRAINING_METRICS_CALLBACK_URL` 上报 epoch 指标，向
`REMOTE_TRAINING_ERROR_CALLBACK_URL` 上报异常诊断。错误回调 URL 未显式填写时，后端会从
metrics 回调 URL 自动推导同级的 `/callbacks/error`。

服务启动时会做一次本地环境变量检查：只判断这些变量是否填写，缺失时写 WARNING 日志，
不会连接 OSS/PAI-DLC，也不会阻止服务启动。接口实际执行时仍会再次强校验必填配置。

### PAI-DLC

```text
PAI_ACCESS_KEY_ID
PAI_ACCESS_KEY_SECRET
PAI_SECURITY_TOKEN
PAI_REGION_ID
PAI_DLC_ENDPOINT
PAI_WORKSPACE_ID
PAI_RESOURCE_ID
PAI_IMAGE_URI
PAI_JOB_TYPE
PAI_ECS_SPEC
PAI_POD_COUNT
PAI_JOB_MAX_RUNNING_MINUTES
PAI_DATASET_MOUNT_PATH
PAI_OUTPUT_MOUNT_PATH
```

`PAI_IMAGE_URI` 必须是完整镜像地址，例如：

```text
crpi-xxx-vpc.cn-shanghai.personal.cr.aliyuncs.com/namespace/repo:v1
```

不要填 PAI 自定义镜像 ID，例如 `image-xxxx`。

### ACR

```text
ACR_DOCKER_REGISTRY
ACR_USERNAME
ACR_PASSWORD
```

公开镜像通常可留空；VPC endpoint 或私有仓库出现 `insufficient_scope` 时需要填写。

## 6. 后续待补

当前模块已经完成远程训练主链路的后端骨架，但仍有待补项：

- 训练脚本内的数据集准备阶段：raw ZIP 读取、安全解压、YOLO 格式校验、`validation_report.json` 输出。
- OSS EventBridge/FC 事件接入。
- 后端定时对账 Worker。
- 训练结束后自动创建 `model_versions`。
- 默认模型同步到服务端 `models/best.pt` 推理缓存。
- 前端删除 device 选择，并改为远程上传和远程训练流程。

## 7. 接口测试手册

以下命令假设后端运行在：

```bash
export API=http://127.0.0.1:8000
```

并且你已经执行数据库迁移：

```bash
cd backend
alembic upgrade head
```

### 7.1 启动后端

```bash
cd backend
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

如果使用项目已有启动方式，只要确保 `/docs` 能打开即可。

### 7.2 准备 Token

先注册一个测试用户。如果用户已存在，可以跳过注册，直接登录。

```bash
curl -s -X POST "$API/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "remote_tester",
    "email": "remote_tester@example.com",
    "password": "123456",
    "user_type": "doctor"
  }'
```

登录并保存 token：

```bash
export TOKEN=$(
  curl -s -X POST "$API/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"remote_tester","password":"123456"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])"
)
```

验证 token：

```bash
curl -s "$API/api/auth/me" \
  -H "Authorization: Bearer $TOKEN"
```

### 7.3 测试创建上传会话

接口：

```text
POST /api/training/remote/uploads
```

说明：

- 可以传已有 `scene_id`，也可以传新的 `scene_name`。
- 如果 `scene_name` 不存在，后端会自动创建自定义场景，并临时写入默认类别 `class_0`。
- 后续训练脚本写出 `validation_report.json` 后，应从其中的真实类别回填 `detection_scenes.class_names`。

命令：

```bash
curl -s -X POST "$API/api/training/remote/uploads" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scene_name": "remote_test_scene",
    "dataset_name": "remote_test_dataset",
    "filename": "dataset.zip",
    "content_type": "application/zip",
    "expected_size": 1024,
    "upload_mode": "multipart"
  }' | tee /tmp/remote-upload.json
```

保存返回值：

```bash
export UPLOAD_ID=$(python -c "import json; print(json.load(open('/tmp/remote-upload.json'))['upload_id'])")
export DATASET_ID=$(python -c "import json; print(json.load(open('/tmp/remote-upload.json'))['dataset_id'])")
export OSS_BUCKET=$(python -c "import json; print(json.load(open('/tmp/remote-upload.json'))['bucket'])")
export OBJECT_KEY=$(python -c "import json; print(json.load(open('/tmp/remote-upload.json'))['object_key'])")
export PART_SIZE=$(python -c "import json; print(json.load(open('/tmp/remote-upload.json'))['multipart']['part_size'])")
```

预期：

- 返回 `upload_id`
- 返回 `dataset_id`
- 返回 `multipart.oss_upload_id`
- 返回 `multipart.part_size`
- 数据库中 `dataset_uploads.status = INITIATED`

如果这里报 OSS 配置缺失，需要检查：

```text
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
OSS_ENDPOINT
OSS_REGION
OSS_BUCKET
```

### 7.4 测试 multipart 分片上传

创建一个 1024 字节测试文件，大小要和上一步 `expected_size` 一致：

```bash
dd if=/dev/zero of=/tmp/dataset.zip bs=1024 count=1
```

由于 1024 字节小于默认 `part_size`，这里总分片数是 1：

```bash
python -c "import math, os; print(math.ceil(os.path.getsize('/tmp/dataset.zip') / int(os.environ['PART_SIZE'])))"
```

签发第 1 个 part 的 URL：

```bash
curl -s -X POST "$API/api/training/remote/uploads/$UPLOAD_ID/multipart/parts/sign" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"part_numbers":[1]}' | tee /tmp/remote-parts.json
```

保存 part URL：

```bash
export PART1_URL=$(python -c "import json; print(json.load(open('/tmp/remote-parts.json'))['parts'][0]['url'])")
```

用 part URL 上传第 1 片，并保存响应头中的 ETag：

```bash
curl -s -D /tmp/remote-part1.headers -X PUT "$PART1_URL" \
  --data-binary @/tmp/dataset.zip
```

```bash
export PART1_ETAG=$(python - <<'PY'
from pathlib import Path
for line in Path('/tmp/remote-part1.headers').read_text().splitlines():
    if line.lower().startswith('etag:'):
        print(line.split(':', 1)[1].strip())
        break
PY
)
```

提交全部 part 的 ETag，让后端调用 OSS `CompleteMultipartUpload`：

```bash
python - <<'PY' >/tmp/remote-multipart-complete.json
import json, os
print(json.dumps({
    "parts": [
        {"part_number": 1, "etag": os.environ["PART1_ETAG"]}
    ]
}))
PY

curl -s -X POST "$API/api/training/remote/uploads/$UPLOAD_ID/multipart/complete" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @/tmp/remote-multipart-complete.json | tee /tmp/remote-multipart-complete-result.json
```

预期：

- part PUT 的 HTTP 状态是 200
- `/multipart/complete` 返回 `upload.status = CLIENT_COMPLETED`
- OSS 中出现 raw object
- 后端仍需等待 OSS `CompleteMultipartUpload` 事件，才会把状态推进到 `UPLOADED`

如果返回 403：

- 检查后端 AK 是否有 `oss:PutObject`
- 检查 bucket policy/access point policy
- 检查 part URL 是否过期
- 检查 part PUT 时是否额外添加了未参与签名的特殊 header

### 7.5 测试兼容 presigned_put 路径

如果要测试旧的简单 PUT 预签名 URL，在创建上传会话时传：

```json
{
  "upload_mode": "presigned_put"
}
```

该模式会返回 `upload.url` 和必须原样携带的 `upload.headers`。它只用于小文件和 OSS 连通性测试；生产默认 multipart。

### 7.6 测试 complete 校验

接口：

```text
POST /api/training/remote/uploads/{upload_id}/complete
```

命令：

```bash
curl -s -X POST "$API/api/training/remote/uploads/$UPLOAD_ID/complete" \
  -H "Authorization: Bearer $TOKEN"
```

预期：

- 如果只调用了客户端 `complete`，返回 `status = CLIENT_COMPLETED`
- 如果上传会话已是 `FAILED/EXPIRED/CANCELLED`，返回 400，不会改回可用状态
- 收到 OSS `CompleteMultipartUpload` 回调后，状态才变为 `UPLOADED`
- `actual_size` 和 `etag` 来自 OSS 事件；如果 FC 未传入则可能为空

本阶段不会因为 OSS 对象大小或 ETag 不一致失败；这些校验字段保留给后续恢复可信上传确认。

### 7.7 手动模拟 OSS 分片完成回调

接口：

```text
POST /api/training/remote/callbacks/oss/multipart-complete
```

该接口模拟 FC/EventBridge 在收到 OSS `oss:ObjectCreated:CompleteMultipartUpload` 后调用后端。它不使用用户 JWT，而使用机器密钥：

```bash
export CALLBACK_SECRET="与后端 REMOTE_TRAINING_CALLBACK_SECRET 相同的值"
```

命令：

```bash
curl -s -X POST "$API/api/training/remote/callbacks/oss/multipart-complete" \
  -H "Authorization: Bearer $CALLBACK_SECRET" \
  -H "Content-Type: application/json" \
  -d "{
    \"event_id\": \"manual-test-event\",
    \"event_type\": \"oss:ObjectCreated:CompleteMultipartUpload\",
    \"bucket\": \"$OSS_BUCKET\",
    \"object_key\": \"$OBJECT_KEY\",
    \"size\": 1024,
    \"etag\": \"manual-etag\",
    \"event_time\": \"2026-07-20T00:00:00Z\"
  }"
```

预期：

- 回调密钥错误时返回 401
- `event_type` 不是 `oss:ObjectCreated:CompleteMultipartUpload` 时返回 `ignored = true`
- `bucket + object_key` 无法匹配数据库上传记录时返回 400
- 匹配成功后返回 `ignored = false`，上传状态变为 `UPLOADED`

### 7.8 测试启动远程训练

接口：

```text
POST /api/training/remote/start
```

命令：

```bash
curl -s -X POST "$API/api/training/remote/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"dataset_id\": \"$DATASET_ID\",
    \"model_name\": \"yolo11n\",
    \"epochs\": 5,
    \"img_size\": 640,
    \"batch_size\": 4,
    \"optimizer\": \"SGD\",
    \"lr0\": 0.01
  }" | tee /tmp/remote-train.json
```

保存任务 ID：

```bash
export TASK_ID=$(python -c "import json; print(json.load(open('/tmp/remote-train.json'))['id'])")
export DLC_JOB_ID=$(python -c "import json; print(json.load(open('/tmp/remote-train.json'))['remote']['dlc_job_id'])")
```

预期：

- 创建 `training_tasks`
- 创建 `remote_training_jobs`
- 返回 `remote.dlc_job_id`
- 远程状态为 `SUBMITTED`
- DLC 训练脚本启动后先写 `validation_report.json`；校验失败则任务进入 `failed`，校验成功才继续训练。

如果这里报 PAI 配置缺失，需要检查：

```text
PAI_ACCESS_KEY_ID
PAI_ACCESS_KEY_SECRET
PAI_REGION_ID
PAI_WORKSPACE_ID
PAI_IMAGE_URI
PAI_ECS_SPEC 或 PAI_RESOURCE_ID
```

如果镜像拉取失败，需要检查：

```text
PAI_IMAGE_URI
ACR_DOCKER_REGISTRY
ACR_USERNAME
ACR_PASSWORD
```

### 7.9 测试状态同步

接口：

```text
POST /api/training/remote/jobs/{task_id}/sync
GET  /api/training/remote/status/{task_id}
```

命令：

```bash
curl -s -X POST "$API/api/training/remote/jobs/$TASK_ID/sync" \
  -H "Authorization: Bearer $TOKEN"
```

或：

```bash
curl -s "$API/api/training/remote/status/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN"
```

预期：

- PAI-DLC `Submitted/Pending/EnvPreparing` 映射为业务 `pending`
- PAI-DLC `Running` 映射为业务 `running`
- PAI-DLC `Stopped` 映射为业务 `cancelled`
- PAI-DLC `Failed` 映射为业务 `failed`
- PAI-DLC `Succeeded` 后还要校验 OSS 产物，产物齐全才变 `completed`

### 7.10 测试停止远程训练

接口：

```text
POST /api/training/remote/stop/{task_id}
```

命令：

```bash
curl -s -X POST "$API/api/training/remote/stop/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN"
```

预期：

- PAI-DLC 收到 StopJob
- `training_tasks.status = cancelled`
- `remote_training_jobs.remote_status = STOPPED`

### 7.11 测试产物位置查询

接口：

```text
GET /api/training/remote/artifacts/{task_id}
```

命令：

```bash
curl -s "$API/api/training/remote/artifacts/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN"
```

预期：

- 训练未完成时可能返回空列表
- 训练完成且 OSS 产物齐全后，返回 `best_weight`、`results_csv`、`success` 等位置

### 7.12 测试 DLC callback

接口：

```text
POST /api/training/remote/callbacks/dlc
```

该接口需要真实 callback token。token 当前只注入到 DLC 容器环境变量中，数据库只保存 hash。

如果只是测试错误 token 被拒绝：

```bash
curl -s -X POST "$API/api/training/remote/callbacks/dlc" \
  -H "Content-Type: application/json" \
  -d "{
    \"dlc_job_id\": \"$DLC_JOB_ID\",
    \"status\": \"Succeeded\",
    \"token\": \"bad-token\"
  }"
```

预期：

- 返回 400
- 错误信息为 `回调 token 无效`

如果要测试成功 callback，需要从 DLC 容器环境变量 `CALLBACK_TOKEN` 中取出真实 token，或者在测试环境临时增加只读调试接口。生产环境不应返回 callback token。
