# chatgpt-queue-reg

队列式 ChatGPT 注册 + Team/Plus 支付长链 + ChatGPT AT / OpenAI RT 同步 + 微软邮箱（含裂变）+ Camoufox / HAR 调试项目。

## 模块边界

- **后端 (`backend/`)**：FastAPI + SQLModel/SQLite，按 stage 拆分的多 worker pool。
- **前端 (`frontend/`)**：React 19 + Vite + Ant Design，统一 dark theme。
- **集成 (`backend/integrations/`)**：ChatGPT 注册 / 长链生成 / OpenAI OAuth、Microsoft Graph 邮箱、sub2api 账号同步。

## AT / RT 语义

- **AT**：ChatGPT Web Session access token，来自 `chatgpt.com/api/auth/session`，存储在 `ChatGPTAccount.access_token`。
- **RT**：OpenAI OAuth refresh token，来自 OAuth PKCE 流程，存储在 `OpenAIRefreshToken.refresh_token`。
- OAuth 返回的短期 access token 只作为 `oauth_access_token` 元数据保存，不作为业务 AT，也不会传给 sub2api 的 `credentials.access_token`。
- `ChatGPTAccount.session_token` 是 ChatGPT Web session token，不是 RT。

## Pipeline

后端统一走声明式 pipeline：`POST /api/pipelines` 默认使用 `full_chain` 完整链路，也可传 `preset` 或显式 `stages`；`stop_after` 可让账号停在任意 stage 边界。

当前 WorkPool stage：

1. `register`：注册账号，绑定账号身份（proxy_id/proxy_url/UA/fingerprint/cookies/local_storage）。
2. `payment_link`：复用账号身份生成 Team/Plus hosted 长链，写入 `payment_links`。
3. `payment`：PayPal guest checkout / 支付自动化。
4. `chatgpt_session`：刷新并规范化 ChatGPT Web Session，维护 ChatGPT AT/session 材料。
5. `openai_oauth`：显式获取 OpenAI OAuth RT，写入 `openai_refresh_tokens`。
6. `sub2api_sync`：把 ChatGPT AT + 可选 OpenAI RT / Web session 材料同步到 sub2api，并回写状态。

默认完整链路为：

```text
register → payment_link → payment → chatgpt_session → sub2api_sync
```

RT 获取是显式链路，可用 `openai_oauth` 插入到 `sub2api_sync` 前。

## 核心 API

- `POST /api/pipelines` 创建声明式 pipeline；`GET /api/pipelines` / `GET /api/pipelines/{id}` 查询。
- `GET /api/jobs` / `GET /api/jobs/{id}/events/stream` 查看 job 与 SSE 日志。
- `GET /api/pools` / `GET /api/stages` / `GET /api/queue/stats` 查看 stage/resource pool 状态。
- `GET /api/accounts` / `GET /api/accounts/subscriptions` / `POST /api/accounts/{id}/refresh-token|sub2api-sync|read-email|debug-browser|payment-link/retry`。
- `GET /api/payment-links` / `POST /api/payment-links/{id}/payment|debug-browser`。
- `GET /api/access-tokens` / `POST /api/access-tokens/{id}/refresh-token` / `GET /api/access-tokens/export`。
- `GET /api/refresh-tokens` / `POST /api/refresh-tokens/{id}/sync` / `PATCH /api/refresh-tokens/{id}/toggle` / `DELETE /api/refresh-tokens/{id}`。
- `POST /api/email/import` / `POST /api/email/read`。
- `GET/POST/PATCH/DELETE /api/proxies`、`/api/cards`、`/api/sms/projects` 管理资源池数据。
- `GET/PUT /api/settings` 按 WorkPool / ResourcePool 维护配置列表，任务创建不携带模块配置。
- `POST /api/browser-debug/open` 调起 Camoufox/Chromium，注入 cookies/UA/localStorage 并抓 HAR。
- `GET /api/healthz`。

## 开发

后端：

```bash
cd chatgpt-queue-reg
pip install -r requirements.txt
DATABASE_URL=sqlite:///dev.db python -m uvicorn backend.main:app --reload --port 8000
```

前端：

```bash
cd frontend
pnpm install
pnpm dev
```

构建产物会落到 `static/`，后端会把它当 SPA 静态资源挂到根目录。

## 数据库

启动时自动 `init_db()`，使用的表：

- `pipelines` / `jobs` / `job_events`
- `chatgpt_accounts` / `access_token_accounts` / `openai_refresh_tokens` / `sub2api_account_bindings`
- `payment_links` / `payment_cards` / `paypal_numbers`
- `email_accounts` / `email_messages`
- `proxies` / `browser_debug_sessions`
- `sms_projects` / `settings`

SQLite 上自动开 WAL + busy_timeout。旧 RT 表会在启动迁移到 `openai_refresh_tokens`。

## 调试浏览器

Pipeline、账号、支付长链页面都可以手动调起 Camoufox/HAR；pipeline 停在某个 stage 后不会自动弹窗。

`open_debug_session(...)` 会把账号保存的 cookies、localStorage、UA、fingerprint 和代理注入浏览器，HAR 输出到 `logs/har/debug-{ts}.har`。

## 邮箱裂变（Plus 别名）

`POST /api/email/import` 设置 `alias_split_enabled=true` + `alias_split_count` 即可。每条原始记录会在 `email_accounts` 中扩展为 N 条带随机后缀的 `+xxxxxx@domain` 邮箱（OAuth 信息共享），包含/不包含原始邮箱可控。
