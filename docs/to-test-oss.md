# To Test: OSS Upload And Dataset Ready Flow

本文档只拆 OSS 侧需要先验证的后端能力。目标不是测试真实业务训练逻辑，而是把“创建上传会话 -> 浏览器直传 -> 服务端确认 -> 事件幂等 -> manifest/_SUCCESS -> READY”的核心流程先用一个可执行模拟程序跑通。

对应脚本：

```bash
python backend/tools/oss_test.py --case all
```

## 1. 单功能测试清单

### 1.1 固定 Object Key 生成

要测：

- Object Key 只能由服务端生成。
- Key 包含 `user_id/upload_id`，不能由前端注入任意路径。
- dataset_name 只允许字母、数字、下划线、短横线。
- raw 对象固定为 `{user_id}/{upload_id}/dataset.zip`；真实服务会在前面加 `REMOTE_TRAIN_OSS_PREFIX`，例如 `upload/1/upl_xxx/dataset.zip`。

模拟断言：

- 合法 dataset_name 创建成功。
- 包含 `/../`、空格、路径分隔符的 dataset_name 被拒绝。

### 1.2 创建上传会话并返回 PUT 预签名 URL

要测：

- 创建 `upload_id`。
- 写入 `INITIATED` 状态。
- 记录 `expected_size`、bucket、raw object key、过期时间。
- 返回 mock PUT 预签名 URL，URL 只绑定本次 upload object key。
- 返回前端必须随 PUT 一起发送的 headers，例如 `Content-Type` 和 `x-oss-meta-*`。

模拟断言：

- upload 记录存在。
- status 为 `INITIATED`。
- 预签名 URL 中只包含本次 object key。
- 前端不接触 AccessKey、SecretKey 或 STS token。

### 1.3 浏览器分片上传与非可信心跳

要测：

- 前端使用预签名 URL 上传时可以记录进度，但不作为可信状态。
- heartbeat 只能更新 `client_progress`。
- heartbeat 不能让任务进入 `UPLOADED` 或 `READY`。

模拟断言：

- 多次 heartbeat 后 status 仍为 `INITIATED/UPLOADING`。
- `client_progress` 不影响 HeadObject 校验。

### 1.4 前端 complete 与 HeadObject 校验

要测：

- 前端调用 complete 后，状态先进入 `CLIENT_COMPLETED`。
- 服务端必须调用 HeadObject。
- bucket/key/size/metadata 全部匹配后才进入 `UPLOADED`。
- 对象不存在、size 不匹配、metadata 不匹配必须失败或保持待对账。

模拟断言：

- happy path：`CLIENT_COMPLETED -> UPLOADED`。
- size mismatch：状态为 `FAILED`，记录错误。
- object missing：保持 `CLIENT_COMPLETED`，等待 Worker 对账。

### 1.5 OSS Callback / EventBridge 事件幂等

要测：

- 同一事件重复到达只能处理一次。
- 事件晚于 complete 到达时，不应破坏状态。
- 事件早于 complete 到达时，也应能通过 HeadObject 推进状态。
- 无关 object key 事件应忽略。

模拟断言：

- duplicate event 计为 `ignored`。
- 第二次事件不会重复提交处理任务。
- 无关 key 不改变 upload 状态。

### 1.6 processed manifest 与 _SUCCESS

要测：

- 数据处理产物以 `manifest.json + _SUCCESS` 为 READY 信号。
- `_SUCCESS` 必须最后写入。
- manifest 缺失或内容不匹配，不能进入 READY。
- `_SUCCESS` 事件重复到达幂等。

模拟断言：

- manifest 和 `_SUCCESS` 都存在时进入 `READY`。
- 只有 `_SUCCESS` 无 manifest 时进入 `FAILED` 或保持 `PROCESSING`。
- duplicate `_SUCCESS` event 不重复改状态。

### 1.7 Worker 对账

要测：

- complete 后 EventBridge 丢失，Worker 能通过 HeadObject 推进。
- `INITIATED/UPLOADING` 过期后进入 `EXPIRED`。
- `UPLOADED` 但未提交处理任务时，Worker 能补交。

模拟断言：

- missing event 场景最终变为 `UPLOADED` 或 `READY`。
- expired 场景最终变为 `EXPIRED`。

## 2. 模拟流程设计

`backend/tools/oss_test.py` 将单文件内包含：

- `MockOSS`：内存对象存储，支持模拟预签名 URL PUT、HeadObject、PutObject。
- `UploadService`：模拟后端 upload 状态机。
- `RemoteEventStore`：模拟 `remote_events` 幂等记录。
- 多个 case：
  - `happy`：完整上传、事件、manifest、READY。
  - `duplicate-event`：重复 OSS event 和 `_SUCCESS` event。
  - `missing-event`：不发 OSS event，靠 reconcile 推进。
  - `size-mismatch`：HeadObject size 不符，进入 FAILED。
  - `expired`：上传会话过期。

## 3. 后续接真实 OSS 时的替换点

模拟脚本中只有 `MockOSS` 需要替换为真实 `OssClient`：

- `create_multipart_upload`
- `upload_part`
- `complete_multipart_upload`
- `head_object`
- `put_object`
- `get_json`

状态机和断言应保持不变。
