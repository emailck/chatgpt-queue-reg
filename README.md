# chatgpt-queue-reg

队列式 ChatGPT 注册 + Team 支付长链 + 微软邮箱（含裂变）+ 随处调起 Camoufox / HAR 的精简版项目。

## 模块边界

- **后端 (`backend/`)**：FastAPI + SQLModel/SQLite，多线程 worker 池。
- **前端 (`frontend/`)**：React 19 + Vite + Ant Design，统一 dark theme。
- **集成 (`backend/integrations/`)**：从原项目复制并重写 import 路径的 ChatGPT 注册 / 长链生成代码 + 自写的 Microsoft Graph 邮箱模块。
- **占位 (`backend/flows/payment_empty.py`)**：长链生成成功后 pipeline 进入“占位支付”一步，永远 succeeded，方便接真实支付时再替换。

## Pipeline

每条 pipeline 自动串联 3 步：

1. `chatgpt_register` 注册账号 → 入库 `chatgpt_accounts`
2. `chatgpt_payment_link` 生成 Team hosted 长链 → 入库 `payment_links`
3. `payment_empty` 占位支付步骤

任何一步失败/取消，pipeline 整体标记失败并停止推进。

## 核心 API

- `POST /api/pipelines/chatgpt-account` 创建 N 条 pipeline
- `GET /api/pipelines` / `GET /api/pipelines/{id}`
- `GET /api/jobs` / `GET /api/jobs/{id}/events/stream` (SSE 日志)
- `GET /api/accounts` / `POST /api/accounts/{id}/read-email|debug-browser|payment-link/retry`
- `GET /api/payment-links` / `POST /api/payment-links/{id}/payment|debug-browser`
- `POST /api/email/import` (支持 `alias_split_enabled` 邮箱裂变 + 含原邮箱)
- `POST /api/email/read` 主动收一封邮件 / OTP
- `POST /api/browser-debug/open` 任何上下文调起 Camoufox/Chromium，注入 cookies/UA/localStorage 并抓 HAR
- `GET /api/queue/stats` / `GET /api/healthz`

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
- `chatgpt_accounts` / `payment_links`
- `email_accounts` / `email_messages`
- `proxies` / `browser_debug_sessions`
- `settings`

非 SQLite 的 DB 也行，把 `DATABASE_URL` 指过去即可（SQLite 上自动开 WAL + busy_timeout）。

## 调试浏览器

`open_debug_session(...)` 会把：

- 账号注册时保存的 `cookies_json` 注入 chatgpt.com
- `local_storage_json` 通过 init_script 写入 window.localStorage
- 账号注册时的 UA / fingerprint
- 任意覆盖代理
- HAR 输出到 `logs/har/debug-{ts}.har`

并把 browser/context/page/playwright/camoufox 句柄放进进程内 registry，窗口不会被回收。需要关闭：`POST /api/browser-debug/sessions/{id}/close`。

## 邮箱裂变（Plus 别名）

`POST /api/email/import` 设置 `alias_split_enabled=true` + `alias_split_count` 即可。每条原始记录会在 `email_accounts` 中扩展为 N 条带随机后缀的 `+xxxxxx@domain` 邮箱（OAuth 信息共享），包含/不包含原始邮箱可控。
