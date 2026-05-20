# 后端架构 v2（实现对齐版）

> 本文描述当前代码实现，不是未来计划。
> 后端已切到声明式 stage pipeline；前端已按当前后端契约做最小适配。
> 开发库允许直接清表（无须迁移）。

---

## 0. 总览

整个系统由三层组成：

```
┌────────────────────────────────────────────────────────────────┐
│                         Pipeline 层                            │
│  默认 full_chain 完整链路 + stop_after，可串可截，每条对应一个账号 │
└────────────────────────────────────────────────────────────────┘
                              │ enqueue
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                     WorkPool 层（5 个池）                       │
│  register · payment_link · payment · oauth_codex · rt_keepalive │
│  每个池独立并发数 / 队列 / 速率                                 │
└────────────────────────────────────────────────────────────────┘
                              │ acquire/release
                              ▼
┌────────────────────────────────────────────────────────────────┐
│                  ResourcePool 层（4 类资源）                    │
│  email_pool · card_pool · sms_pool(多 project) · proxy_pool     │
└────────────────────────────────────────────────────────────────┘
```

**核心不变量**：

- **Stage = WorkPool**：5 个 stage、5 个池，名字唯一 = `Job.type`。
- **Pipeline 只编排**：池由 Job.type 决定；Pipeline 只声明数量、链路和停止点，不携带模块/资源配置。
- **资源池可被多 stage 复用**：sms 池被 `payment` 和 `oauth_codex` 共用，按 `project` 路由到不同 provider。
- **默认完整链路**：未显式指定时使用 `full_chain = register → payment_link → payment → oauth_codex → rt_keepalive`。
- **任意池可截停**：Pipeline 上设 `stop_after=<stage>`，到位即视为成功，账号停在该 stage 边界。
- **任意 Job 可独立入队**：不属于任何 pipeline 也能跑。
- **🔑 身份一致性（identity binding）**：一个账号在所有账号绑定 stage 看到的 `proxy_id`、`proxy_url`、`user_agent`、`fingerprint`、`cookies/local_storage` 必须**始终一致**；`payment` 单独按 region 选择不同支付代理。详见 §4。

---

## 1. 五个 WorkPool（Stage）

| # | Stage | 职责 | 必需资源 | 可选资源 | 输出关键字段 |
| --- | --- | --- | --- | --- | --- |
| 1 | `register` | 注册 ChatGPT 账号；落 `chatgpt_accounts`；保存 `proxy_id/proxy_url/cookies/local_storage/UA/fingerprint` | `email_pool` | `proxy_pool` | `account_id`, `email`, `email_address`, `proxy_id`, `proxy_url`, `registered_account_id`, `workspace_id`, `source` |
| 2 | `payment_link` | 调 ChatGPT 后台生成 Team/Plus hosted 长链；落 `payment_links` | — | `proxy_pool` | `payment_link_id`, `checkout_url`, `cs_id`, `plan` |
| 3 | `payment` | 浏览器自动化打开长链 + 用卡 + 必要时 sms → 完成支付（**当前 v1 stub**） | `card_pool`, `sms_pool` | `proxy_pool` | `payment_link_id`, `state`, `payment_proxy_id`, `payment_proxy_region` |
| 4 | `oauth_codex` | 已注册账号跑 OpenAI OAuth PKCE，换 Codex `refresh_token` + `access_token`；上传到 sub2api RT 池 | — | `sms_pool(openai_oauth)` *仅 add_phone 时*<br>`proxy_pool` | `account_id`, `codex_token_id`, `codex_rt`, `codex_at`, `expires_in`, `sub2api_external_id`, `sub2api_status` |
| 5 | `rt_keepalive` | 同步 sub2api RT 池状态；必要时重试上传 pending RT；**不在本项目轮转 RT** | — | `proxy_pool` | `account_id`, `codex_token_id`, `sub2api_status`, `sub2api_external_id` |

**Stage 元信息（单一来源）**：

```python
@stage(
    name="register",
    default_concurrency=3,
    requires_resources=["email_pool"],
    optional_resources=["proxy_pool"],
    rate_limit_per_min=None,
    retry_policy=RetryPolicy(max_attempts=1),
    input_schema=RegisterInput,    # Pydantic
    output_schema=RegisterOutput,
)
def run_register(ctx: JobContext) -> None: ...
```

每个 stage 一个文件：`backend/stages/<name>.py`。导入即注册。`requires_resources` / `optional_resources` 是 stage 契约元信息；当前 handler 中，`register` 的邮箱领取仍由 `MicrosoftEmailService` 调用 `backend.integrations.mail.pool` 完成，`payment` v1 stub 只实际 acquire 支付代理并跳过 card/sms，`oauth_codex` 的 add-phone 接码仍走 settings 驱动的 `PhoneProvider`。

---

## 2. 资源池（4 类）

### 2.1 统一抽象

```python
class ResourcePool(Protocol):
    name: str
    def acquire(self, *, stage: str, job_id: int,
                project: str | None = None,
                hint: dict | None = None) -> Resource | None: ...
    def release(self, resource: Resource, *,
                outcome: Literal["consumed","reusable","failed","banned"],
                reason: str = "") -> None: ...
    def stats(self) -> dict: ...
```

- 全部注册到 `RESOURCE_REGISTRY`。
- `JobContext.acquire(name, project=...)` 自动登记到 ctx，job 终结时按 outcome 自动释放（异常时回滚为 `reusable` 或按 stage 指定）。

### 2.2 EmailPool

- **复用现有 `email_accounts` 表**。
- 领取状态由 `backend.integrations.mail.pool` 维护在 `EmailAccount.metadata_json["pool_status"]`：`available|claimed|consumed|blacklist`。
- `enabled` 仍是“当前可领取”的源字段：available 为 true，claimed/consumed/blacklist 为 false。
- 仅 `register` stage 使用。
- 保留 alias_split 行为不变。

### 2.3 CardPool（**新表**）

```sql
payment_cards (
  id, number, exp_month, exp_year, cvv, holder_name,
  billing_country, billing_postal,
  status,            -- available | in_use | used | failed | banned
  bind_count,        -- 累计被领次数
  last_used_at, last_error,
  bound_job_id, note,
  created_at, updated_at
)
```

- 仅 `payment` stage 使用。
- Acquire：`status=available` + `bind_count < N`，FIFO。
- Release outcome：
  - 支付成功 → `consumed`（status=used）
  - 卡被拒 → `failed`，`bind_count++`；超阈值标 `banned`
  - 流程异常但卡未被绑 → `reusable`

### 2.4 SmsPool（**多 project 路由**）

接码池对外是 1 个 `SmsPool` 实例，内部按 `project` 路由到不同 provider 适配器。

```sql
sms_projects (
  id, name,            -- 业务标识，如 "stripe_payment" / "openai_oauth"
  provider,            -- 适配器：smstome / sms-activate / ...
  config_json,         -- api_key / endpoint / country / service code
  enabled,
  created_at, updated_at
)
```

- `payment` stage 元信息声明需要 `sms_pool`，但当前 v1 stub 不实际取号。
- `oauth_codex` stage 元信息声明可选 `sms_pool`，但当前 add-phone 接码实际使用 `backend.integrations.chatgpt.phone_service` 的 settings 驱动 provider（smsbower/fivesim/smsgiare）。
- `sms_pool` / `sms_projects` 当前是资源池框架和 CRUD；已放置 smstome 适配器骨架，新 provider 走 `SmsProvider` 接口。

### 2.5 ProxyPool

- 实现在 `backend/core/pools/proxy_pool.py`，包装 `proxies` 表并实现 `acquire/release`。
- 支持 `region`、`proxy_id`、`exclude_proxy_id`、`exclude_url` hint。
- 关键：注册/长链/OAuth/RT sync 使用账号绑定代理；payment 按指定 region 选择不同代理。详见 §4。

---

## 3. Pipeline 串接

### 3.1 数据结构

`pipelines` 当前把声明式结构拆成独立列：

```text
stages_json             # JSON array: ["register", "payment_link", ...]
stop_after              # "" = run all
stage_inputs_json       # 保留内部字段；创建 pipeline API 不接收模块配置
resource_bindings_json  # 保留内部字段；创建 pipeline API 不接收资源配置
input_json              # create_pipeline 可保存原始 payload；当前 POST /api/pipelines 未传时为 {}
```

示例请求：

```json
{
  "preset": "full_chain",
  "stop_after": "payment_link",
  "count": 1
}
```

### 3.2 推进规则（无 if/elif）

```
on_job_finished(job):
    p = pipeline_of(job)
    i = p.stages.index(job.type)
    if job.status != SUCCEEDED:
        p.status = job.status; return
    if p.stop_after == job.type or i == len(p.stages) - 1:
        p.status = SUCCEEDED; return
    next_stage = p.stages[i + 1]
    next_input = merge_carry_over(p.stage_inputs[next_stage], job.result)
    enqueue_job(type=next_stage, pipeline_id=p.id,
                account_id=p.account_id, payment_link_id=p.payment_link_id,
                proxy_id=p.proxy_id, proxy_url=p.proxy_url, input=next_input)
```

**carry-over 字段固定**（任何 stage 输出的这些字段，都自动透传到下游 stage 的 input）：

- `account_id`
- `payment_link_id`
- `email_address`
- `proxy_id`
- `proxy_url`（账号绑定后不会变，见 §4）
- `codex_rt` / `codex_at`

### 3.3 Preset（API 语法糖）

| Preset | stages | 用途 |
| --- | --- | --- |
| `full_chain` | `[register, payment_link, payment, oauth_codex, rt_keepalive]` | 默认完整链路，可配 `stop_after` 停在任一模块 |
| `register_only` | `[register]` | Free 号 / AT 号池 |
| `register_with_codex_rt` | `[register, oauth_codex]` | Free 号 + Codex RT |
| `account_paid` | `[register, payment_link, payment]` | 全自动付费号 |
| `account_paid_with_codex_rt` | `[register, payment_link, payment, oauth_codex]` | 付费号 + RT |
| `link_only` | `[register, payment_link]` | 只到长链（人工付） |
| `codex_rt_only` | `[oauth_codex]` | 给已有账号补 RT |

API：

- `POST /api/pipelines` body 可传 `count`、`preset` **或** 自定义 `stages`、`stop_after`；不传链路时默认 `full_chain`。创建 API 禁止额外模块配置字段。
- 旧 `/api/pipelines/chatgpt-account` 与 `/api/pipelines/chatgpt-register-only` 已删除；只保留声明式入口。

---

## 4. 身份一致性（identity binding）

> 一个账号生命周期内的 `proxy_id`、`proxy_url`、`user_agent`、`fingerprint`、`cookies/local_storage` 必须始终一致。
> 一旦在 `register` 时确定，`payment_link`、`oauth_codex`、`rt_keepalive` 都用同一份；`payment` 另按 region 选择不同支付代理。

### 4.1 存储位置

`chatgpt_accounts` 表是身份的**唯一权威源**：

```sql
chatgpt_accounts (
  id, email, password,
  access_token, session_token,
  -- identity bundle ---
  proxy_id                  INTEGER,
  proxy_url                 TEXT NOT NULL DEFAULT '',
  user_agent                TEXT NOT NULL DEFAULT '',
  browser_fingerprint_json  TEXT NOT NULL DEFAULT '{}',
  cookies_json              TEXT NOT NULL DEFAULT '[]',
  local_storage_json        TEXT NOT NULL DEFAULT '{}',
  -- 其他业务字段 ...
)
```

### 4.2 写入与读取规则

- **register stage** 是账号身份的唯一**写入者**：成功/失败落库时把 `proxy_id/proxy_url/UA/fingerprint/cookies/local_storage` 一并写入 `chatgpt_accounts`。
- `register` 必须拿到完整 `proxy_id + proxy_url`，否则 job 失败。
- 后续账号绑定 stage（payment_link / oauth_codex / rt_keepalive）启动前在 `JobContext` 里**强制从 `chatgpt_accounts` 重新 hydrate**身份，不从 Pipeline.input 取。
- `payment` 也会 hydrate 账号身份用于排除账号代理，但实际支付代理 region 来自 `workpool.payment.proxy_region`，再从 `proxy_pool` 另选。
- 创建 pipeline 不传 `proxy_id/proxy_url`；注册代理 region 来自 register WorkPool 配置，账号绑定后以 `chatgpt_accounts` 为准。
- ProxyPool 支持账号粘性：`proxy_pool.acquire(hint={"account_id": ...})` 在未指定 region/proxy_id 时返回账号绑定 proxy。

### 4.3 失败处理

- 如果某次 stage 因身份失效（cookies 过期 / proxy IP 拉黑）失败，**不要换 proxy 重试**：失败即回退到 `oauth_codex` 重新拿 RT，或人工介入。
- 换 proxy = 重做账号；这是契约。

---

## 5. RT 池（sub2api-backed）

> RT 池最终由 **sub2api** 承担：
> - `oauth_codex` 是 RT 生产者：拿到 Codex `refresh_token` 后上传给 sub2api。
> - `rt_keepalive` 只是本项目里的同步 stage：重试上传 pending RT，或拉取 sub2api 远端状态。
> - RT 轮转、保活、失效判断都交给 sub2api；本项目不再直接调用 OpenAI token endpoint 刷 AT。
> - 如果 sub2api 标记 RT 失效，可重新触发 `oauth_codex` 重产并再次上传。

### 5.1 表（本地镜像）

```sql
codex_tokens (
  id,
  account_id              INTEGER NOT NULL UNIQUE,   -- 1:1 chatgpt_accounts
  refresh_token           TEXT NOT NULL,
  access_token            TEXT,
  id_token                TEXT,
  expires_at              DATETIME,
  next_refresh_at         DATETIME,                  -- 下一次 sub2api 状态同步/上传重试
  consecutive_failures    INTEGER DEFAULT 0,
  alive                   BOOLEAN DEFAULT 1,
  last_refreshed_at       DATETIME,
  last_error              TEXT,
  sub2api_external_id     TEXT,
  sub2api_status          TEXT,
  sub2api_payload_json    TEXT,
  uploaded_at             DATETIME,
  status_checked_at       DATETIME,
  created_at, updated_at
)
```

### 5.2 sub2api adapter

配置项：

```text
sub2api_base_url
sub2api_api_key
sub2api_upload_path    # 默认 /api/codex-tokens
sub2api_status_path    # 默认 /api/codex-tokens/{external_id}
sub2api_timeout_seconds
```

上传 payload：

```json
{
  "account_id": 123,
  "refresh_token": "...",
  "access_token": "...",
  "id_token": "...",
  "expires_at": "...",
  "proxy_url": "...",
  "metadata": {"local_codex_token_id": 1}
}
```

### 5.3 调度器

`backend/core/scheduler.py` 每 60s 扫描 `alive=true` 且 `next_refresh_at IS NULL OR next_refresh_at <= now` 的行，入队 `rt_keepalive`；同一 token 已有 queued/running `rt_keepalive` 时跳过。

`rt_keepalive` 行为：

- 没有 `sub2api_external_id` 或状态为 `pending_upload/upload_failed/sync_failed`：调用 sub2api upload。
- 已有 `sub2api_external_id`：调用 sub2api status endpoint。
- 成功：更新 `sub2api_status/sub2api_payload_json/status_checked_at`，下一次同步默认 24h 后。
- sub2api 未配置：job 视为成功，行保持 `pending_upload`，便于配置后重试。
- sub2api 请求失败：`consecutive_failures++`，1h 后重试。

---

## 6. JobContext 扩展

```python
@dataclass
class JobContext:
    job_id: int
    pipeline_id: int | None
    stage: str
    account_id: int | None
    payment_link_id: int | None
    input: dict
    # 自动 hydrate 的身份 bundle（来自 chatgpt_accounts）
    identity: AccountIdentity | None   # proxy_url / ua / fingerprint / cookies / local_storage
    _acquired: list[AcquiredResource]

    def acquire(self, pool: str, *, project: str | None = None,
                hint: dict | None = None) -> Resource: ...
    def release(self, resource: Resource, *,
                outcome: str, reason: str = "") -> None: ...
    def update_result(self, fields: dict) -> None: ...   # merge into result_json
    def emit_result(self, **fields) -> None: ...          # thin wrapper around update_result
    def check_cancelled(self) -> None: ...
    def log(self, msg: str, level: str = "info", **payload) -> None: ...
```

stage 函数永远是 `def run(ctx: JobContext) -> None:`。

---

## 7. 表结构变更清单（清表重建）

| 表 | 操作 |
| --- | --- |
| `pipelines` | 当前字段：`preset/status/stages_json/stop_after/stage_inputs_json/resource_bindings_json/current_stage/total_steps/completed_steps/account_id/payment_link_id/proxy_id/proxy_url/input_json/result_json/error/cancel_requested/...` |
| `jobs` | `Job.type` 含义为 stage name；含 `(type, status)` 索引；新增 `proxy_id/proxy_url` 用于队列期传递 |
| `chatgpt_accounts` | 身份字段为 `proxy_id/proxy_url/user_agent/browser_fingerprint_json/cookies_json/local_storage_json` |
| `email_accounts` | 复用现有字段；池状态写在 `metadata_json["pool_status"]`，`enabled` 表示当前可领取 |
| `payment_cards` | **新表**，见 §2.3 |
| `sms_projects` | **新表**，见 §2.4 |
| `codex_tokens` | **新表**，见 §5.1 |
| `access_token_accounts` | 保留作为"AT 号池"展示；不再混用 RT（RT 全部移到 `codex_tokens`） |

允许 `dev.db` 直接清表（用户授权）。

---

## 8. 代码组织

```
backend/
  core/
    stages.py            # @stage 装饰器, StageMeta, STAGE_REGISTRY
    pipeline.py          # 重写：声明式推进 + stop_after
    queue.py             # 重写：StagePoolManager（每 stage 独立 ThreadPoolExecutor）
    scheduler.py         # sub2api RT 状态同步/上传重试调度器
    job_context.py       # 加 acquire/release + identity hydrate
    pools/
      __init__.py        # RESOURCE_REGISTRY
      base.py            # ResourcePool Protocol + Resource dataclass + AcquireOutcome
      email_pool.py      # 包装 email_accounts
      card_pool.py       # 新写
      sms_pool.py        # 多 project 路由 + provider 适配器接口
      sms_providers/
        smstome.py       # smstome provider adapter
      proxy_pool.py      # 沿用 + acquire/release（账号粘性）
  stages/
    __init__.py          # import all
    register.py          # was flows/chatgpt_register.py
    payment_link.py      # was flows/chatgpt_payment_link.py
    payment.py           # **新写（v1 留 stub）**
    oauth_codex.py       # was flows/chatgpt_refresh_token.py 改造
    rt_keepalive.py      # 同步 sub2api RT 池状态；不做本地 RT 轮转
  schemas/
    stage_io.py          # 每 stage 的 input/output Pydantic schema
  api/
    jobs.py              # POST /api/pipelines、POST /api/jobs、SSE、queue stats
    pools.py             # GET /api/pools、GET /api/stages
    cards.py             # CRUD payment_cards
    sms.py               # CRUD sms_projects
    codex_tokens.py      # codex_tokens 本地镜像 + sub2api sync
    accounts.py          # 账号池、订阅池、手动补 RT、长链重试
    payments.py          # payment_links 与手动 payment job
```

已清理旧 flow 架构：`backend/flows/`、`backend/core/flow_registry.py`、`PIPELINE_STEP_*` / `PIPELINE_TYPE_*` 常量均不再作为后端运行时入口。

---

## 9. 团队（Subagent 分工）

> 我作为协调者（**Coordinator**），把工作拆给 4 个 Subagent。
> 每次落地一阶段（P1/P2/P3/P4）按下表派单。

| 角色 | 代号 | 职责 |
| --- | --- | --- |
| **Architect** | `arch` | 维护本文档、stage/pool 接口签名、carry-over 契约；任何接口变更必须先改文档。 |
| **CoreDev** | `core` | `core/stages.py` / `core/queue.py` / `core/pipeline.py` / `core/scheduler.py` / `core/job_context.py` / `core/pools/` 全部由其实现。 |
| **StageDev** | `stage` | `backend/stages/*.py` 与各 stage 的 schema；包括迁移现有 flow → stage、新写 payment stub、新写 sub2api RT sync、改造 oauth_codex。 |
| **APIDev** | `api` | `backend/api/*.py` 与请求 schema；模型层（`models/`）的字段补齐。 |
| **QA** | `qa` | 写最小回归（pytest / 手动 curl 脚本）：注册 stage 单跑、pipeline stop_after、sub2api RT sync、资源池 acquire/release。 |

### 9.1 协作规则（硬约束）

- **接口先于实现**：CoreDev 改 `JobContext` / `ResourcePool` / `StageMeta` 必须先经 Architect 改文档。
- **单写者原则**：每个文件只能由一个 owner 编辑；交叉改动需 Coordinator 派对方做。
- **Job.type 即 stage.name**：跨层提到的字符串都来自 `core.stages.STAGE_REGISTRY`，不允许字面量散落。
- **carry-over 只走白名单**（§3.2）：新增 carry-over 字段必须改文档。
- **资源释放必须在 finally**：CoreDev 在 `JobContext` 里实现自动 release，stage 代码只声明 outcome。
- **每阶段交付前自检**：QA 跑通对应阶段的最小用例，否则不算 done。

### 9.2 阶段派单

| Phase | 责任人 | 交付 |
| --- | --- | --- |
| **P1 - 框架骨架** | core + arch | 已落地：`stages.py` / `pools/base.py` / `queue.py` StagePoolManager / `pipeline.py` 声明式推进 / `JobContext` 扩展。 |
| **P2 - 资源池** | core + api | 已落地：`email_pool` / `card_pool` + CRUD / `sms_pool` + smstome 适配器 / `proxy_pool` 账号粘性与 region 选择。 |
| **P3 - stage 迁移与新建** | stage | 已落地：`register/payment_link/oauth_codex` stage；`payment` stub；`rt_keepalive` sub2api sync；`scheduler.py`；`codex_tokens` 镜像与 API。 |
| **P4 - 清理** | core + api | 已落地：旧 flow/flow_registry/helper queued stage 清理；前端兼容文案清理；保留声明式 pipeline API。 |
| **验收** | qa | 当前通过：后端编译、stage registry smoke、前端 lint/build、HTTP smoke。 |

---

## 10. 已锁决策回顾（避免反复）

| # | 决策 |
| --- | --- |
| 1 | RT 池由 sub2api 承担；oauth_codex 是 RT 生产者；rt_keepalive 只做 sub2api 上传/状态同步；RT 失效可重新过 oauth |
| 2 | 一账号生命周期内 proxy + UA + fingerprint + cookies + local_storage 全部固定 |
| 3 | email_accounts 复用；领取状态写入 `metadata_json["pool_status"]`，不新增状态列 |
| 4 | payment_cards 新表 |
| 5 | payment stage v1 留 stub（浏览器自动化方向，框架先就位） |
| 6 | oauth_codex 仅在触发 add_phone 时 acquire sms（标记为可选资源） |
| 7 | 本项目每 24h 拉取一次 sub2api RT 状态；RT 轮转由 sub2api 维护 |
| 8 | 数据库直接清表，无迁移 |
| 9 | 每 stage 输入输出 Pydantic schema（在 `backend/schemas/stage_io.py` 集中） |
| 10 | sub2api endpoint/path/token 通过 settings/env 配置；本项目不硬编码 sub2api 部署地址 |
