# 2FAuth API 开发者接入指南

## 1. 接入范围

本文档适用于当前线上实例：

- 服务地址：`https://2fa.oai-gpt.com`
- API 前缀：`https://2fa.oai-gpt.com/api/v1`
- 部署版本：2FAuth `8.0.1`
- 数据格式：JSON，二维码图片解码接口除外
- 鉴权方式：Personal Access Token（PAT）Bearer Token

本文档覆盖以下流程：

1. 使用二维码数据预览 2FA 条目。
2. 创建并保存 2FA 条目。
3. 查询条目列表或单个条目。
4. 获取已保存条目的当前验证码。
5. 不保存条目，直接通过二维码数据生成验证码。
6. 从二维码图片中解码 `otpauth://` 数据。

## 2. 鉴权

登录 2FAuth 后进入 `Settings -> OAUTH`，选择 `Generate a new token` 创建
PAT。PAT 只在创建时显示一次，应立即保存到密钥管理系统。

每个 API 请求都必须携带：

```http
Authorization: Bearer <PAT>
Accept: application/json
```

不要将 PAT 放在 URL、浏览器前端代码、日志或代码仓库中。服务端程序应从
环境变量或密钥管理服务读取 PAT。

本文档中的 PowerShell 示例使用：

```powershell
$env:TWOFAUTH_PAT = '由部署人员安全注入的 PAT'

$baseUrl = 'https://2fa.oai-gpt.com/api/v1'
$headers = @{
    Authorization = "Bearer $env:TWOFAUTH_PAT"
    Accept = 'application/json'
}
```

## 3. 二维码数据格式

常见的二维码内容是一个 `otpauth://` URI：

```text
otpauth://totp/Example:user%40example.com?secret=BASE32_SECRET_PLACEHOLDER&issuer=Example&algorithm=SHA1&digits=6&period=30
```

主要参数：

| 参数 | 含义 | 常见值 |
| --- | --- | --- |
| `secret` | Base32 编码的永久 TOTP 密钥 | 必填 |
| `issuer` | 服务名称 | `Example` |
| `algorithm` | 摘要算法 | `SHA1`、`SHA256`、`SHA512` |
| `digits` | 验证码位数 | `6` |
| `period` | 更新周期，单位为秒 | `30` |

应保留二维码提供的原始参数，不要擅自改变算法、位数或周期。`secret` 是永久
密钥，敏感级别高于短期 6 位验证码。

## 4. API 一览

| 方法 | 路径 | 用途 | 成功状态 |
| --- | --- | --- | --- |
| `POST` | `/twofaccounts/preview` | 解析并预览 `otpauth://` URI，不保存 | `200` |
| `POST` | `/twofaccounts` | 创建并保存 2FA 条目 | `201` |
| `GET` | `/twofaccounts` | 查询当前用户可见的条目 | `200` |
| `GET` | `/twofaccounts/{id}` | 查询单个条目 | `200` |
| `GET` | `/twofaccounts/{id}/otp` | 获取已保存条目的当前验证码 | `200` |
| `POST` | `/twofaccounts/otp` | 使用 URI 临时生成验证码，不保存 | `200` |
| `POST` | `/qrcode/decode` | 从二维码图片中提取数据 | `200` |

## 5. 推荐接入流程

### 5.1 预览二维码数据

先调用预览接口验证二维码数据。该操作不会写数据库。

```powershell
$qrData = 'otpauth://totp/Example:user%40example.com?secret=BASE32_SECRET_PLACEHOLDER&issuer=Example&algorithm=SHA1&digits=6&period=30'
$body = @{ uri = $qrData } | ConvertTo-Json

$preview = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/twofaccounts/preview" `
    -Headers $headers `
    -ContentType 'application/json' `
    -Body $body

$preview | Select-Object service, account, otp_type, algorithm, digits, period
```

预览结果应与二维码来源页面显示的信息一致。

### 5.2 创建并保存条目

```powershell
$created = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/twofaccounts" `
    -Headers $headers `
    -ContentType 'application/json' `
    -Body $body

$accountId = $created.id
[pscustomobject]@{
    Id = $created.id
    Service = $created.service
    Account = $created.account
}
```

保存返回的 `id`，后续应通过 ID 获取验证码。创建响应可能包含永久密钥，
不要记录完整响应体。

### 5.3 查询条目列表

```powershell
$accounts = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/twofaccounts" `
    -Headers $headers

$accounts | Select-Object id, service, account, otp_type, algorithm, digits, period
```

默认查询不需要返回 `secret`。除非执行受控迁移，不要使用会请求永久密钥的
`withSecret=true` 参数。

### 5.4 查询单个条目

```powershell
$account = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/twofaccounts/$accountId" `
    -Headers $headers

$account | Select-Object id, service, account, otp_type, algorithm, digits, period
```

### 5.5 获取当前验证码

```powershell
$otp = Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/twofaccounts/$accountId/otp" `
    -Headers $headers

$otp.password
```

典型响应：

```json
{
  "password": "225897",
  "otp_type": "totp",
  "generated_at": 1787846400,
  "period": 30
}
```

`password` 是当前验证码。客户端应把它当作字符串处理，以保留可能存在的前导
零。验证码接近周期边界时，目标系统可能在提交前已经进入下一周期；建议剩余
时间不足 5 秒时等待下一枚验证码。

### 5.6 不保存条目，直接生成验证码

只有无法保存条目时才使用此方式，因为每次请求都要重新传输永久密钥。

```powershell
$otp = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/twofaccounts/otp" `
    -Headers $headers `
    -ContentType 'application/json' `
    -Body $body

$otp.password
```

### 5.7 解码二维码图片

如果输入是 PNG、JPEG、BMP、GIF、SVG 或 WebP 图片，可先解码：

```powershell
$decoded = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/qrcode/decode" `
    -Headers $headers `
    -Form @{ qrcode = Get-Item 'C:\path\qrcode.png' }

$qrData = $decoded.data
```

随后将 `$qrData` 提交到预览或创建接口。不要把二维码图片保存到公共目录。

## 6. Python 示例

依赖：`requests`。

```python
import os
import requests

BASE_URL = "https://2fa.oai-gpt.com/api/v1"
TOKEN = os.environ["TWOFAUTH_PAT"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
}


def create_account(otpauth_uri: str) -> int:
    response = requests.post(
        f"{BASE_URL}/twofaccounts",
        headers=HEADERS,
        json={"uri": otpauth_uri},
        timeout=15,
    )
    response.raise_for_status()
    return int(response.json()["id"])


def list_accounts() -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/twofaccounts",
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_otp(account_id: int) -> str:
    response = requests.get(
        f"{BASE_URL}/twofaccounts/{account_id}/otp",
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return str(response.json()["password"])
```

调用程序不应输出 PAT、`otpauth://` URI、创建接口完整响应或验证码。

## 7. 错误处理

| 状态码 | 含义 | 建议处理 |
| --- | --- | --- |
| `400` | 请求格式或参数组合错误 | 修正请求，不自动重试 |
| `401` | PAT 缺失、无效或已撤销 | 停止调用并更新凭据 |
| `403` | 当前用户无权访问该资源 | 检查条目所有者和共享权限 |
| `404` | 条目 ID 不存在 | 刷新条目列表 |
| `422` | URI、密钥或字段校验失败 | 检查二维码数据和参数 |
| `429` | 达到 API 频率限制 | 按退避策略重试 |
| `5xx` | 服务端暂时异常 | 有上限地指数退避并告警 |

查询和预览请求可以安全重试。创建接口没有幂等键；如果创建请求超时，不要立即
盲目重试，否则可能产生重复条目。应先查询列表确认是否已创建，再决定是否重试。

## 8. 安全要求

- PAT 按登录密码等级管理，使用独立密钥并支持快速撤销。
- 只从服务端调用 API，不要把 PAT 打包到网页或桌面前端代码中。
- 不记录 PAT、二维码图片、`otpauth://` URI、永久密钥或 6 位验证码。
- 持久化条目后只保存其 `id`，以后通过 ID 获取验证码。
- 不在 URL 查询参数中传输 PAT 或永久密钥。
- 仅使用 HTTPS 地址，禁止关闭 TLS 证书校验。
- 为不同系统使用不同 PAT，系统下线时立即撤销对应 PAT。

## 9. 接入验收

完成接入后至少验证：

1. 无 PAT 或错误 PAT 返回 `401`。
2. 预览接口返回 `200` 且不新增条目。
3. 创建接口返回 `201` 和有效 `id`。
4. 列表和单条目查询能找到新建条目。
5. OTP 接口返回字符串形式的正确验证码。
6. 应用日志、错误日志和监控中没有 PAT、URI、密钥或验证码。
7. 创建请求超时后的处理不会生成重复条目。

官方参考：<https://docs.2fauth.app/api/>
