# ChatGPT Queue Reg

队列式 ChatGPT 注册、支付链路、Token 同步和资源池管理项目。项目包含 FastAPI 后端、React 管理面板、声明式 Pipeline、Job 队列、多类资源池，以及 ChatGPT AT / OpenAI RT / sub2api 同步等能力。

## 交流

QQ 交流群：1094941151

## 重要声明

本项目仍处于开发和实验阶段，功能并不完善，稳定性、兼容性、错误处理和成功率都可能存在不足。请仅在你拥有明确授权的本地、测试、CTF、研究或防御环境中使用。

不得将本项目用于未授权注册、批量滥用、攻击、绕过平台规则、骚扰、欺诈或其他违法违规用途。使用者需要自行承担配置、运行和使用行为带来的全部责任。

## 模块边界

- **后端 (`backend/`)**：FastAPI + SQLModel/SQLite，负责 API、数据库、队列、Stage 调度、资源池和第三方集成。
- **前端 (`frontend/`)**：React 19 + Vite + Ant Design，提供账号、Job、Pipeline、资源池、设置和调试页面。
- **静态资源 (`static/`)**：前端构建产物，后端会作为 SPA 静态资源托管。
- **集成 (`backend/integrations/`)**：ChatGPT 注册/支付/OpenAI OAuth、Microsoft 邮箱、sub2api、PayPal 等协议和浏览器链路。

## 功能概览

- Pipeline / Job 队列调度、并发控制、日志事件流和失败重试
- 邮箱、代理、短信、支付卡、PayPal 号码等资源池管理
- ChatGPT 注册流程编排，支持协议链路和浏览器调试链路
- Payment Link、PayPal guest checkout / 支付自动化相关链路
- ChatGPT Web Session access token 管理
- OpenAI OAuth refresh token 获取、保存和同步
- sub2api 账号绑定与同步
- Camoufox / Chromium 调试浏览器与 HAR 抓取
- 前端管理面板和 WorkPool / ResourcePool 配置

## AT / RT 语义

- **AT**：ChatGPT Web Session access token，来自 `chatgpt.com/api/auth/session`，存储在 `ChatGPTAccount.access_token`。
- **RT**：OpenAI OAuth refresh token，来自 OAuth PKCE 流程，存储在 `OpenAIRefreshToken.refresh_token`。
- OAuth 返回的短期 access token 只作为 `oauth_access_token` 元数据保存，不作为业务 AT，也不会传给 sub2api 的 `credentials.access_token`。
- `ChatGPTAccount.session_token` 是 ChatGPT Web session token，不是 RT。

## Pipeline

后端统一走声明式 Pipeline：`POST /api/pipelines` 默认使用 `full_chain` 完整链路，也可传 `preset` 或显式 `stages`；`stop_after` 可让账号停在任意 Stage 边界。

当前 WorkPool Stage：

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

## 技术栈

- 后端：Python、FastAPI、SQLModel、Uvicorn
- 前端：React、TypeScript、Vite、Ant Design
- 浏览器自动化：Playwright / Patchright / Camoufox
- HTTP 客户端：curl_cffi / requests / httpx

## 快速开始

### 1. 安装后端依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell 可使用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 安装前端依赖

```bash
pnpm --dir frontend install
```

### 3. 启动后端

```bash
python -m backend.main
```

默认监听 `0.0.0.0:8000`，可通过环境变量覆盖：

```bash
HOST=127.0.0.1 PORT=8000 python -m backend.main
```

开发时也可以使用 Uvicorn reload：

```bash
python -m uvicorn backend.main:app --reload --port 8000
```

### 4. 启动前端开发服务

```bash
pnpm --dir frontend dev
```

### 5. 构建前端静态资源

```bash
pnpm --dir frontend build
```

构建产物会落到 `static/`，后端会把它当 SPA 静态资源挂到根目录。

## 配置说明

项目中的邮箱、代理、短信、支付、PayPal 号码、sub2api 等能力需要在前端管理面板或对应 API 中配置资源池后才能正常运行。不同链路依赖的外部服务、代理质量、验证码接收、浏览器运行环境都会影响成功率。

## 核心 API

- `POST /api/pipelines` 创建声明式 Pipeline；`GET /api/pipelines` / `GET /api/pipelines/{id}` 查询。
- `GET /api/jobs` / `GET /api/jobs/{id}/events/stream` 查看 Job 与 SSE 日志。
- `GET /api/pools` / `GET /api/stages` / `GET /api/queue/stats` 查看 Stage / ResourcePool 状态。
- `GET /api/accounts` / `GET /api/accounts/subscriptions` / `POST /api/accounts/{id}/refresh-token|sub2api-sync|read-email|debug-browser|payment-link/retry`。
- `GET /api/payment-links` / `POST /api/payment-links/{id}/payment|debug-browser`。
- `GET /api/access-tokens` / `POST /api/access-tokens/{id}/refresh-token` / `GET /api/access-tokens/export`。
- `GET /api/refresh-tokens` / `POST /api/refresh-tokens/{id}/sync` / `PATCH /api/refresh-tokens/{id}/toggle` / `DELETE /api/refresh-tokens/{id}`。
- `POST /api/email/import` / `POST /api/email/read`。
- `GET/POST/PATCH/DELETE /api/proxies`、`/api/cards`、`/api/sms/projects` 管理资源池数据。
- `GET/PUT /api/settings` 按 WorkPool / ResourcePool 维护配置列表，任务创建不携带模块配置。
- `POST /api/browser-debug/open` 调起 Camoufox/Chromium，注入 cookies/UA/localStorage 并抓 HAR。
- `GET /api/healthz` 健康检查。

## 数据库

启动时自动 `init_db()`，主要表包括：

- `pipelines` / `jobs` / `job_events`
- `chatgpt_accounts` / `access_token_accounts` / `openai_refresh_tokens` / `sub2api_account_bindings`
- `payment_links` / `payment_cards` / `paypal_numbers`
- `email_accounts` / `email_messages`
- `proxies` / `browser_debug_sessions`
- `sms_projects` / `settings`

SQLite 上自动开 WAL + busy_timeout。旧 RT 表会在启动时迁移到 `openai_refresh_tokens`。

## 调试浏览器

Pipeline、账号、支付长链页面都可以手动调起 Camoufox/HAR；Pipeline 停在某个 Stage 后不会自动弹窗。

`open_debug_session(...)` 会把账号保存的 cookies、localStorage、UA、fingerprint 和代理注入浏览器，HAR 输出到 `logs/har/debug-{ts}.har`。

## 邮箱裂变（Plus 别名）

`POST /api/email/import` 设置 `alias_split_enabled=true` + `alias_split_count` 即可。每条原始记录会在 `email_accounts` 中扩展为 N 条带随机后缀的 `+xxxxxx@domain` 邮箱（OAuth 信息共享），包含/不包含原始邮箱可控。

## 常用检查

```bash
python -m compileall backend
pnpm --dir frontend lint
pnpm --dir frontend build
```

## 贡献与反馈

欢迎在 QQ 群 1094941151 交流问题和改进建议。反馈问题时建议附带运行环境、配置范围、相关日志和复现步骤。
