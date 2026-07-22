# To Test: PAI-DLC Remote Job Flow

本文档只拆 PAI-DLC 侧需要先验证的后端能力。目标是用一个单文件模拟测试远程计算任务生命周期：创建 Job、轮询状态、同步指标、处理回调、验证产物、提前停止。

对应脚本：

```bash
python backend/tools/pai_dlc_test.py --case all
```

## 1. 单功能测试清单

### 1.1 CreateJob Payload 构建

要测：

- READY 数据集才能启动远程训练。
- payload 包含 workspace/resource/image/job_type/user_command/envs。
- envs 包含 `TASK_UUID`、`DATASET_PREFIX`、`OUTPUT_PREFIX`、训练参数、callback 信息。
- output prefix 固定为 `training/jobs/{task_uuid}/`。

模拟断言：

- dataset 非 READY 时拒绝启动。
- payload 缺少关键字段时拒绝提交。
- 创建任务后写入 `training_task` 与 `remote_training_job`。

### 1.2 提交 Job 与状态映射

要测：

- `CreateJob` 成功后保存 `dlc_job_id`。
- PAI-DLC 原始状态映射为内部状态：
  - `Submitted/Pending` -> `SUBMITTED/QUEUED`
  - `Running` -> `RUNNING`
  - `Succeeded` -> `SUCCEEDED`
  - `Failed` -> `FAILED`
  - `Stopped` -> `STOPPED`
- 业务主状态 `training_tasks.status` 兼容现有前端：`pending/running/completed/failed/cancelled`。

模拟断言：

- 创建后 remote_status 为 `SUBMITTED`。
- 轮询后依次进入 `QUEUED`、`RUNNING`、`SUCCEEDED`。
- 业务状态同步为 `pending`、`running`、`completed`。

### 1.3 轮询与指标同步

要测：

- 远程 Job RUNNING 后产生 epoch 指标。
- Worker 轮询时将新指标写入 `training_metrics`。
- 指标同步幂等，重复轮询不重复写 epoch。

模拟断言：

- RUNNING 期间 metrics 数量递增。
- 同一 epoch 不重复。
- latest metric 可用于前端画 loss 曲线。

### 1.4 成功产物验证

要测：

- 训练完成不能只信 DLC 状态或 callback。
- 必须验证：
  - `training/jobs/{task_uuid}/_SUCCESS`
  - `results.csv`
  - `weights/best.pt`
  - 可选 `eval_report.json`
- 验证成功后才更新 `completed` 并创建模型版本信息。

模拟断言：

- SUCCEEDED 但缺少 `best.pt` 时不能 completed。
- 关键产物完整时 completed。
- completed 后有模型版本 artifact 指针。

### 1.5 DLC 主动回调

要测：

- 回调必须校验 per-job token。
- callback 与 polling/_SUCCESS event 可能重复到达，必须幂等。
- callback 只作为触发同步的信号，不作为唯一可信来源。

模拟断言：

- bad token 被拒绝。
- good callback 后触发 artifact sync。
- 重复 callback 不重复写模型版本。

### 1.6 提前停止

要测：

- running 任务可以调用 StopJob。
- 停止后 remote_status 为 `STOPPED`。
- 业务状态为 `cancelled`。
- 不创建模型版本。

模拟断言：

- stop 后再次 poll 不会恢复为 running。
- cancelled 任务无 `model_version`。

### 1.7 失败与超时

要测：

- DLC 返回失败状态时，任务进入 failed。
- 保存错误信息。
- 超过最大运行时间时，Worker 可触发 stop 或标记 failed。

模拟断言：

- failed case 状态为 `failed`。
- error_message 有内容。

## 2. 模拟流程设计

`backend/tools/pai_dlc_test.py` 将单文件内包含：

- `MockDlcClient`：内存 PAI-DLC 客户端，支持 create/get/stop。
- `MockArtifactStore`：模拟 OSS 训练输出产物。
- `RemoteTrainingService`：模拟后端远程训练状态机、轮询、回调、产物验证。
- 多个 case：
  - `happy`：创建、轮询、指标同步、成功产物、completed。
  - `callback`：主动回调触发同步，重复回调幂等。
  - `stop`：RUNNING 后提前停止。
  - `failed`：DLC 失败状态映射到业务 failed。
  - `bad-callback`：错误 token 被拒绝。

## 3. 后续接真实 PAI-DLC 时的替换点

模拟脚本中只有 `MockDlcClient` 和 `MockArtifactStore` 需要替换：

- `create_job(payload)` -> PAI-DLC CreateJob
- `get_job(job_id)` -> PAI-DLC GetJob
- `stop_job(job_id)` -> PAI-DLC StopJob/DeleteJob
- `put/get/exists/list` artifact -> OSS Client

远程训练状态机、指标幂等、callback token 校验、产物验证断言应保持不变。
