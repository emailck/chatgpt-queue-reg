# chatgpt-queue-reg

队列式 ChatGPT 注册 + Team/Plus 支付长链 + Codex RT 同步 + 微软邮箱（含裂变）+ 随处调起 Camoufox / HAR 的精简版项目。

## 模块边界

- **后端 (`backend/`)**：FastAPI + SQLModel/SQLite，按 stage 拆分的多 worker pool。
- **前端 (`frontend/`)**：React 19 + Vite + Ant Design，统一 dark theme。
- **集成 (`backend/integrations/`)**：ChatGPT 注册 / 长链生成 / OAuth、Microsoft Graph 邮箱、sub2api RT 池同步。

## Pipeline

后端统一走声明式 pipeline：`POST /api/pipelines` 默认使用 `full_chain` 完整链路，也可传 `preset` 或显式 `stages`；`stop_after` 可让账号停在任意 stage 边界。

当前只有 5 个 WorkPool stage：

1. `register`：注册账号，绑定账号身份（proxy_id/proxy_url/UA/fingerprint/cookies/local_storage）。
2. `payment_link`：复用账号身份生成 Team/Plus hosted 长链，写入 `payment_links`。
3. `payment`：支付浏览器自动化框架位，当前 v1 stub；支付代理必须按 region 选择且不同于账号代理。
4. `oauth_codex`：对已注册账号执行 OpenAI OAuth PKCE，获取 Codex RT/AT 并上传 sub2api。
5. `rt_keepalive`：同步本地 `codex_tokens` 镜像与 sub2api 状态；RT 轮转由 sub2api 负责。

任何一步失败/取消，pipeline 整体标记失败并停止推进。默认完整链路为 `register → payment_link → payment → oauth_codex → rt_keepalive`；例如 `stop_after=payment_link` 会生成账号和长链后停住。创建 pipeline 只表达数量、链路和停止点，注册代理、支付代理 region、OAuth 接码等由各池配置决定。

## 核心 API

- `POST /api/pipelines` 创建声明式 pipeline；`GET /api/pipelines` / `GET /api/pipelines/{id}` 查询。
- `GET /api/jobs` / `GET /api/jobs/{id}/events/stream` 查看 job 与 SSE 日志。
- `GET /api/pools` / `GET /api/stages` / `GET /api/queue/stats` 查看 stage/resource pool 状态。
- `GET /api/accounts` / `GET /api/accounts/subscriptions` / `POST /api/accounts/{id}/refresh-token|read-email|debug-browser|payment-link/retry`。
- `GET /api/payment-links` / `POST /api/payment-links/{id}/payment|debug-browser`；payment 参数从 payment WorkPool 配置读取。
- `GET /api/access-tokens` / `POST /api/access-tokens/{id}/refresh-token` / `GET /api/codex-tokens` / `POST /api/codex-tokens/{id}/sync`。
- `POST /api/email/import` (支持 `alias_split_enabled` 邮箱裂变 + 含原邮箱) / `POST /api/email/read`。
- `GET/POST/PATCH/DELETE /api/proxies`、`/api/cards`、`/api/sms/projects` 管理资源池数据。
- `GET/PUT /api/settings` 按 WorkPool / ResourcePool 维护配置列表，任务创建不携带模块配置。
- `POST /api/browser-debug/open` 任何上下文调起 Camoufox/Chromium，注入 cookies/UA/localStorage 并抓 HAR。
- `GET /api/healthz`。

## 开发

后端（建议用原项目同款 conda 环境 `any-auto-register`）：

```bash
cd chatgpt-queue-reg
pip install -r requirements.txt   # 已经装过同款环境则跳过
DATABASE_URL=sqlite:///dev.db python -m uvicorn backend.main:app --reload --port 8000
```

前端：

```bash
cd frontend
pnpm install
pnpm dev   # localhost:5173 -> 代理到 8000
```

构建产物会落到 `static/`，后端会把它当 SPA 静态资源挂到根目录。

## 数据库

启动时自动 `init_db()`，使用的表：

- `pipelines` / `jobs` / `job_events`
- `chatgpt_accounts` / `access_token_accounts` / `payment_links`
- `email_accounts` / `email_messages`
- `proxies` / `browser_debug_sessions`
- `payment_cards` / `sms_projects` / `codex_tokens`
- `settings`

非 SQLite 的 DB 也行，把 `DATABASE_URL` 指过去即可（SQLite 上自动开 WAL + busy_timeout）。

## 调试浏览器

Pipeline、账号、支付长链页面都可以手动调起 Camoufox/HAR；pipeline 停在某个 stage 后不会自动弹窗。

`open_debug_session(...)` 会把：

- 账号注册时保存的 `cookies_json` 注入 chatgpt.com
- `local_storage_json` 通过 init_script 写入 window.localStorage
- 账号注册时的 UA / fingerprint
- 任意覆盖代理
- HAR 输出到 `logs/har/debug-{ts}.har`

并把 browser/context/page/playwright/camoufox 句柄放进进程内 registry，窗口不会被回收。需要关闭：`POST /api/browser-debug/sessions/{id}/close`。

## 邮箱裂变（Plus 别名）

`POST /api/email/import` 设置 `alias_split_enabled=true` + `alias_split_count` 即可。每条原始记录会在 `email_accounts` 中扩展为 N 条带随机后缀的 `+xxxxxx@domain` 邮箱（OAuth 信息共享），包含/不包含原始邮箱可控。
