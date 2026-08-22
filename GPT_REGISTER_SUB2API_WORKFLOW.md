# GPT 注册、加入空间、创建令牌/RT 并上传 sub2api 流程

本文档说明三种邮箱来源的完整流水线：

1. **QQ 邮箱**：需要先提供/导入邮箱收码链接，再注册 GPT，申请加入指定 workspace。
2. **Outlook 邮箱**：从已导入邮箱池选择 `outlook.com/hotmail.com` 邮箱，再注册 GPT，默认加入固定 workspace：`68dd6ed1-261e-45e3-9fcc-28e09adee411`。
3. **TinkMail 邮箱**：系统可自动注册 `tinkmail.me` 邮箱，再注册 GPT，默认加入固定 workspace：`d5ea8b6d-de61-48d5-9210-bf662eb1f1c3`。

后续统一执行：优先创建 team Codex 令牌；如果失败，则自动获取 OAuth RT，并上传到 sub2api。

---

## 0. 前置检查

确保后台已启动：

```bash
cd /home/ubuntu/chatgpt-queue-reg
curl -sS http://127.0.0.1:8000/api/pipelines/presets | jq .
```

常用查询命令：

```bash
# 最近流水线
curl -sS 'http://127.0.0.1:8000/api/pipelines?limit=10' | jq '.[] | {id,preset,status,current_stage,account_id,error,result}'

# 最近任务
curl -sS 'http://127.0.0.1:8000/api/jobs?limit=10' | jq '.[] | {id,type,status,account_id,error,result}'

# 指定任务日志
curl -sS 'http://127.0.0.1:8000/api/jobs/JOB_ID/events?limit=120' \
  | jq -r '.[] | "\(.created_at) [\(.level)] \(.message)"'
```

---

## 1A. Outlook 邮箱流程

Outlook / Hotmail 邮箱池默认 workspace：

```text
68dd6ed1-261e-45e3-9fcc-28e09adee411
```

### 1A.1 从邮箱池选择最新可用 Outlook 邮箱并执行步骤1

```bash
cd /home/ubuntu/chatgpt-queue-reg

EMAIL=$(curl -sS 'http://127.0.0.1:8000/api/email/accounts?limit=1000' \
  | jq -r '[.[] | select(.provider=="microsoft" and .enabled==true and ((.email|ascii_downcase|endswith("@outlook.com")) or (.email|ascii_downcase|endswith("@hotmail.com"))))] | sort_by(.created_at) | reverse | .[0].email')

./scripts/create_account_join_workspace.py \
  -e "$EMAIL" \
  -w 68dd6ed1-261e-45e3-9fcc-28e09adee411 \
  --wait \
  --json
```

### 1A.2 步骤2：创建令牌/RT 并上传 sub2api

```bash
./scripts/team_token_or_rt_upload_sub.py \
  --account-id ACCOUNT_ID \
  -w 68dd6ed1-261e-45e3-9fcc-28e09adee411 \
  --json
```

---

## 1. QQ 邮箱流程

QQ 邮箱这批历史验证成功的默认 workspace：

```text
7dc92548-255c-4e45-a570-ef25d793ab23
```

最近成功加入该空间的 QQ 邮箱包括：

```text
3661108237@qq.com
3669891558@qq.com
3646817554@qq.com
3765704066@qq.com
3766497800@qq.com
3840036562@qq.com
3770710971@qq.com
3853122365@qq.com
3853654629@qq.com
```

注意：之前 `885440cb-01f9-4927-b2da-c7734cde849d` 对 QQ 邮箱失败过，后面切换到 `7dc92548-255c-4e45-a570-ef25d793ab23` 成功。

### 1.1 输入格式

QQ 邮箱需要提供邮箱和收码接口，一行一个：

```text
邮箱----收码URL
```

示例：

```text
3661108237@qq.com----http://120.77.168.211:20269/v1/messages?email=3661108237%40qq.com&api%5Fkey=qma_xxx
```

### 1.2 注册 GPT 并申请加入指定空间

把 `WORKSPACE_ID` 换成目标空间 ID：

```bash
cd /home/ubuntu/chatgpt-queue-reg

./scripts/create_account_join_workspace.py \
  --email-line '3661108237@qq.com----http://120.77.168.211:20269/v1/messages?email=3661108237%40qq.com&api%5Fkey=qma_xxx' \
  -w WORKSPACE_ID \
  --wait \
  --json
```

如果已经把 QQ 邮箱导入邮箱池，也可以直接指定邮箱：

```bash
./scripts/create_account_join_workspace.py \
  -e 3661108237@qq.com \
  -w WORKSPACE_ID \
  --wait \
  --json
```

成功后输出里会有：

```json
"account_id": 123
```

这个 `account_id` 是后续创建令牌/RT 上传 sub2api 需要用的本地账号 ID。

### 1.3 创建 Codex 令牌；失败则获取 RT；上传 sub2api

```bash
./scripts/team_token_or_rt_upload_sub.py \
  --account-id ACCOUNT_ID \
  -w WORKSPACE_ID \
  --json
```

也可以按邮箱查找账号：

```bash
./scripts/team_token_or_rt_upload_sub.py \
  --email 3661108237@qq.com \
  -w WORKSPACE_ID \
  --json
```

结果说明：

- `path=codex_token`：创建 team Codex 令牌成功，并已上传 sub2api。
- `path=oauth_rt`：Codex 令牌创建失败，已 fallback 获取 OAuth RT，并上传 sub2api。
- `sub2api_job.result.sub2api_status=active`：sub2api 同步成功。

---

## 2. TinkMail 流程

TinkMail 可以自动注册邮箱，不需要提前提供邮箱。

默认目标 workspace：

```text
d5ea8b6d-de61-48d5-9210-bf662eb1f1c3
```

### 2.1 自动注册一个 TinkMail 邮箱

```bash
cd /home/ubuntu/chatgpt-queue-reg

./scripts/register_tinkmail_email.py --wait --json
```

成功后输出中会有：

```json
"email": "xxxx@tinkmail.me"
```

### 2.2 使用该 TinkMail 邮箱注册 GPT 并申请加入默认空间

把 `EMAIL` 换成上一步输出的邮箱：

```bash
./scripts/create_account_join_workspace.py \
  -e EMAIL \
  -w d5ea8b6d-de61-48d5-9210-bf662eb1f1c3 \
  --wait \
  --json
```

成功后记录输出里的：

```json
"account_id": 123
```

### 2.3 创建 Codex 令牌；失败则获取 RT；上传 sub2api

```bash
./scripts/team_token_or_rt_upload_sub.py \
  --account-id ACCOUNT_ID \
  -w d5ea8b6d-de61-48d5-9210-bf662eb1f1c3 \
  --json
```

如果 `codex_token` 返回：

```text
access_token_creation_disabled
```

这是当前 team 禁止创建令牌，脚本会自动 fallback 到 OAuth RT，并继续上传 sub2api。

---

## 3. TinkMail 一套命令示例

```bash
cd /home/ubuntu/chatgpt-queue-reg

# 1) 注册 TinkMail 邮箱
TM_OUT=$(./scripts/register_tinkmail_email.py --wait --json)
echo "$TM_OUT" | jq .
EMAIL=$(echo "$TM_OUT" | jq -r '.pipeline.result.last_job_result.email')
echo "EMAIL=$EMAIL"

# 2) 用 TinkMail 注册 GPT + 申请加入默认空间
REG_OUT=$(./scripts/create_account_join_workspace.py \
  -e "$EMAIL" \
  -w d5ea8b6d-de61-48d5-9210-bf662eb1f1c3 \
  --wait \
  --json)
echo "$REG_OUT" | jq .
ACCOUNT_ID=$(echo "$REG_OUT" | jq -r '.pipelines[0].account_id')
echo "ACCOUNT_ID=$ACCOUNT_ID"

# 3) 优先创建 Codex 令牌；失败自动获取 RT；上传 sub2api
./scripts/team_token_or_rt_upload_sub.py \
  --account-id "$ACCOUNT_ID" \
  -w d5ea8b6d-de61-48d5-9210-bf662eb1f1c3 \
  --json
```

---

## 4. QQ 一套命令示例

```bash
cd /home/ubuntu/chatgpt-queue-reg

EMAIL='3661108237@qq.com'
WORKSPACE_ID='替换成目标workspace_id'
EMAIL_LINE='3661108237@qq.com----http://120.77.168.211:20269/v1/messages?email=3661108237%40qq.com&api%5Fkey=qma_xxx'

# 1) 导入 QQ 邮箱 + 注册 GPT + 申请加入空间
REG_OUT=$(./scripts/create_account_join_workspace.py \
  --email-line "$EMAIL_LINE" \
  -w "$WORKSPACE_ID" \
  --wait \
  --json)
echo "$REG_OUT" | jq .
ACCOUNT_ID=$(echo "$REG_OUT" | jq -r '.pipelines[0].account_id')
echo "ACCOUNT_ID=$ACCOUNT_ID"

# 2) 优先创建 Codex 令牌；失败自动获取 RT；上传 sub2api
./scripts/team_token_or_rt_upload_sub.py \
  --account-id "$ACCOUNT_ID" \
  -w "$WORKSPACE_ID" \
  --json
```

---

## 5. 常见问题

### 5.1 任务一直 queued

可能有旧任务占着队列。查看运行中的任务：

```bash
curl -sS 'http://127.0.0.1:8000/api/jobs?limit=20' \
  | jq '.[] | select(.status=="running" or .status=="queued") | {id,type,status,account_id,error}'
```

强停卡住的任务：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/jobs/JOB_ID/force-stop | jq .
```

### 5.2 验证码错误 / max_check_attempts

如果日志出现：

```text
wrong_email_otp_code
max_check_attempts: Too many tries
```

说明该邮箱短时间内错误尝试过多，建议换新邮箱或等待冷却。

### 5.3 Codex 令牌创建失败

如果日志出现：

```text
access_token_creation_disabled
```

说明该 workspace 当前禁用了令牌创建。无需手动处理，`team_token_or_rt_upload_sub.py` 会 fallback 到 OAuth RT。

### 5.4 判断 sub2api 是否成功

看最终输出：

```json
"sub2api_job": {
  "status": "succeeded",
  "result": {
    "sub2api_status": "active"
  }
}
```

`active` 即上传成功并可调度。
