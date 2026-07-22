# To Test: Real Aliyun SDK Access

本文档对应真实阿里云 SDK 访问测试，不再使用 mock。

脚本：

```bash
python backend/tools/oss_real_test.py --case all
# 含义：依次运行 OSS 的全部真实 SDK 测试用例；会访问真实 OSS。

python backend/tools/pai_dlc_real_test.py --case list-only
# 含义：只查询 PAI-DLC 资源规格和任务列表；不创建训练任务。

python backend/tools/pai_dlc_real_test.py --case create-stop
# 含义：创建一个真实 PAI-DLC 任务，然后在可停止状态调用 StopJob；可能产生费用。
```

安全提醒：

- 两个脚本默认不会请求真实云端，必须先在文件顶部填写全局变量，并将 `CONFIRM_REAL_CLOUD_CALLS = True`。
- PAI-DLC 的 `create-wait` / `create-stop` 会创建真实 DLC Job，可能产生费用。
- OSS 测试默认会清理测试对象；如需保留对象排查，将 `DELETE_OBJECTS_AFTER_TEST = False`。

## 1. OSS 真实 SDK 测试

脚本：`backend/tools/oss_real_test.py`

需要填写的全局变量：

- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_SECURITY_TOKEN`，可选，仅后端签名凭证使用 STS 时填写；不会给浏览器
- `OSS_ENDPOINT`
- `OSS_REGION`
- `OSS_BUCKET`
- `OSS_TEST_PREFIX`
- `CONFIRM_REAL_CLOUD_CALLS`

测试功能：

- `config-check`：只检查全局变量是否填写，不访问云端。
- `simple`：服务端 SDK PutObject、HeadObject、GetObject、DeleteObject 连通性。
- `multipart`：InitMultipartUpload、UploadPart、CompleteMultipartUpload、HeadObject。
- `flow`：后端生成固定 object key 的 PUT 预签名 URL，模拟浏览器 HTTP PUT 上传数据集 ZIP，后端 HeadObject 校验 metadata，写入 manifest.json 和 `_SUCCESS`，ListObjects 观察前缀，再清理。
- `signed-url`：最小化 PUT 预签名 URL 测试，用标准库 HTTP PUT 上传文本，再 HeadObject 校验。

常用 OSS 指令：

```bash
python backend/tools/oss_real_test.py --case config-check
# 含义：只检查全局变量是否填写完整；不会访问 OSS，也不会产生对象或费用。

python backend/tools/oss_real_test.py --case simple
# 含义：测试服务端 OSS SDK 的基础对象操作：上传、查看元信息、读取、删除。

python backend/tools/oss_real_test.py --case multipart
# 含义：测试服务端 OSS SDK 的分片上传能力，用于验证大文件上传相关 SDK 行为。

python backend/tools/oss_real_test.py --case flow
# 含义：测试推荐业务流程：后端签发短期 PUT 预签名 URL，浏览器用 URL 上传，
#       后端 HeadObject 校验，再写 manifest.json 和 _SUCCESS。

python backend/tools/oss_real_test.py --case signed-url
# 含义：测试最小化 PUT 预签名 URL 上传一个文本对象。
```

排查提示：

- 如果生成的 URL 路径里出现 `%2F`，例如 `a%2Fb.txt`，说明 object key 中的 `/` 被编码了。OSS Python SDK 生成 PUT 预签名 URL 时必须设置 `slash_safe=True`，否则 URL 通常不能直接使用，可能返回 403。
- 如果返回 403，先看脚本打印的 OSS XML 错误体：
  - `SignatureDoesNotMatch`：多半是 URL 被改动、headers 不一致、`slash_safe=True` 未生效、URL 已过期。
  - `AccessDenied`：多半是 RAM Policy、Bucket Policy 或接入点策略权限不足。
  - `InvalidAccessKeyId`：AK 不存在、填错或被禁用。

依赖：

```bash
pip install oss2
```

## 2. PAI-DLC 真实 SDK 测试

脚本：`backend/tools/pai_dlc_real_test.py`

需要填写的全局变量：

- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `ALIYUN_SECURITY_TOKEN`，可选
- `PAI_REGION_ID`
- `PAI_WORKSPACE_ID`
- `PAI_RESOURCE_ID`，使用资源配额时填写
- `PAI_IMAGE_URI`
- `PAI_ECS_SPEC`
- `PAI_JOB_TYPE`
- `PAI_USER_COMMAND`
- `EXISTING_JOB_ID`
- `CONFIRM_REAL_CLOUD_CALLS`

测试功能：

- `config-check`：只检查全局变量是否填写，不访问云端。
- `list-only`：ListEcsSpecs、ListJobs，验证凭证、region、workspace 可用。
- `get-existing`：GetJob 查询已有 Job 状态。
- `create-wait`：CreateJob 后轮询 GetJob 直到终态。
- `create-stop`：CreateJob 后轮询到非终态，再调用 StopJob，继续轮询确认停止。
- `stop-existing`：对已有 Job 调用 StopJob。

依赖：

```bash
pip install alibabacloud_credentials alibabacloud_tea_openapi alibabacloud_pai_dlc20201203==1.4.17
```

## 3. 预期排查顺序

1. 先跑 `config-check`，确认脚本读取到你填的变量。
2. OSS 先跑 `simple`，再跑 `multipart`，最后跑 `flow`。
3. PAI-DLC 先跑 `list-only`，确认 AK、region、workspace、endpoint 正确。
4. 再用控制台已有 Job 跑 `get-existing`。
5. 最后跑 `create-stop`，验证真实创建和停止能力。
6. 确认资源规格和镜像没问题后，再跑 `create-wait`。
