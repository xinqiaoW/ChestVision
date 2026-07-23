# OSS 分片上传流程调整计划

## 背景

当前数据集上传和模型上传都采用 OSS multipart，但状态推进方式不一致：

- 数据集上传在后端 `CompleteMultipartUpload` 成功后进入 `CLIENT_COMPLETED`，再等待 OSS 完成事件/FC 回调推进到 `UPLOADED`。
- 模型上传在后端 `CompleteMultipartUpload` 成功后立即 `HeadObject` 校验，并创建 `model_versions` 与 `model_artifact_locations`，最终进入 `COMPLETED`。

经确认，当前系统的 OSS multipart 合并动作均由后端通过 OSS SDK 执行，不是浏览器单方面声明完成。因此远程 FC 函数触发的 OSS 完成事件不再作为必要流程。后续将禁用远程 FC 函数，后端不再兼容旧的 OSS 事件确认流程。

## 目标

统一数据集上传和模型上传的 OSS 文件分片上传层：

```text
创建上传会话
→ 初始化 OSS multipart upload
→ 前端逐片 PUT 到 OSS
→ 前端提交 part_number + etag
→ 后端 CompleteMultipartUpload
→ 后端 HeadObject 校验最终对象
→ 后端直接推进业务完成状态
```

删除 `CLIENT_COMPLETED` 作为持久业务状态。

删除 OSS 完成事件/FC 回调依赖。

数据集和模型的差异只保留在业务完成 hook 中。

## 新流程整体定义

### 参与方

新流程只有三个直接参与方：

- 浏览器前端：负责选择单个文件、切片、逐片 PUT 到 OSS、统计上传进度、提交分片结果。
- 后端业务服务：负责创建上传会话、生成固定 OSS object key、签发 part URL、调用 OSS complete、执行 `HeadObject` 可信确认、推进业务状态。
- OSS：保存 multipart 临时分片和最终对象。

不再包含 FC/EventBridge 作为上传完成链路的一部分。

### 权限与密钥边界

浏览器永远不持有 OSS AccessKey。

浏览器只能拿到后端签发的短期预签名 URL：

- URL 绑定固定 `bucket/object_key`
- URL 绑定固定 `upload_id`
- URL 绑定固定 `part_number`
- URL 有过期时间

最终的 `CompleteMultipartUpload` 必须由后端使用 OSS SDK 执行。浏览器只能把它实际上传成功后拿到的 `part_number + etag` 返回给后端。

### 标准上传生命周期

所有大文件上传统一采用以下生命周期：

```text
1. create_upload_session
   后端创建业务上传记录
   后端生成 object_key
   后端调用 InitMultipartUpload
   后端保存 oss_upload_id / part_size / headers 等 metadata
   返回 upload_id、part_size、签名接口地址

2. sign_parts
   前端按 part_number 批量请求签名
   后端校验会话和 part_number
   后端返回每个 part 的 PUT URL

3. upload_parts
   前端按 part_size 切片
   前端逐片 PUT 到 OSS
   前端记录每片返回的 ETag
   前端计算进度、速度、预计剩余时间

4. complete_upload
   前端提交全部 part_number + etag
   后端校验会话、parts、expected_size
   后端调用 OSS CompleteMultipartUpload
   后端调用 HeadObject 校验最终对象
   后端执行业务完成 hook

5. done
   数据集进入 UPLOADED
   模型上传会话同样进入 UPLOADED，并创建模型主表与产物表记录
```

### 后端完成确认定义

上传完成不再由客户端声明，也不再由 OSS 事件声明。

后端完成确认必须同时满足：

1. 当前上传会话存在且状态允许完成。
2. `parts` 通过后端校验。
3. OSS `CompleteMultipartUpload` 成功。
4. OSS `HeadObject` 能查到最终对象。
5. 如果记录了 `expected_size`，`HeadObject.content_length == expected_size`。
6. 业务完成 hook 成功。
7. 数据库事务提交成功。

只有以上全部成功，接口才返回业务完成状态。

如果第 3 步之后、第 7 步之前失败，后端应把上传记录标记为 `FAILED`，并保留错误信息。当前阶段不维护 OSS 事件补偿，必要时由开发人员通过后台命令或管理脚本清理 OSS 对象和数据库状态。

### 事务边界

OSS 操作和数据库事务不能真正原子化，因此采用以下规则：

- 先完成 OSS `CompleteMultipartUpload`。
- 再执行 OSS `HeadObject`。
- 再写数据库业务完成状态。
- 数据库提交失败时，不回滚 OSS 对象，只记录错误并由人工或后续清理脚本处理。

禁止先把数据库写成完成态，再调用 OSS complete。

### 幂等性要求

当前不兼容旧流程，但新流程仍需要基础幂等：

- 已完成的数据集上传再次 complete，应返回当前 `UPLOADED` 记录，不能重复调用 OSS complete。
- 已完成的模型上传再次 complete，应返回当前 `UPLOADED` 记录和对应模型，不能重复创建 `model_versions`。
- 已取消、失败、过期的上传会话不能继续签名或 complete。
- abort 对已经取消、失败、过期的会话可直接返回当前状态。
- abort 对已经完成的会话必须拒绝。

### 前端进度定义

上传进度只表示浏览器上传 part 的过程，不表示业务对象已经登记完成。

前端应展示：

- `uploaded_parts / total_parts`
- `uploaded_mb / total_mb`
- 上传速度
- 预计剩余时间

当所有 part 上传完成后，前端调用 complete 接口。complete 接口期间可显示“正在合并并校验”。complete 成功后才显示最终业务状态。

## 统一状态机

### 数据集上传

目标状态：

```text
INITIATED
→ UPLOADING
→ UPLOADED
→ FAILED / CANCELLED / EXPIRED
```

说明：

- `UPLOADED` 表示数据集 ZIP 已经在 OSS 中完成合并，并通过后端 `HeadObject` 校验。
- 当前没有数据集后处理流程，因此不再使用 `READY` 作为真实状态。
- 后续如果增加数据集预处理，再新增：

```text
UPLOADED
→ PROCESSING
→ READY
```

但当前阶段不保留 `READY`。

### 模型上传

目标状态：

```text
INITIATED
→ UPLOADING
→ UPLOADED
→ FAILED / CANCELLED / EXPIRED
```

说明：

- `UPLOADED` 表示模型 `.pt` 文件已在 OSS 中完成合并，并通过后端 `HeadObject` 校验。
- `UPLOADED` 后必须已经创建：
  - `model_versions`
  - `model_artifact_locations`
- 上传模型不会自动替换当前默认推理模型。

## OSS 事件和 FC 回调处理

确认删除以下依赖：

- OSS `ObjectCreated:CompleteMultipartUpload` 事件作为上传完成依据
- EventBridge/OSS 事件通知到 FC 的链路
- FC 调用后端上传确认 callback 的链路
- 后端对 OSS 完成事件 callback 的业务状态推进

远程 FC 函数将被禁用。

后端不再保留旧流程兼容逻辑。

## 后端接口调整

### 删除数据集 OSS callback 接口

删除：

```text
POST /api/training/remote/callbacks/oss/multipart-complete
```

同步删除：

- `OssMultipartUploadEventRequest`
- `handle_oss_multipart_complete_callback`
- `handle_oss_multipart_upload_event`
- `OSS_MULTIPART_COMPLETE_EVENT`
- 上传会话响应中的 `server_confirm_event`

### 数据集完成上传接口

保留并改造：

```text
POST /api/training/remote/uploads/{upload_id}/multipart/complete
```

目标行为：

1. 校验上传会话属于当前用户。
2. 校验会话仍处于 `INITIATED` 或 `UPLOADING`。
3. 校验 `parts`：
   - 非空
   - part_number 为整数
   - part_number 在 `1..10000`
   - part_number 不重复
   - etag 非空且长度合理
4. 如果存在 `expected_size`，校验分片数量与 `part_size` 推导结果一致。
5. 后端调用 OSS `CompleteMultipartUpload`。
6. 后端调用 OSS `HeadObject`。
7. 校验对象大小与 `expected_size` 一致。
8. 写入：
   - `status = UPLOADED`
   - `actual_size`
   - `etag`
   - `server_verified_at`
   - `updated_at`
   - `metadata.multipart_completed_at`
   - `metadata.completed_part_count`
   - `metadata.complete_request_id`
9. 返回数据集上传记录。

不再写入 `CLIENT_COMPLETED`。

### 模型完成上传接口

保留：

```text
POST /api/model-management/uploads/{upload_id}/multipart/complete
```

目标行为：

1. 校验上传会话属于当前管理员。
2. 校验会话仍处于 `INITIATED` 或 `UPLOADING`。
3. 校验模型名称、版本、场景、`.pt` 后缀、`model_type`、版本唯一性。
4. 校验 `parts`。
5. 后端调用 OSS `CompleteMultipartUpload`。
6. 后端调用 OSS `HeadObject`。
7. 校验对象大小与 `expected_size` 一致。
8. 创建 `model_versions`。
9. 创建 `model_artifact_locations`。
10. 写入：
    - `model_uploads.status = UPLOADED`
    - `model_uploads.model_version_id`
    - `actual_size`
    - `etag`
    - `server_verified_at`
    - `updated_at`
11. 返回上传记录和模型记录。

不再写入 `CLIENT_COMPLETED`。

## OSS 文件分片上传抽象规划

### 抽象原则

抽象 OSS 文件分片上传流程，不抽象数据集或模型业务。

文件分片上传层负责回答：

- 如何初始化 OSS multipart
- 如何保存和读取 multipart metadata
- 如何校验 part_number 和 etag
- 如何签发 part URL
- 如何 complete
- 如何 abort
- 如何 `HeadObject` 确认最终对象

业务层负责回答：

- 谁有权限上传
- 文件名和业务字段是否合法
- object key 如何生成
- 完成后写什么业务状态
- 是否创建模型版本
- 是否允许删除
- 是否触发训练或推理相关逻辑

### 建议模块位置

建议新增独立共享模块：

```text
backend/app/oss_multipart/
├── __init__.py
├── errors.py
├── schemas.py
├── service.py
└── validators.py
```

不建议继续放在 `app/train` 下。原因是该抽象会同时服务数据集管理、模型管理，未来也可能服务报告、影像或其他大文件上传，不应绑定训练模块命名。这个模块应比 OSS 基础操作更高一层：它封装完整文件分片上传流程，但不处理业务实体。

### 文件分片上传层数据结构

建议定义以下内部数据结构。

#### MultipartUploadTarget

表示一个业务上传会话映射到 OSS 的固定目标：

```python
class MultipartUploadTarget:
    upload_id: str
    status: str
    bucket: str
    object_key: str
    oss_upload_id: str
    part_size: int
    expected_size: int | None
    expires_seconds: int
```

它不要求必须是 ORM 对象。数据集和模型服务可以从各自的 ORM 记录转换出这个对象。

#### SignedPart

表示一个签名后的 part 上传 URL：

```python
class SignedPart:
    part_number: int
    method: str
    url: str
    headers: dict[str, str]
    expires_seconds: int
```

#### CompletedPart

表示前端已经上传成功并提交给后端的 part：

```python
class CompletedPart:
    part_number: int
    etag: str
```

#### ConfirmedObject

表示 OSS 最终对象已经可信存在：

```python
class ConfirmedObject:
    bucket: str
    object_key: str
    content_length: int | None
    etag: str | None
    content_type: str | None
    headers: dict
```

业务层只能基于 `ConfirmedObject` 推进完成状态。

### 文件分片上传层服务接口

建议提供 `OssFileMultipartUploadService`：

```python
class OssFileMultipartUploadService:
    def init_upload(
        self,
        object_key: str,
        content_type: str,
        headers: dict[str, str],
    ) -> str:
        ...

    def sign_parts(
        self,
        target: MultipartUploadTarget,
        part_numbers: list[int],
        expires_seconds: int | None = None,
    ) -> list[SignedPart]:
        ...

    def complete_and_confirm(
        self,
        target: MultipartUploadTarget,
        parts: list[dict],
    ) -> ConfirmedObject:
        ...

    def abort(
        self,
        target: MultipartUploadTarget,
    ) -> None:
        ...
```

其中 `complete_and_confirm()` 必须按固定顺序执行：

```text
normalize_completed_parts
→ validate_expected_part_count
→ OSS CompleteMultipartUpload
→ OSS HeadObject
→ validate_expected_size
→ return ConfirmedObject
```

### 文件分片上传层校验函数

建议放在 `validators.py`：

```python
def normalize_part_numbers(values: list[int]) -> list[int]:
    ...

def normalize_completed_parts(values: list[dict]) -> list[CompletedPart]:
    ...

def total_part_count(file_size: int, part_size: int) -> int:
    ...

def validate_expected_part_count(
    expected_size: int | None,
    part_size: int,
    part_count: int,
) -> None:
    ...

def validate_confirmed_object_size(
    expected_size: int | None,
    content_length: int | None,
) -> None:
    ...
```

### 文件分片上传层常量

建议集中定义：

```python
DEFAULT_MULTIPART_PART_SIZE = 32 * 1024 * 1024
MIN_MULTIPART_PART_SIZE = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10000
MAX_PARTS_PER_SIGN = 50
```

数据集和模型上传都引用这些常量，不再各自复制。

### 业务层调用模板

#### 创建上传会话

业务层负责：

1. 校验用户权限。
2. 校验业务字段。
3. 生成 `upload_uuid`。
4. 生成固定 `object_key`。
5. 构造 OSS metadata headers。
6. 调用 `OssFileMultipartUploadService.init_upload()`。
7. 写入业务上传表。

文件分片上传层不创建数据库记录。

#### 签名 part

业务层负责：

1. 查询上传记录。
2. 校验记录归属和状态。
3. 从上传记录构造 `MultipartUploadTarget`。
4. 调用 `OssFileMultipartUploadService.sign_parts()`。
5. 必要时把状态从 `INITIATED` 改为 `UPLOADING`。

#### 完成上传

业务层负责：

1. 查询上传记录。
2. 校验记录归属和状态。
3. 从上传记录构造 `MultipartUploadTarget`。
4. 调用 `OssFileMultipartUploadService.complete_and_confirm()`。
5. 执行业务完成 hook。
6. 提交数据库事务。

数据集完成 hook：

```text
status = UPLOADED
actual_size = confirmed.content_length
etag = confirmed.etag
server_verified_at = now
```

模型完成 hook：

```text
创建 model_versions
创建 model_artifact_locations
model_uploads.status = UPLOADED
model_uploads.model_version_id = model_versions.id
actual_size = confirmed.content_length
etag = confirmed.etag
server_verified_at = now
```

#### 取消上传

业务层负责：

1. 查询上传记录。
2. 校验记录归属和状态。
3. 从上传记录构造 `MultipartUploadTarget`。
4. 调用 `OssFileMultipartUploadService.abort()`。
5. 写入 `CANCELLED`。

### 明确不抽象的内容

以下内容必须留在数据集管理或模型管理各自服务中：

- 数据集 `.zip` 校验
- 数据集名称校验
- 数据集场景绑定
- 模型 `.pt` 校验
- 模型名称和版本校验
- `model_type` 枚举校验
- 模型版本唯一性
- 模型版本和模型产物创建
- 训练来源模型登记
- 默认推理模型切换
- 本地缓存同步
- 删除联动

### 从现有代码迁移的重复点

优先迁移以下重复逻辑：

- `_normalize_part_numbers`
- `_normalize_completed_parts`
- `_total_part_count`
- `_normalize_part_size`
- `_require_multipart_metadata`
- `_ensure_upload_can_accept_parts` 中与 OSS 协议相关的部分
- `sign_multipart_part_urls` 中构造 signed parts 的部分
- `complete_multipart_upload` 中 OSS complete + HeadObject 的部分
- `abort_multipart_upload` 中 OSS abort 的部分

状态判断、权限判断和业务字段写入不迁移到文件分片上传层。

## 数据库调整

### 数据集上传

`dataset_uploads.status` 不再使用：

- `CLIENT_COMPLETED`
- `READY`

当前有效状态：

- `INITIATED`
- `UPLOADING`
- `UPLOADED`
- `FAILED`
- `EXPIRED`
- `CANCELLED`

需要清理开发数据库中的旧状态：

```sql
UPDATE dataset_uploads
SET status = 'UPLOADED'
WHERE status IN ('CLIENT_COMPLETED', 'READY');
```

该项目当前仍处于开发阶段，不维护旧流程兼容迁移脚本。只需要保证从 0 部署时表结构和初始化逻辑正确。

### 模型上传

`model_uploads.status` 不再使用：

- `CLIENT_COMPLETED`
- `COMPLETED`

当前有效状态：

- `INITIATED`
- `UPLOADING`
- `UPLOADED`
- `FAILED`
- `EXPIRED`
- `CANCELLED`

开发数据库可直接清理：

```sql
UPDATE model_uploads
SET status = 'UPLOADED'
WHERE status IN ('CLIENT_COMPLETED', 'COMPLETED');
```

## 前端调整

数据集上传页面和模型上传页面统一按后端完成接口的最终返回值展示结果：

- complete 接口返回成功后，数据集直接显示 `已上传`。
- complete 接口返回成功后，模型直接显示 `已完成` 或进入模型列表。
- 不再展示“等待 OSS 完成事件确认”。
- 不再处理 `CLIENT_COMPLETED`。
- 不再提示 `server_confirm_event`。

上传进度条仍由前端 multipart 上传过程计算：

- 已上传分片数 / 总分片数
- 已上传 MB / 总 MB
- 预计剩余时间

这些进度信息来自浏览器上传 part 的实际完成情况，不依赖 OSS 事件。

## 数据集上传与模型上传迁移规划

### 迁移原则

本次迁移采用一次性切换，不保留旧 OSS 事件确认流程兼容。

迁移后，系统只承认一种上传完成路径：

```text
业务 complete 接口
→ OssFileMultipartUploadService.complete_and_confirm()
→ 业务完成 hook
→ 数据库提交
```

不再存在：

- 数据集上传完成后等待 FC 回调。
- 数据集 `CLIENT_COMPLETED` 持久状态。
- 当前无后处理阶段下的数据集 `READY` 状态。
- 模型上传 `CLIENT_COMPLETED` 中间状态。
- `server_confirm_event` 响应字段。

### 迁移顺序

建议按以下顺序迁移，降低半改状态下的行为分叉：

1. 新增 OSS 文件分片上传抽象模块。
2. 先迁移数据集上传到新抽象和新状态机。
3. 再迁移模型上传到同一抽象和新状态机。
4. 删除 OSS/FC callback 旧路径。
5. 调整前端状态展示和训练数据集筛选。
6. 清理开发数据库旧状态。
7. 运行后端和前端回归检查。

原因：

- 数据集上传是旧 FC 事件流程的主要使用方，应先消除状态机差异。
- 模型上传已经接近新流程，迁移重点是接入抽象，并删除 `CLIENT_COMPLETED` / `COMPLETED` 上传状态。
- 旧 callback 删除应放在数据集 complete 改造之后，避免中间状态下没有任何完成路径。

### 数据集上传迁移

#### 后端文件范围

重点调整：

- `backend/app/train/remote_train_dataset_service.py`
- `backend/app/train/remote_train_router.py`
- `backend/app/train/remote_train_schemas.py`
- `backend/app/train/remote_train_utils.py`
- 新增的 `backend/app/oss_multipart/*`

可能联动调整：

- `backend/app/train/remote_train_job_service.py`
- `backend/app/train/remote_train_serializers.py`
- 前端数据集 API 调用文件
- 模型训练页面的数据集筛选逻辑

#### 创建上传会话

保留现有接口：

```text
POST /api/training/remote/uploads
```

目标改造：

- 删除响应中的 `server_confirm_event`。
- 保留 multipart 响应字段：
  - `oss_upload_id`
  - `part_size`
  - `max_parts`
  - `max_parts_per_sign`
  - `sign_parts_endpoint`
  - `complete_endpoint`
  - `abort_endpoint`
- `metadata.upload_mode` 可继续保存为 `multipart`，但不再表达 FC 确认流程。
- 如果仍保留 `presigned_put` 兼容测试路径，需要单独评估；按当前“只保留分片上传”的方向，建议删除 `presigned_put`。

#### 签名 part

保留现有接口：

```text
POST /api/training/remote/uploads/{upload_id}/multipart/parts/sign
```

目标改造：

- 查询 `DatasetUpload`。
- 校验用户归属。
- 校验状态只允许：
  - `INITIATED`
  - `UPLOADING`
- 从上传记录构造 `MultipartUploadTarget`。
- 调用 `OssFileMultipartUploadService.sign_parts()`。
- 如果当前是 `INITIATED`，更新为 `UPLOADING`。

#### 完成上传

保留现有接口：

```text
POST /api/training/remote/uploads/{upload_id}/multipart/complete
```

目标改造：

```text
DatasetUpload
→ MultipartUploadTarget
→ OssFileMultipartUploadService.complete_and_confirm()
→ dataset completion hook
→ status = UPLOADED
```

数据集完成 hook 写入：

- `status = UPLOADED`
- `actual_size = confirmed.content_length`
- `etag = confirmed.etag`
- `server_verified_at = now`
- `client_completed_at` 不再需要写入；如字段暂时保留，应停止依赖它。
- `error_message = None`
- `updated_at = now`
- `metadata.multipart_completed_at`
- `metadata.completed_part_count`
- `metadata.complete_request_id`

如果上传记录已经是 `UPLOADED`：

- complete 接口直接返回 `serialize_upload(upload)`。
- 不重复调用 OSS complete。

如果状态是 `FAILED / EXPIRED / CANCELLED`：

- 拒绝 complete。

#### 删除 READY 相关逻辑

删除或停用：

- `mark_dataset_ready`
- `RemoteDatasetReadyRequest`
- `/uploads/{upload_id}/ready`
- `processed_prefix / manifest_key / success_key` 在当前数据集上传完成语义中的必填或完成判断

当前阶段保留字段不影响表结构，但业务不再使用它们判断可训练。

训练启动逻辑调整：

- 原先允许 `upload.status in {"UPLOADED", "READY"}`。
- 改为只允许 `upload.status == "UPLOADED"`。
- `data_yaml` 如果没有后处理结果，应由远程训练容器从 ZIP 解压并定位，而不是要求 `processed_prefix/data.yaml`。

训练数据集选择逻辑调整：

- 只展示 `status = UPLOADED` 的数据集。
- 不再把 `READY` 作为可选状态。

#### 删除 OSS callback

删除：

- `handle_oss_multipart_upload_event`
- `handle_oss_multipart_complete_callback`
- `OssMultipartUploadEventRequest`
- `OSS_MULTIPART_COMPLETE_EVENT`
- `_require_internal_callback_token` 中仅服务 OSS callback 的使用点

注意：

- `REMOTE_TRAINING_CALLBACK_SECRET` 可能仍用于远程训练容器回调或内部回调认证，不能因为删除 OSS callback 就直接删除该环境变量。删除前必须确认其他回调路径是否还在使用。

### 模型上传迁移

#### 后端文件范围

重点调整：

- `backend/app/model_management/service.py`
- `backend/app/model_management/router.py`
- `backend/app/model_management/schemas.py`
- 新增的 `backend/app/oss_multipart/*`

可能联动调整：

- `backend/app/entity/db_models.py`
- `backend/app/train/remote_train_artifact_service.py`
- 前端模型管理 API 调用文件

#### 创建上传会话

保留接口：

```text
POST /api/model-management/uploads
```

目标改造：

- 继续校验：
  - `.pt`
  - `model_type`
  - `model_name`
  - `version`
  - 同场景版本唯一
- 生成 object key：

```text
{oss_prefix}/models/uploaded/{user_id}/{upload_id}/model.pt
```

- 调用 `OssFileMultipartUploadService.init_upload()`。
- 写入 `model_uploads.status = INITIATED`。
- 返回 multipart 信息。

#### 签名 part

保留接口：

```text
POST /api/model-management/uploads/{upload_id}/multipart/parts/sign
```

目标改造：

- 查询 `ModelUpload`。
- 校验管理员归属和状态。
- 从上传记录构造 `MultipartUploadTarget`。
- 调用 `OssFileMultipartUploadService.sign_parts()`。
- 如果当前是 `INITIATED`，更新为 `UPLOADING`。

#### 完成上传

保留接口：

```text
POST /api/model-management/uploads/{upload_id}/multipart/complete
```

目标改造：

```text
ModelUpload
→ MultipartUploadTarget
→ OssFileMultipartUploadService.complete_and_confirm()
→ model completion hook
→ status = UPLOADED
```

模型完成 hook 写入：

- 创建 `model_versions`
- 创建 `model_artifact_locations`
- `model_uploads.status = UPLOADED`
- `model_uploads.model_version_id = model_versions.id`
- `actual_size = confirmed.content_length`
- `etag = confirmed.etag`
- `server_verified_at = now`
- `error_message = None`
- `updated_at = now`
- `metadata.multipart_completed_at`
- `metadata.completed_part_count`
- `metadata.complete_request_id`

不再写入 `CLIENT_COMPLETED`。

如果上传记录已经是 `UPLOADED`：

- complete 接口直接返回当前上传记录和关联模型。
- 不重复调用 OSS complete。
- 不重复创建 `model_versions`。

如果状态是 `FAILED / EXPIRED / CANCELLED`：

- 拒绝 complete。

#### 模型列表与下载

模型列表只读取 `model_versions.status = active`。

上传会话表 `model_uploads` 不直接作为“所有模型”列表来源。

模型下载仍通过：

```text
GET /api/model-management/models/{model_version_id}/download-url
```

下载 URL 与上传抽象分开处理；可以复用同一个 `OssStorageGateway.sign_get_url()`，但不放进 multipart 抽象。

### 前端迁移

#### 数据集上传页面

调整点：

- 删除 `CLIENT_COMPLETED` 展示逻辑。
- 删除“等待 OSS 完成事件确认”展示逻辑。
- 删除 `server_confirm_event` 相关处理。
- complete 接口成功后直接刷新列表，状态显示为“已上传”。
- 数据集可训练条件改为 `status === "UPLOADED"`。

#### 模型上传页面

调整点：

- 删除 `CLIENT_COMPLETED` 展示逻辑。
- complete 接口成功后直接进入“上传完成”展示，并刷新模型列表。
- 模型上传完成状态来自 `model_uploads.status === "UPLOADED"` 和返回的 `model`。
- 上传模型不自动设为默认。

#### 共用前端上传逻辑

前端也可以抽象“分片上传执行器”，但边界同样只放传输逻辑：

- 文件切片
- 并发上传 part
- 暂停/继续
- 取消
- 速度统计
- 剩余时间估算
- part ETag 收集
- complete 调用

不放入：

- 数据集字段校验
- 模型字段校验
- 业务状态文案
- 上传完成后的页面跳转

### 数据库迁移

当前仍处开发阶段，不写兼容迁移脚本。

直接执行数据库清理命令即可：

```sql
UPDATE dataset_uploads
SET status = 'UPLOADED'
WHERE status IN ('CLIENT_COMPLETED', 'READY');

UPDATE model_uploads
SET status = 'UPLOADED'
WHERE status IN ('CLIENT_COMPLETED', 'COMPLETED');
```

如存在依赖 `READY` 的测试数据，应同时调整测试用例和前端 mock。

### 迁移完成后的代码检查清单

后端不应再出现：

```text
CLIENT_COMPLETED
READY
OSS_MULTIPART_COMPLETE_EVENT
OssMultipartUploadEventRequest
handle_oss_multipart_upload_event
server_confirm_event
callbacks/oss/multipart-complete
```

例外：

- 文档中作为历史说明出现可以保留。
- 数据库字段名如 `client_completed_at` 可以暂时保留，但业务逻辑不再依赖。

前端不应再出现：

```text
CLIENT_COMPLETED
READY
server_confirm_event
等待 OSS 完成事件
```

### 迁移风险

主要风险：

- 数据库提交失败时 OSS 对象已完成，可能留下孤立对象。
- 删除 FC 回调后，不再有事件补偿路径。
- 数据集 `READY` 删除后，训练页面和远程训练启动逻辑必须同步只认 `UPLOADED`。
- 如果前端仍按旧状态展示，会出现“上传后仍等待确认”的错误 UI。

当前项目处于开发阶段，接受通过管理命令清理孤立对象和旧状态，不做旧流程兼容。

## 实施步骤

1. 新增 OSS 文件分片上传抽象模块。
2. 将数据集上传和模型上传重复的 part 校验、签名、complete、abort 逻辑迁移到文件分片上传抽象层。
3. 改造数据集 complete 接口：
   - 后端 complete 成功后立即 HeadObject
   - 直接写 `UPLOADED`
   - 删除 `CLIENT_COMPLETED`
4. 改造模型 complete 接口：
   - 删除 `CLIENT_COMPLETED` / `COMPLETED` 上传状态
   - complete + HeadObject 成功后直接写 `UPLOADED`
5. 删除 OSS callback route/schema/service 方法/常量。
6. 删除响应中的 `server_confirm_event`。
7. 移除前端对 `CLIENT_COMPLETED` 和 OSS callback 等待态的展示逻辑。
8. 清理开发数据库中的旧状态。
9. 确认远程 FC 函数已禁用。
10. 跑后端编译、主应用导入、上传接口单元/集成测试。

## 验证点

### 数据集上传

- 创建上传会话返回 multipart 信息。
- part 签名可用。
- part 上传完成后，complete 接口返回 `status = UPLOADED`。
- OSS 中对象存在，`actual_size` 与 `expected_size` 一致。
- 数据集列表只把 `UPLOADED` 视为可用于训练。
- 不再调用 OSS callback 接口。

### 模型上传

- 创建上传会话返回 multipart 信息。
- part 签名可用。
- part 上传完成后，complete 接口返回 `status = UPLOADED`。
- 同时创建 `model_versions` 和 `model_artifact_locations`。
- 模型列表能看到上传模型。
- 模型不会自动变成默认推理模型。

### 回归检查

- 删除上传会话仍能 abort 未完成的 OSS multipart。
- 已完成上传不能 abort。
- 取消上传后不能继续签名或 complete。
- 数据集上传失败不污染训练可选数据集。
- 模型上传失败不污染模型列表。
