export type FieldType = 'text' | 'switch' | 'number' | 'select' | 'password'

export interface SettingField {
  key: string
  label: string
  type?: FieldType
  placeholder?: string
  options?: { value: string; label: string }[]
}

export interface PoolSettingGroup {
  id: string
  title: string
  description: string
  fields: SettingField[]
  emptyText?: string
}

export const WORKPOOL_SETTING_GROUPS: Record<string, PoolSettingGroup> = {
  register: {
    id: 'workpool.register',
    title: 'WorkPool: register 配置',
    description: '注册池只负责账号注册和账号身份绑定；邮箱、代理从对应资源池领取。',
    fields: [
      { key: 'worker_concurrency.register', label: 'register 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.register.proxy_region', label: '注册代理 region', placeholder: '留空则不限制 region' },
      { key: 'workpool.register.also_record_to_at_pool', label: '注册后写入 AT 池', type: 'switch' },
    ],
  },
  payment_link: {
    id: 'workpool.payment_link',
    title: 'WorkPool: payment_link 配置',
    description: '长链池负责选择套餐和生成 hosted payment link。',
    fields: [
      { key: 'worker_concurrency.payment_link', label: 'payment_link 并发', type: 'number', placeholder: '默认 3' },
      {
        key: 'workpool.payment_link.plan',
        label: '默认套餐',
        type: 'select',
        options: [
          { value: 'plus', label: 'Plus Hosted' },
          { value: 'team', label: 'Team Hosted' },
        ],
      },
      { key: 'workpool.payment_link.country', label: '默认国家', placeholder: 'Plus 默认 ID；Team 默认 US' },
      { key: 'workpool.payment_link.currency', label: '默认货币', placeholder: '留空按国家自动' },
      { key: 'workpool.payment_link.workspace_name', label: 'Team workspace 名称', placeholder: 'MyWorkspace' },
      { key: 'workpool.payment_link.price_interval', label: 'Team 付款周期', placeholder: 'month / year' },
      { key: 'workpool.payment_link.seat_quantity', label: 'Team 座位数', type: 'number', placeholder: '2' },
    ],
  },
  payment: {
    id: 'workpool.payment',
    title: 'WorkPool: payment 配置',
    description: '支付池负责付款自动化所需的支付代理、卡和短信项目；当前 payment 仍是 stub。',
    fields: [
      { key: 'worker_concurrency.payment', label: 'payment 并发', type: 'number', placeholder: '默认 2' },
      { key: 'payment_proxy_region', label: '支付代理 region', placeholder: '例如 US / ID' },
      { key: 'payment_proxy_url', label: '显式支付代理 URL', placeholder: '留空则按 region 从 proxy_pool 领取' },
      { key: 'paypal_email', label: 'PayPal 邮箱' },
      { key: 'paypal_password', label: 'PayPal 密码', type: 'password' },
      { key: 'paypal_cookies', label: 'PayPal cookies / cookie header', type: 'password' },
      { key: 'stripe_publishable_key', label: 'Stripe publishable key' },
      { key: 'captcha_api_key', label: 'Captcha API Key', type: 'password' },
      { key: 'captcha_api_url', label: 'Captcha API 地址' },
    ],
  },
  oauth_codex: {
    id: 'workpool.oauth_codex',
    title: 'WorkPool: oauth_codex 配置',
    description: 'OAuth 池负责 Codex RT/AT 获取；只有遇到 add-phone 才使用手机接码。',
    fields: [
      { key: 'worker_concurrency.oauth_codex', label: 'oauth_codex 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.oauth_codex.sms_project', label: 'OAuth 短信项目', placeholder: 'openai_oauth' },
      { key: 'workpool.oauth_codex.phone_verification_enabled', label: '启用 add-phone 接码', type: 'switch' },
      {
        key: 'workpool.oauth_codex.phone_verification_provider',
        label: '接码平台',
        type: 'select',
        options: [
          { value: 'smsbower', label: 'SmsBower' },
          { value: 'fivesim', label: '5SIM' },
          { value: 'smsgiare', label: 'SmsGiaRe' },
        ],
      },
      { key: 'workpool.oauth_codex.phone_verification_use_proxy', label: '接码平台 API 走账号代理', type: 'switch' },
      { key: 'workpool.oauth_codex.phone_verification_max_attempts', label: '最大取号次数', type: 'number', placeholder: '3' },
      { key: 'workpool.oauth_codex.phone_verification_poll_timeout_seconds', label: '等待短信秒数', type: 'number', placeholder: '180' },
    ],
  },
  rt_keepalive: {
    id: 'workpool.rt_keepalive',
    title: 'WorkPool: rt_keepalive 配置',
    description: 'RT 同步池负责上传 Codex RT 到 sub2api 并拉取状态；RT 轮转由 sub2api 维护。',
    fields: [
      { key: 'worker_concurrency.rt_keepalive', label: 'rt_keepalive 并发', type: 'number', placeholder: '默认 2' },
      { key: 'sub2api_base_url', label: 'sub2api 地址' },
      { key: 'sub2api_api_key', label: 'sub2api API Key', type: 'password' },
      { key: 'sub2api_upload_path', label: '上传路径', placeholder: '/api/codex-tokens' },
      { key: 'sub2api_status_path', label: '状态路径', placeholder: '/api/codex-tokens/{external_id}' },
      { key: 'sub2api_timeout_seconds', label: '请求超时秒数', type: 'number', placeholder: '30' },
    ],
  },
}

export const RESOURCEPOOL_SETTING_GROUPS: Record<string, PoolSettingGroup> = {
  email_pool: {
    id: 'resource.email_pool',
    title: 'ResourcePool: email_pool 配置',
    description: '邮箱资源池负责邮箱领取、域名策略和 OTP 轮询参数。',
    fields: [
      { key: 'email_domain_rule_enabled', label: '启用邮箱域名规则', type: 'switch' },
      { key: 'email_domain_level_count', label: '邮箱域名级数', type: 'number', placeholder: '2' },
      { key: 'email_poll_interval_seconds', label: '邮件轮询间隔(秒)', type: 'number', placeholder: '5' },
    ],
  },
  card_pool: {
    id: 'resource.card_pool',
    title: 'ResourcePool: card_pool 配置',
    description: '付款卡资源本身在卡池数据表维护。',
    fields: [],
    emptyText: '暂无全局配置项；卡号、状态、失败/禁用等资源数据应在付款卡资源列表维护。',
  },
  paypal_number_pool: {
    id: 'resource.paypal_number_pool',
    title: 'ResourcePool: paypal_number_pool 配置',
    description: 'PayPal 手机号和短信 URL 是支付池消费的一次性资源，资源数据在 PayPal 号码页维护。',
    fields: [],
    emptyText: '暂无全局配置项；phone、smsurl、状态和失败原因在 PayPal 号码资源列表维护。',
  },
  proxy_pool: {
    id: 'resource.proxy_pool',
    title: 'ResourcePool: proxy_pool 配置',
    description: '代理资源本身在代理页维护；这里仅保留全局 fallback。账号链路优先使用注册时绑定的代理。',
    fields: [
      { key: 'default_proxy_enabled', label: '启用全局默认代理', type: 'switch' },
      { key: 'default_proxy_url', label: '全局默认代理', placeholder: 'http://user:pass@host:port' },
    ],
  },
  sms_pool: {
    id: 'resource.sms_pool',
    title: 'ResourcePool: sms_pool 配置',
    description: '短信资源池 provider 凭据。WorkPool 只引用短信项目/平台配置。',
    fields: [
      { key: 'smsbower_api_key', label: 'SmsBower API Key', type: 'password' },
      { key: 'smsbower_base_url', label: 'SmsBower API 地址', placeholder: 'https://smsbower.page/stubs/handler_api.php' },
      { key: 'smsbower_service', label: 'SmsBower 服务代码', placeholder: 'dr' },
      { key: 'smsbower_country', label: 'SmsBower 国家 ID', type: 'number', placeholder: '0' },
      { key: 'fivesim_api_key', label: '5SIM API Key', type: 'password' },
      { key: 'fivesim_service', label: '5SIM 服务代码', placeholder: 'openai' },
      { key: 'fivesim_country', label: '5SIM 国家', placeholder: 'any' },
      { key: 'fivesim_operator', label: '5SIM 运营商', placeholder: 'any' },
      { key: 'fivesim_max_price', label: '5SIM 最高价格', type: 'number', placeholder: '0' },
      { key: 'smsgiare_token', label: 'SmsGiaRe Token', type: 'password' },
      { key: 'smsgiare_base_url', label: 'SmsGiaRe API 地址', placeholder: 'https://api.smsgiare.io.vn/api/v1' },
      { key: 'smsgiare_service_id', label: 'SmsGiaRe OpenAI serviceId', type: 'number', placeholder: '2653' },
      {
        key: 'smsgiare_carrier',
        label: 'SmsGiaRe 运营商',
        type: 'select',
        options: [
          { value: 'ALL', label: 'ALL' },
          { value: 'VIETTEL', label: 'VIETTEL' },
          { value: 'VINA', label: 'VINA' },
          { value: 'MOBI', label: 'MOBI' },
        ],
      },
      { key: 'smsgiare_reuse_phone_number', label: 'SmsGiaRe 复用号码', placeholder: '可留空' },
    ],
  },
}

export function isTruthy(value: unknown): boolean {
  return ['1', 'true', 'yes', 'on', 'enabled'].includes(String(value || '').trim().toLowerCase())
}

export function toFormValues(fields: SettingField[], data: Record<string, string>) {
  const values: Record<string, unknown> = {}
  for (const field of fields) {
    const raw = data[field.key]
    if (field.type === 'switch') {
      values[field.key] = isTruthy(raw)
    } else if (field.type === 'number') {
      if (raw !== undefined && raw !== '') {
        const parsed = Number(raw)
        values[field.key] = Number.isFinite(parsed) ? parsed : undefined
      } else {
        values[field.key] = undefined
      }
    } else {
      values[field.key] = raw ?? ''
    }
  }
  return values
}

export function toSettingsValues(fields: SettingField[], values: Record<string, unknown>) {
  const data: Record<string, string> = {}
  for (const field of fields) {
    const value = values[field.key]
    if (field.type === 'switch') {
      data[field.key] = value ? '1' : '0'
    } else if (value === undefined || value === null) {
      data[field.key] = ''
    } else {
      data[field.key] = String(value)
    }
  }
  return data
}
