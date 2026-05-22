# 后端架构 v2（实现对齐版）

> 本文描述当前代码实现。
> 后端使用声明式 stage pipeline；Pipeline 只编排，WorkPool / ResourcePool 各自维护配置。

---

## 0. 总览

```text
Pipeline 层
  默认 full_chain 完整链路 + stop_after，可串可截，每条对应一个账号
    ↓ enqueue
WorkPool 层
  register · payment_link · payment · chatgpt_session · openai_oauth · sub2api_sync
    ↓ acquire/release
ResourcePool 层
  email_pool · card_pool · sms_pool · proxy_pool · paypal_number_pool
```

核心不变量：

- **Stage = WorkPool**：stage 名字唯一 = `Job.type`。
- **Pipeline 只编排**：只声明数量、链路和停止点，不携带模块/资源配置。
- **默认完整链路**：`register → payment_link → payment → chatgpt_session → sub2api_sync`。
- **RT 获取显式化**：只有包含 `openai_oauth` 的链路才获取 OpenAI RT。
- **身份一致性**：一个账号在账号绑定 stage 看到的 `proxy_id`、`proxy_url`、`user_agent`、`fingerprint`、`cookies/localStorage` 必须一致；`payment` 可按 region 选择不同支付代理。

---

## 1. AT / RT 语义

| 名称 | 来源 | 存储 | 用途 |
| --- | --- | --- | --- |
| AT | `chatgpt.com/api/auth/session` | `ChatGPTAccount.access_token` | sub2api `credentials.access_token` |
| RT | OpenAI OAuth PKCE token endpoint | `OpenAIRefreshToken.refresh_token` | sub2api `credentials.refresh_token` |
| OAuth access token | OpenAI OAuth token endpoint | `OpenAIRefreshToken.oauth_access_token` | 仅作为 OAuth 元数据保存 |
| ChatGPT session token | ChatGPT web session | `ChatGPTAccount.session_token` | Web session replay 材料 |

禁止把 OAuth 短期 access token 当业务 AT，也禁止把 ChatGPT session token 当 RT。

---

## 2. WorkPool（Stage）

| # | Stage | 职责 | 必需资源 | 可选资源 | 输出关键字段 |
| --- | --- | --- | --- | --- | --- |
| 1 | `register` | 注册 ChatGPT 账号；落 `chatgpt_accounts`；保存身份材料 | email | `proxy_pool` | `account_id`, `email`, `proxy_id`, `proxy_url`, `access_token_account_id` |
| 2 | `payment_link` | 生成 Team/Plus hosted 长链；落 `payment_links` | — | `proxy_pool` | `payment_link_id`, `checkout_url`, `cs_id`, `plan` |
| 3 | `payment` | PayPal guest checkout / 支付自动化 | `card_pool`, `sms_pool` | `proxy_pool` | `payment_link_id`, `state`, `payment_proxy_id`, `payment_proxy_region` |
| 4 | `chatgpt_session` | 刷新/规范化 ChatGPT Web Session AT | — | `proxy_pool` | `account_id`, `access_token`, `session_expires_at`, `plan_type` |
| 5 | `openai_oauth` | 跑 OpenAI OAuth PKCE，获取 RT | — | `sms_pool`, `proxy_pool` | `account_id`, `refresh_token_id`, `has_refresh_token`, `sub2api_status` |
| 6 | `sub2api_sync` | 同步 ChatGPT AT + 可选 OpenAI RT / Web session 到 sub2api，并回写状态 | — | `proxy_pool` | `account_id`, `refresh_token_id`, `sub2api_account_id`, `sub2api_status`, `auth_mode` |

Stage 元信息由 `backend/core/stages.py` 的 `@stage(...)` 装饰器注册，导入 `backend/stages/__init__.py` 即完成注册。

---

## 3. Pipeline 串接

`pipelines` 保存声明式结构：

```text
stages_json             # JSON array: ["register", "payment_link", ...]
stop_after              # "" = run all
stage_inputs_json       # stage-specific input
resource_bindings_json  # 保留字段；创建 pipeline API 不接收资源配置
```

推进规则：

```python
on_job_finished(job):
    p = pipeline_of(job)
    i = p.stages.index(job.type)
    if job.status != SUCCEEDED:
        p.status = job.status
        return
    if p.stop_after == job.type or i == len(p.stages) - 1:
        p.status = SUCCEEDED
        return
    next_stage = p.stages[i + 1]
    next_input = merge_carry_over(p.stage_inputs[next_stage], job.result)
    enqueue_job(type=next_stage, pipeline_id=p.id, input=next_input)
```

carry-over 字段固定：

- `account_id`
- `payment_link_id`
- `email_address`
- `proxy_id`
- `proxy_url`
- `refresh_token_id`
- `has_refresh_token`

Preset：

| Preset | stages | 用途 |
| --- | --- | --- |
| `full_chain` | `[register, payment_link, payment, chatgpt_session, sub2api_sync]` | 默认完整链路 |
| `register_only` | `[register]` | Free 号 / AT 号池 |
| `register_with_refresh_token` | `[register, chatgpt_session, openai_oauth, sub2api_sync]` | Free 号 + RT |
| `account_paid` | `[register, payment_link, payment]` | 全自动付费号 |
| `account_paid_with_refresh_token` | `[register, payment_link, payment, chatgpt_session, openai_oauth, sub2api_sync]` | 付费号 + RT |
| `link_only` | `[register, payment_link]` | 只到长链 |
| `refresh_token_only` | `[chatgpt_session, openai_oauth, sub2api_sync]` | 给已有账号补 RT |

---

## 4. 身份一致性

`chatgpt_accounts` 是账号身份的权威源：

```sql
chatgpt_accounts (
  id, email, password,
  access_token, id_token, session_token, session_expires_at,
  proxy_id, proxy_url,
  user_agent,
  browser_fingerprint_json,
  cookies_json,
  local_storage_json,
  plan_type,
  metadata_json,
  ...
)
```

规则：

- `register` 是账号身份初始写入者。
- `chatgpt_session` 刷新 ChatGPT Web Session 材料。
- `payment_link`、`chatgpt_session`、`openai_oauth`、`sub2api_sync` 使用账号绑定代理/UA/fingerprint/session。
- `payment` 使用账号身份排除账号代理，但支付代理按 payment WorkPool 配置另选。

---

## 5. OpenAI RT 与 sub2api 同步

### 5.1 本地 RT 表

```sql
openai_refresh_tokens (
  id,
  account_id                 INTEGER NOT NULL UNIQUE,
  refresh_token              TEXT NOT NULL,
  oauth_access_token         TEXT,
  oauth_id_token             TEXT,
  oauth_access_expires_at    DATETIME,
  next_sync_at               DATETIME,
  last_sync_at               DATETIME,
  consecutive_failures       INTEGER DEFAULT 0,
  enabled                    BOOLEAN DEFAULT 1,
  last_error                 TEXT,
  sub2api_account_id         TEXT,
  sub2api_status             TEXT,
  sub2api_payload_json       TEXT,
  uploaded_at                DATETIME,
  status_checked_at          DATETIME,
  created_at, updated_at
)
```

### 5.2 sub2api payload source rules

`sub2api_sync` 构建 `sub2api-data` import payload：

- `credentials.access_token` 只来自 `ChatGPTAccount.access_token`。
- `credentials.refresh_token` 只来自 `OpenAIRefreshToken.refresh_token`。
- `credentials.expires_at` 来自 `ChatGPTAccount.session_expires_at`。
- `credentials.web_session` 来自 ChatGPT cookies/session/localStorage/fingerprint/UA。
- `extra.local_refresh_token_id` 记录本地 RT 行 id。

`auth_mode`：

- `oauth_rt`：存在 OpenAI RT。
- `chatgpt_web_session`：无 RT 但有 Web session replay 材料。
- `access_token_only`：仅有 ChatGPT AT。

### 5.3 调度器

`backend/core/scheduler.py` 每 60 秒扫描 `OpenAIRefreshToken.enabled=true` 且 `next_sync_at IS NULL OR next_sync_at <= now` 的行，入队 `sub2api_sync`；同一 `refresh_token_id` 已有 queued/running `sub2api_sync` 时跳过。

---

## 6. ResourcePool

- `email_pool`：邮箱领取、OTP 轮询、邮箱状态。
- `card_pool`：付款卡资源。
- `sms_pool`：短信项目/provider 配置，供 payment / OpenAI OAuth add-phone 使用。
- `proxy_pool`：代理 URL、region、启用状态，支持账号粘性和排除账号代理。
- `paypal_number_pool`：PayPal guest checkout 手机号资源。

---

## 7. 代码组织

```text
backend/
  core/
    stages.py            # StageMeta / registry
    pipeline.py          # 声明式推进 + stop_after
    queue.py             # StagePoolManager
    scheduler.py         # OpenAI RT sub2api_sync 调度器
    job_context.py       # identity hydrate + result/log helpers
    pools/               # ResourcePool implementations
  stages/
    __init__.py
    register.py
    payment_link.py
    payment.py
    chatgpt_session.py
    openai_oauth.py
    sub2api_sync.py
  models/
    account.py
    access_token.py
    openai_refresh_token.py
    sub2api_binding.py
  schemas/
    stage_io.py
  api/
    jobs.py
    pools.py
    accounts.py
    access_tokens.py
    refresh_tokens.py
    payments.py
    ...
```

---

## 8. 已锁决策回顾

| # | 决策 |
| --- | --- |
| 1 | AT = ChatGPT Web Session access token；RT = OpenAI OAuth refresh token。 |
| 2 | 默认链路不获取 RT；RT 获取必须显式包含 `openai_oauth`。 |
| 3 | sub2api 同步使用现有 admin import/export/list/status/update 接口。 |
| 4 | sub2api `credentials.access_token` 只使用 ChatGPT AT。 |
| 5 | OAuth 短期 access token 不作为业务 AT。 |
| 6 | 一个账号生命周期内 proxy + UA + fingerprint + cookies + localStorage 固定。 |
| 7 | Pipeline creation 是编排边界，不携带各模块配置。 |
| 8 | `OpenAIRefreshToken` 是 RT 状态源；`Sub2ApiAccountBinding` 是账号级 sub2api 状态源。 |
