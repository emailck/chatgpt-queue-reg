# ChatGPT 注册链路说明

本文记录当前后端的声明式 pipeline 注册链路和 AT/RT 语义。

## AT / RT 边界

- **AT**：ChatGPT Web Session access token，来自 `chatgpt.com/api/auth/session`，由 `chatgpt_session` 维护并写入 `ChatGPTAccount.access_token`。
- **RT**：OpenAI OAuth refresh token，来自 OAuth PKCE，显式通过 `openai_oauth` 获取并写入 `OpenAIRefreshToken.refresh_token`。
- OAuth 短期 access token 只保存为 `OpenAIRefreshToken.oauth_access_token`，不是业务 AT，不传给 sub2api 的 `credentials.access_token`。
- `ChatGPTAccount.session_token` 是 ChatGPT Web session token，不是 RT。

## 总览

前端通过 `POST /api/pipelines` 创建 pipeline，默认 preset 是 `full_chain` 完整链路；也可传自定义 `stages`，并用 `stop_after` 截停在任意 stage。

当前 WorkPool stage：

| stage | 作用 | 资源要求 |
| --- | --- | --- |
| `register` | 注册 ChatGPT 账号，写入 `chatgpt_accounts`，绑定账号身份 | email + optional `proxy_pool` |
| `payment_link` | 复用账号身份生成 Team/Plus hosted 长链，写入 `payment_links` | optional `proxy_pool` |
| `payment` | 支付浏览器自动化 | `card_pool` + `sms_pool` + optional `proxy_pool` |
| `chatgpt_session` | 刷新/规范化 ChatGPT Web Session AT、cookies、localStorage、fingerprint | optional `proxy_pool` |
| `openai_oauth` | 对已有账号执行 OpenAI OAuth PKCE，获取 RT | optional `sms_pool` + `proxy_pool` |
| `sub2api_sync` | 同步 ChatGPT AT + 可选 OpenAI RT / Web session 到 sub2api，并回写状态 | optional `proxy_pool` |

常用 preset：

- `full_chain`：`register → payment_link → payment → chatgpt_session → sub2api_sync`（默认完整链路）
- `register_only`：`register`
- `register_with_refresh_token`：`register → chatgpt_session → openai_oauth → sub2api_sync`
- `link_only`：`register → payment_link`
- `account_paid`：`register → payment_link → payment`
- `account_paid_with_refresh_token`：`register → payment_link → payment → chatgpt_session → openai_oauth → sub2api_sync`
- `refresh_token_only`：`chatgpt_session → openai_oauth → sub2api_sync`

## 入口和队列

文件：`backend/api/jobs.py`

- `POST /api/pipelines` 处理统一 pipeline 创建请求。
- 创建 pipeline 的请求体只包含编排字段：`count`、`preset` 或 `stages`、`stop_after`。
- 不在创建任务时传注册代理、支付代理 region、OAuth 接码、套餐等模块参数；这些由对应 WorkPool / ResourcePool 配置读取。
- `POST /api/jobs` 可直接派发任意已注册 stage。

文件：`backend/core/pipeline.py`

- `create_pipeline()` 创建声明式 pipeline。
- `Job.type == stage.name`。
- job 成功后，`_advance_pipeline()` 找下一个 stage；命中最后一步或 `stop_after` 时 pipeline 成功。
- carry-over 白名单包括：`account_id`、`payment_link_id`、`email_address`、`proxy_id`、`proxy_url`、`refresh_token_id`、`has_refresh_token`。

文件：`backend/core/queue.py`

- `StagePoolManager` 为每个 stage 维护独立 `ThreadPoolExecutor`。
- `set_concurrency(stage, n)` 只调整对应 stage 的并发。
- 非 `register` 的 account-bound stage 会先 hydrate 账号身份，避免绕过账号身份。

## 账号身份绑定

一条账号生命周期内的身份以 `chatgpt_accounts` 为准：

- `proxy_id`
- `proxy_url`
- `user_agent`
- browser fingerprint
- cookies
- local_storage

`register` 必须得到完整的 `proxy_id + proxy_url` 才会继续。`payment_link`、`chatgpt_session`、`openai_oauth`、`sub2api_sync` 都通过账号绑定身份运行。`payment` 按 payment WorkPool 配置另选支付代理。

## register stage

文件：`backend/stages/register.py`

主要步骤：

1. 从 register WorkPool 配置读取注册代理 region，并从 email/proxy 资源池领取资源。
2. 绑定完整 `proxy_id + proxy_url`。
3. 合并全局 settings 和 job `extra_config`。
4. 构造 `MicrosoftEmailService`，由邮箱池领取 Microsoft 邮箱。
5. 使用 `AccessTokenOnlyRegistrationEngine` 完成 ChatGPT 注册。
6. 从注册 session 提取 AT/session/id token，并导出 UA、fingerprint、cookies、localStorage。
7. 写入 `chatgpt_accounts`。
8. 如果 stage input 显式带 `also_record_to_at_pool=true`，额外写入 `access_token_accounts`。
9. 根据注册结果消费或退回邮箱。

`register` 不负责获取 RT；RT 获取由后续 `openai_oauth` stage 显式执行。

## chatgpt_session stage

文件：`backend/stages/chatgpt_session.py`

`chatgpt_session` 只维护 ChatGPT Web Session：

- 复用账号保存的 cookies、localStorage、fingerprint、UA、proxy。
- 请求 `chatgpt.com/api/auth/session`。
- 写入 `ChatGPTAccount.access_token`、`id_token`、`session_token`、`session_expires_at`、plan/account/user metadata。
- 不获取 OpenAI OAuth RT。

## openai_oauth stage

文件：`backend/stages/openai_oauth.py`

`openai_oauth` 面向已有 `chatgpt_accounts` 账号运行：

1. 加载账号 email/password/access identity。
2. 复用账号绑定 proxy、UA、fingerprint、cookies。
3. 创建 OpenAI OAuth PKCE authorize session。
4. 通过协议状态机跑登录/OAuth，必要时收 email OTP 或 add-phone SMS。
5. 请求 OpenAI token endpoint，获取 OAuth token 响应。
6. Upsert `openai_refresh_tokens`：
   - `refresh_token` → RT
   - `access_token` → `oauth_access_token`
   - `id_token` → `oauth_id_token`
7. 输出 `refresh_token_id` / `has_refresh_token`，不输出原始 token secret。

## sub2api 同步

文件：`backend/stages/sub2api_sync.py`、`backend/core/scheduler.py`

本项目不本地轮转 RT；sub2api 承担账号池维护和状态。本项目只做：

1. 用现有 sub2api admin import/export/status/update 接口导入或更新账号。
2. 轮询/回写账号状态到 `Sub2ApiAccountBinding` 和 `OpenAIRefreshToken`。

Payload 来源规则：

- `credentials.access_token` 只来自 `ChatGPTAccount.access_token`。
- `credentials.refresh_token` 只来自 `OpenAIRefreshToken.refresh_token`。
- `credentials.expires_at` 对应 ChatGPT session expiry。
- OAuth `oauth_access_token` 不会作为 sub2api `access_token`。

调度器每 60 秒扫描 `OpenAIRefreshToken.enabled=true` 且 `next_sync_at IS NULL OR next_sync_at <= now` 的行，入队 `sub2api_sync`，同一 `refresh_token_id` 已有 queued/running job 时跳过。

## 邮箱池链路

文件：`backend/integrations/mail/email_service.py`

当前注册邮箱服务是 `MicrosoftEmailService`。

- 不指定邮箱：从 Microsoft 邮箱池取一个 enabled 的账号。
- 指定邮箱：只取该邮箱；如果不在启用池中，注册失败。
- 获取 OTP 时读取 Microsoft OAuth `refresh_token` 和 `client_id`，这里的 `refresh_token` 是 Microsoft 邮箱 OAuth token，不是 OpenAI RT。

## 落库

### `chatgpt_accounts`

`register` / `chatgpt_session` 写入账号 Web Session 状态：

- email/password/status/account_id/workspace_id
- access_token/id_token/session_token/session_expires_at
- cookies/localStorage/browser_fingerprint/user_agent
- proxy_id/proxy_url
- plan_type/metadata_json

### `access_token_accounts`

只有 `register` stage input 显式带 `also_record_to_at_pool=true` 时写入，用于 Free AT 号池展示。

### `openai_refresh_tokens`

OpenAI RT 的本地行。RT 是否可调度以 `enabled`、`sub2api_status` 和 sub2api 同步结果为准。

## 邮箱消费和回退

注册结束后按结果处理邮箱池状态：

- 注册成功：`mark_consumed(email, note="registered")`。
- 注册失败，但 result metadata 有 `mailbox_account_consumed = true`：`mark_consumed(email, note="registered_before_failure")`。
- 注册失败且未标记 consumed：`requeue(email)`，邮箱回到 available。
