# ChatGPT 注册链路说明

本文记录当前后端的声明式 pipeline 注册链路。后端不再用请求级 `refresh_token` 模式分支；是否获取 Codex RT 由 pipeline stage 决定：包含 `oauth_codex` 就获取 RT/AT 并上传 sub2api，不包含则只保留 ChatGPT access/session token。

## 总览

前端统一通过 `POST /api/pipelines` 创建 pipeline，默认 preset 是 `full_chain` 完整链路；也可传自定义 `stages`，并用 `stop_after` 截停在任意 stage。

当前只有 5 个 WorkPool stage：

| stage | 作用 | 资源要求 |
| --- | --- | --- |
| `register` | 注册 ChatGPT 账号，写入 `chatgpt_accounts`，绑定账号身份 | declares `email_pool` + optional `proxy_pool`；当前邮箱领取由 `MicrosoftEmailService` 调用邮箱池完成 |
| `payment_link` | 复用账号身份生成 Team/Plus hosted 长链，写入 `payment_links` | optional `proxy_pool` |
| `payment` | 支付浏览器自动化框架位，当前 v1 stub | declares `card_pool` + `sms_pool`，optional `proxy_pool`；当前只实际领取支付代理 |
| `oauth_codex` | 对已注册账号执行 OpenAI OAuth PKCE，获取 Codex RT/AT | declares optional `sms_pool` + `proxy_pool`；当前 add-phone 接码走 settings 驱动 phone provider |
| `rt_keepalive` | 同步本地 `codex_tokens` 镜像与 sub2api 状态 | optional `proxy_pool` |

常用 preset：

- `full_chain`：`register → payment_link → payment → oauth_codex → rt_keepalive`（默认完整链路）
- `register_only`：`register`
- `register_with_codex_rt`：`register → oauth_codex`
- `link_only`：`register → payment_link`
- `account_paid`：`register → payment_link → payment`
- `account_paid_with_codex_rt`：`register → payment_link → payment → oauth_codex`
- `codex_rt_only`：`oauth_codex`

## 入口和队列

文件：`backend/api/jobs.py`

- `POST /api/pipelines` 处理统一 pipeline 创建请求。
- 创建 pipeline 的请求体只包含编排字段：
  - `count`
  - `preset` 或 `stages`
  - `stop_after`（空值跑完整链路；非空表示成功停在该 stage）
- 不在创建任务时传注册代理、支付代理 region、OAuth 接码、套餐等模块参数；这些都由对应 WorkPool / ResourcePool 配置读取。
- `POST /api/jobs` 可直接派发任意已注册 stage；API 会把 input 中的 `account_id` / `payment_link_id` / `proxy_id` 提升到 Job 字段，保证 worker 能 hydrate identity。

文件：`backend/core/pipeline.py`

- `create_pipeline()` 创建声明式 pipeline，只持久化 stage 列表和停止点；未传 `preset/stages` 时解析为 `full_chain`。
- `Job.type == stage.name`。
- job 成功后，`_advance_pipeline()` 从 `stages_json` 找下一个 stage；命中最后一步或 `stop_after` 时 pipeline 成功。
- carry-over 白名单：`account_id`、`payment_link_id`、`email_address`、`proxy_id`、`proxy_url`、`codex_rt`、`codex_at`。

文件：`backend/core/queue.py`

- `StagePoolManager` 为每个 stage 维护独立 `ThreadPoolExecutor`。
- `set_concurrency(stage, n)` 只调整对应 stage 的并发。
- 非 `register` 的 account-bound stage 会先 `ctx.require_identity()`，避免绕过账号身份。

## 账号身份绑定

一条账号生命周期内的身份以 `chatgpt_accounts` 为准：

- `proxy_id`
- `proxy_url`
- proxy region（通过 `Proxy.region` 反查）
- `user_agent`
- browser fingerprint
- cookies
- local_storage

`register` 必须得到完整的 `proxy_id + proxy_url` 才会继续。来源可以是：

1. `workpool.register.proxy_region` 指定注册代理 region；
2. 未配置 region 时由 `proxy_pool` 从可用代理中领取。

`payment_link` 和 `oauth_codex` 都通过 `ctx.effective_proxy_url()` 复用账号绑定代理；它们不会改绑账号代理。

`payment` 从 `workpool.payment.proxy_region` 读取支付代理 region，并通过 `proxy_pool` 选择该 region 下不同于账号代理的 proxy。当前 `payment` 仍是 stub，只验证和记录代理选择，浏览器自动化支付后续实现。

## register stage

文件：`backend/stages/register.py`

主要步骤：

1. 从 register WorkPool 配置读取注册代理 region，并从 email/proxy 资源池领取资源。
2. 绑定完整 `proxy_id + proxy_url`；绑定失败则 job 失败。
3. 如果指定邮箱，先用 `email_domain_policy` 做域名策略校验。
4. 合并全局 settings 和 job `extra_config`。
5. 构造 `MicrosoftEmailService`，由邮箱池领取 Microsoft 邮箱。
6. 使用 `AccessTokenOnlyRegistrationEngine` 完成 ChatGPT 注册。
7. 从注册 session 提取 access/session/id token，并导出 UA、fingerprint、cookies、local_storage。
8. 写入 `chatgpt_accounts`。
9. 如果 stage input 显式带 `also_record_to_at_pool=true`，额外写入 `access_token_accounts`。
10. 根据注册结果消费或退回邮箱。

当前 `register` stage 只负责 AT-only 注册；Codex RT 获取由后续 `oauth_codex` stage 负责。

## OAuth Codex RT stage

文件：`backend/stages/oauth_codex.py`

`oauth_codex` 面向已有 `chatgpt_accounts` 账号运行：

1. 加载账号 email/password/access identity。
2. 复用账号绑定 proxy、UA、fingerprint、cookies。
3. 创建 OpenAI OAuth PKCE authorize session。
4. 通过 `ProtocolOAuthClient` 跑登录状态机，必要时收 email OTP。
5. 只有遇到 add_phone 时才通过 settings 驱动的 phone provider（smsbower/fivesim/smsgiare）触发 SMS 接码。
6. 请求 OpenAI token endpoint，获取 Codex `refresh_token` / `access_token` / `id_token`。
7. Upsert 本地 `codex_tokens` 镜像。
8. 调用 sub2api 上传 RT；若 sub2api 未配置，则保持 `pending_upload`，等待后续同步。

OpenAI OAuth 参数来自 `backend/integrations/chatgpt/oauth.py` / `oauth_protocol.py`，token endpoint 与参数对齐 codex2api 参考实现。

## RT keepalive / sub2api 同步

文件：`backend/stages/rt_keepalive.py`、`backend/core/scheduler.py`

本项目不本地轮转 RT；sub2api 承担 RT 池维护和轮转。本项目只做两件事：

1. 对 `pending_upload` / `upload_failed` / `sync_failed` 或没有 `sub2api_external_id` 的 `codex_tokens` 行上传 RT。
2. 对已有 `sub2api_external_id` 的行拉取远端状态，更新本地镜像。

调度器每 60 秒扫描一次本地表，只 enqueue `alive=true` 且 `next_refresh_at IS NULL OR next_refresh_at <= now` 的 RT，同一 token 已有 queued/running `rt_keepalive` 时不会重复入队。正常同步后下一次状态探测为 24h 后。

如果 sub2api 未配置，`rt_keepalive` 记录 `pending_upload`，不增加失败计数，也不进入快速重试循环。

## 邮箱池链路

文件：`backend/integrations/mail/email_service.py`

当前注册邮箱服务是 `MicrosoftEmailService`。

领取邮箱：

- 不指定邮箱：从 Microsoft 邮箱池取一个 enabled 的账号。
- 指定邮箱：只取该邮箱；如果不在启用池中，注册失败。
- 被领取后邮箱状态变为 claimed。

获取 OTP：

1. 按 email 查询 `email_accounts`。
2. 读取 Microsoft OAuth `refresh_token` 和 `client_id`。
3. 调用 `wait_for_otp()` 轮询邮箱。
4. 找到 OTP 后写入 `email_messages`。

这里的 `refresh_token` 是 Microsoft 邮箱 OAuth token，不是 Codex RT。

## 落库

### `chatgpt_accounts`

`register` 在注册引擎返回结果后写入账号行；如果引擎直接抛异常，则 job 失败且不会创建账号行。关键字段包括：

- email/password/status/account_id/workspace_id
- access_token/id_token/session_token
- cookies/local_storage/browser_fingerprint/user_agent
- proxy_id/proxy_url
- last_error/registered_at/metadata_json

### `access_token_accounts`

只有 `register` stage input 显式带 `also_record_to_at_pool=true` 时写入，用于 Free AT 号池展示。

### `codex_tokens`

Codex RT 池的本地镜像。RT 是否可用以 `alive`、`sub2api_status` 和 sub2api 同步结果为准；`chatgpt_accounts.refresh_token` 不再作为 RT 池权威来源。

## 邮箱消费和回退

注册结束后按结果处理邮箱池状态：

- 注册成功：`mark_consumed(email, note="registered")`。
- 注册失败，但 result metadata 有 `mailbox_account_consumed = true`：`mark_consumed(email, note="registered_before_failure")`。
- 注册失败且未标记 consumed：`requeue(email)`，邮箱回到 available。

## 当前不包含的链路

- payment 浏览器自动化真实扣款流程；当前只保留 stage/resource/proxy 框架。
- sub2api 内部 RT 轮转实现；本项目只上传 RT 并同步状态。
- 本地 OAuth callback HTTP listener；当前按状态机中的 callback URL 字符串解析 `code`。
