export type FieldType = 'text' | 'switch' | 'number' | 'select' | 'password'

export interface SettingField {
  key: string
  label: string
  type?: FieldType | 'proxy_select'
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

const PROXY_PROVIDER_OPTIONS = [
  { value: 'arxlabs', label: 'arxlabs' },
  { value: '1024proxy', label: '1024proxy' },
  { value: 'custom', label: 'custom / 其他' },
]

export const WORKPOOL_SETTING_GROUPS: Record<string, PoolSettingGroup> = {
  register: {
    id: 'workpool.register',
    title: 'WorkPool: register 配置',
    description: '注册池只负责账号注册和账号身份绑定；邮箱、代理从对应资源池领取。',
    fields: [
      { key: 'worker_concurrency.register', label: 'register 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.register.proxy_provider', label: '注册代理厂商', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'workpool.register.proxy_region', label: '注册代理 region', placeholder: 'US / JP / BR' },
      { key: 'workpool.register.proxy_ttl', label: '注册代理持续时间 t', type: 'number', placeholder: '默认 5' },
      { key: 'workpool.register.proxy_url', label: '显式代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空则按 provider 模板生成' },
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
  pp_long_link: {
    id: 'workpool.pp_long_link',
    title: 'WorkPool: pp_long_link 配置',
    description: 'PP 长链接池：使用 app.py 的 OPLL 流程生成 PayPal approve 长链接。这里配置失败重试次数，不是生成条数。',
    fields: [
      { key: 'worker_concurrency.pp_long_link', label: 'pp_long_link 并发', type: 'number', placeholder: '默认 2' },
      { key: 'workpool.pp_long_link.country', label: '默认国家', placeholder: 'US' },
      { key: 'workpool.pp_long_link.currency', label: '默认币种', placeholder: 'USD' },
      { key: 'workpool.pp_long_link.target_amount', label: '金额覆盖（可选）' },
      { key: 'workpool.pp_long_link.max_retries', label: '失败重试次数', type: 'number', placeholder: '3' },
      { key: 'workpool.pp_long_link.retry_backoff_ms', label: '重试退避毫秒', type: 'number', placeholder: '5000' },
      { key: 'workpool.pp_long_link.proxy_url', label: '统一代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空复用任务/账号代理' },
      { key: 'workpool.pp_long_link.create_proxy_url', label: 'create 代理 URL', type: 'proxy_select' },
      { key: 'workpool.pp_long_link.followup_proxy_url', label: 'followup 代理 URL', type: 'proxy_select' },
      { key: 'workpool.pp_long_link.approve_proxy_url', label: 'approve 代理 URL', type: 'proxy_select' },
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
      { key: 'workpool.payment.max_proxy_switches', label: '坏代理最大切换次数', type: 'number', placeholder: '默认 3' },
      {
        key: 'workpool.payment.paypal_mode',
        label: 'PayPal 模式',
        type: 'select',
        options: [
          { value: 'hybrid', label: '协议 + Camoufox 混合' },
          { value: 'pure_protocol', label: '纯协议' },
        ],
      },
      { key: 'paypal_email', label: 'PayPal 邮箱' },
      { key: 'paypal_password', label: 'PayPal 密码', type: 'password' },
      { key: 'paypal_cookies', label: 'PayPal cookies / cookie header', type: 'password' },
      { key: 'stripe_publishable_key', label: 'Stripe publishable key' },
      {
        key: 'captcha_provider',
        label: 'Captcha 平台',
        type: 'select',
        options: [
          { value: 'yescaptcha', label: 'YesCaptcha' },
        ],
      },
      { key: 'captcha_api_key', label: 'YesCaptcha API Key', type: 'password' },
      { key: 'captcha_api_url', label: 'Captcha API 地址', placeholder: '留空默认 https://api.yescaptcha.com' },
      { key: 'captcha_timeout', label: 'Captcha 超时秒数', type: 'number', placeholder: '120' },
      { key: 'captcha_poll_interval', label: 'Captcha 轮询间隔秒数', type: 'number', placeholder: '5' },
      { key: 'hcaptcha_site_key', label: 'hCaptcha sitekey', placeholder: '留空自动从页面提取' },
      { key: 'hcaptcha_website_url', label: 'hCaptcha websiteURL', placeholder: '留空使用当前验证页 URL' },
    ],
  },
  chatgpt_session: {
    id: 'workpool.chatgpt_session',
    title: 'WorkPool: chatgpt_session 配置',
    description: '复用注册阶段保存的 ChatGPT Web session，并刷新 chatgpt.com /api/auth/session AT。',
    fields: [
      { key: 'worker_concurrency.chatgpt_session', label: 'chatgpt_session 并发', type: 'number', placeholder: '默认 3' },
      {
        key: 'workpool.chatgpt_session.mode',
        label: 'Session 模式',
        type: 'select',
        options: [
          { value: 'session', label: 'session 默认' },
        ],
      },
      { key: 'workpool.chatgpt_session.refresh_before_seconds', label: '提前刷新秒数', type: 'number', placeholder: '300' },
      { key: 'workpool.chatgpt_session.max_attempts', label: 'Session 请求重试次数', type: 'number', placeholder: '3' },
      { key: 'workpool.chatgpt_session.relogin_reuse_account_proxy', label: '重登复用账号代理', type: 'switch' },
      { key: 'workpool.chatgpt_session.proxy_provider', label: '重登代理厂商', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'workpool.chatgpt_session.proxy_region', label: '重登代理 region', placeholder: '只用于重登；US / JP / BR' },
      { key: 'workpool.chatgpt_session.proxy_ttl', label: '重登代理持续时间 t', type: 'number', placeholder: '默认 5' },
    ],
  },
  sub2api_sync: {
    id: 'workpool.sub2api_sync',
    title: 'WorkPool: sub2api_sync 配置',
    description: 'sub2api 池同步：有 RT 就传 RT，没有 RT 就传 Web session replay 材料。',
    fields: [
      { key: 'worker_concurrency.sub2api_sync', label: 'sub2api_sync 并发', type: 'number', placeholder: '默认 5' },
      {
        key: 'workpool.sub2api_sync.mode',
        label: '同步模式',
        type: 'select',
        options: [
          { value: 'auto', label: 'auto' },
        ],
      },
      { key: 'sub2api_base_url', label: 'sub2api 地址' },
      { key: 'sub2api_api_key', label: 'sub2api API Key', type: 'password' },
      { key: 'sub2api_openai_import_path', label: '账号导入路径', placeholder: '/api/v1/admin/accounts/data' },
      { key: 'sub2api_account_export_path', label: '账号导出路径', placeholder: '/api/v1/admin/accounts/data' },
      { key: 'sub2api_account_list_path', label: '账号查重/列表路径', placeholder: '/api/v1/admin/accounts' },
      { key: 'sub2api_account_status_path', label: '账号状态路径', placeholder: '/api/v1/admin/accounts/{account_id}' },
      { key: 'sub2api_account_update_path', label: '账号更新路径', placeholder: '/api/v1/admin/accounts/{account_id}' },
      { key: 'sub2api_account_bulk_update_path', label: '账号批量更新路径', placeholder: '/api/v1/admin/accounts/bulk-update' },
      { key: 'sub2api_sold_group_id', label: '已售出分组 ID', type: 'number', placeholder: '导出并标记已售前必须配置' },
      { key: 'sub2api_timeout_seconds', label: '请求超时秒数', type: 'number', placeholder: '30' },
    ],
  },
  openai_oauth: {
    id: 'workpool.openai_oauth',
    title: 'WorkPool: openai_oauth 配置',
    description: 'OpenAI OAuth PKCE 获取 RT；短期 OAuth access_token 只作 OAuth 元数据保存。',
    fields: [
      { key: 'worker_concurrency.openai_oauth', label: 'openai_oauth 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.openai_oauth.proxy_provider', label: 'OAuth 代理厂商', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'workpool.openai_oauth.proxy_region', label: 'OAuth 代理 region', placeholder: 'US / JP / BR' },
      { key: 'workpool.openai_oauth.proxy_ttl', label: 'OAuth 代理持续时间 t', type: 'number', placeholder: '默认 5' },
      { key: 'workpool.openai_oauth.proxy_url', label: 'OAuth 显式代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空优先复用账号代理' },
      { key: 'workpool.openai_oauth.sms_project', label: 'OAuth 短信项目', placeholder: 'openai_oauth' },
      { key: 'workpool.openai_oauth.phone_verification_enabled', label: '启用 add-phone 接码', type: 'switch' },
      {
        key: 'workpool.openai_oauth.phone_verification_provider',
        label: '接码平台',
        type: 'select',
        options: [
          { value: 'smsbower', label: 'SmsBower' },
          { value: 'fivesim', label: '5SIM' },
          { value: 'smsgiare', label: 'SmsGiaRe' },
          { value: 'smspool', label: 'SmsPool' },
        ],
      },
      { key: 'workpool.openai_oauth.phone_verification_use_proxy', label: '接码平台 API 走账号代理', type: 'switch' },
      { key: 'workpool.openai_oauth.phone_verification_max_attempts', label: '最大取号次数', type: 'number', placeholder: '3' },
      { key: 'workpool.openai_oauth.phone_verification_poll_timeout_seconds', label: '等待短信秒数', type: 'number', placeholder: '180' },
      { key: 'workpool.openai_oauth.smspool_api_key', label: 'SmsPool API Key', type: 'password' },
      { key: 'workpool.openai_oauth.smspool_base_url', label: 'SmsPool Base URL', placeholder: '默认 https://api.smspool.net' },
      { key: 'workpool.openai_oauth.smspool_country', label: 'SmsPool 国家', placeholder: '例如 77 / 1 / US' },
      { key: 'workpool.openai_oauth.smspool_countries', label: 'SmsPool 国家列表', placeholder: '多个用换行/逗号分隔，留空则用单个国家' },
      { key: 'workpool.openai_oauth.smspool_service', label: 'SmsPool Service', placeholder: '默认 openai' },
      { key: 'workpool.openai_oauth.smspool_pool', label: 'SmsPool Pool', placeholder: '留空则自动取可用 pool' },
      { key: 'workpool.openai_oauth.smspool_max_price', label: 'SmsPool 最高价格', type: 'number', placeholder: '0 表示不限' },
      { key: 'workpool.openai_oauth.smspool_pricing_option', label: 'SmsPool Pricing Option', placeholder: '可留空' },
      { key: 'workpool.openai_oauth.smspool_max_reuses', label: 'SmsPool 最大复用次数', type: 'number', placeholder: '3' },
      { key: 'workpool.openai_oauth.smspool_reuse_cooldown_seconds', label: 'SmsPool 复用冷却秒数', type: 'number', placeholder: '1800' },
      { key: 'workpool.openai_oauth.smspool_reuse_enabled', label: '启用 SmsPool 复用池', type: 'switch' },
      { key: 'workpool.openai_oauth.smspool_purchase_enabled', label: '启用 SmsPool 购买号码', type: 'switch' },
    ],
  },
  sso_oauth: {
    id: 'workpool.sso_oauth',
    title: 'WorkPool: sso_oauth 配置',
    description: 'SSO OAuth 通过外部 IdP (auth.oai-gpt.com) 登录获取 RT。不需要邮箱OTP/密码。',
    fields: [
      { key: 'worker_concurrency.sso_oauth', label: 'sso_oauth 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.sso_oauth.sso_email_domain', label: 'SSO 邮箱域名', placeholder: '例: aicoco.xyz，随机用户名+此域名' },
      { key: 'workpool.sso_oauth.sso_invite_code', label: 'SSO 邀请码', placeholder: '邀请码，用于 auth.oai-gpt.com 注册' },
      { key: 'workpool.sso_oauth.sso_connection_id', label: 'SSO Connection ID', placeholder: 'conn_xxx，留空自动检测' },
      { key: 'workpool.sso_oauth.sso_provider', label: 'SSO Provider', type: 'number', placeholder: '默认 2 (WorkOS)' },
      { key: 'workpool.sso_oauth.account_id', label: '账号 ID (可选)', type: 'number', placeholder: '指定账号ID，RT会存入该账号的token池' },
    ],
  },
  codex_invitation: {
    id: 'workpool.codex_invitation',
    title: 'WorkPool: codex_invitation 配置',
    description: 'Codex referral 邀请池：输入源账号/邮箱 ID，按同域名随机生成受邀邮箱并发送邀请。',
    fields: [
      { key: 'worker_concurrency.codex_invitation', label: 'codex_invitation 并发', type: 'number', placeholder: '默认 2' },
      { key: 'workpool.codex_invitation.invite_count', label: '默认邀请数量', type: 'number', placeholder: '1' },
      { key: 'workpool.codex_invitation.prefix_len', label: '随机邮箱前缀长度', type: 'number', placeholder: '20' },
      { key: 'workpool.codex_invitation.proxy_url', label: '显式代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空则不使用；也可任务输入 proxy_url' },
      { key: 'workpool.codex_invitation.acquire_proxy', label: '无显式代理时从 proxy_pool 领取', type: 'switch' },
      { key: 'workpool.codex_invitation.check_eligibility', label: '发送前检查邀请额度', type: 'switch' },
      { key: 'workpool.codex_invitation.dry_run', label: '默认 dry-run', type: 'switch' },
      { key: 'workpool.codex_invitation.auth_file', label: 'Codex auth.json 路径', placeholder: '默认 ~/.codex/auth.json' },
      { key: 'workpool.codex_invitation.access_token', label: '母号 access_token 覆盖', type: 'password' },
      { key: 'workpool.codex_invitation.chatgpt_account_id', label: '母号 chatgpt-account-id 覆盖' },
    ],
  },
  codex_batch_invite: {
    id: 'workpool.codex_batch_invite',
    title: 'WorkPool: codex_batch_invite 配置',
    description: '批量邀请编排：多个母号先全部邀请完成，再统一创建 SSO OAuth + active 子流程。',
    fields: [
      { key: 'worker_concurrency.codex_batch_invite', label: 'codex_batch_invite 并发', type: 'number', placeholder: '默认 1' },
      { key: 'workpool.codex_batch_invite.invite_count_per_inviter', label: '每个母号邀请数量', type: 'number', placeholder: '最多 5' },
      { key: 'workpool.codex_batch_invite.prefix_len', label: '随机邮箱前缀长度', type: 'number', placeholder: '20' },
      { key: 'workpool.codex_batch_invite.activate_after_invite', label: '邀请完成后自动创建激活子流程', type: 'switch' },
      { key: 'workpool.codex_batch_invite.dry_run', label: '默认 dry-run', type: 'switch' },
    ],
  },
  codex_token: {
    id: 'workpool.codex_token',
    title: 'WorkPool: codex_token 配置',
    description: '切换 Team Workspace 后，在 ChatGPT Admin 创建 Codex 令牌，并交给 sub2api_sync 作为 access_token 上传。',
    fields: [
      { key: 'worker_concurrency.codex_token', label: 'codex_token 并发', type: 'number', placeholder: '默认 2' },
      { key: 'workpool.codex_token.workspace_ids', label: '默认 Team Workspace ID 列表', placeholder: '多个用换行/逗号分隔' },
      { key: 'workpool.codex_token.token_name', label: '令牌名前缀', placeholder: 'codex' },
      { key: 'workpool.codex_token.ttl', label: '令牌 TTL 秒', type: 'number', placeholder: '7776000' },
      { key: 'workpool.codex_token.scope', label: 'Scope', placeholder: 'chatgpt.workspace.feature.allow-codex-local-access.access' },
      { key: 'workpool.codex_token.interval_ms', label: 'Workspace 间隔毫秒', type: 'number', placeholder: '1500' },
      { key: 'workpool.codex_token.max_retries', label: '最大重试次数', type: 'number', placeholder: '3' },
      { key: 'workpool.codex_token.retry_backoff_ms', label: '重试退避毫秒', type: 'number', placeholder: '5000' },
      { key: 'workpool.codex_token.proxy_provider', label: '代理厂商', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'workpool.codex_token.proxy_region', label: '代理 region', placeholder: 'US / JP / BR' },
      { key: 'workpool.codex_token.proxy_ttl', label: '代理持续时间 t', type: 'number', placeholder: '默认 5' },
      { key: 'workpool.codex_token.proxy_url', label: '显式代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空复用账号代理' },
    ],
  },

  workspace_join: {
    id: 'workpool.workspace_join',
    title: 'WorkPool: workspace_join 配置',
    description: 'Workspace Join Request：使用子号 AT 对母号 Workspace 发起 request 或 accept。AT 通常来自前置 sso_oauth。',
    fields: [
      { key: 'worker_concurrency.workspace_join', label: 'workspace_join 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.workspace_join.workspace_ids', label: '默认 Workspace ID 列表', placeholder: '多个用换行/逗号分隔；也可创建工作流时填写' },
      {
        key: 'workpool.workspace_join.route',
        label: '默认动作',
        type: 'select',
        options: [
          { value: 'request', label: 'request 主动申请加入' },
          { value: 'accept', label: 'accept 接受已有邀请' },
        ],
      },
      { key: 'workpool.workspace_join.interval_ms', label: 'Workspace 间隔毫秒', type: 'number', placeholder: '1500' },
      { key: 'workpool.workspace_join.max_retries', label: '最大重试次数', type: 'number', placeholder: '3' },
      { key: 'workpool.workspace_join.retry_backoff_ms', label: '重试退避毫秒', type: 'number', placeholder: '5000' },
      { key: 'workpool.workspace_join.refresh_before_request', label: '请求前刷新 AT', type: 'switch' },
      { key: 'workpool.workspace_join.allow_partial', label: '允许部分成功', type: 'switch' },
      { key: 'workpool.workspace_join.switch_after_join', label: '加入成功后切换到该 Workspace', type: 'switch' },
      { key: 'workpool.workspace_join.proxy_provider', label: '代理厂商', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'workpool.workspace_join.proxy_region', label: '代理 region', placeholder: 'US / JP / BR' },
      { key: 'workpool.workspace_join.proxy_ttl', label: '代理持续时间 t', type: 'number', placeholder: '默认 5' },
      { key: 'workpool.workspace_join.proxy_url', label: '显式代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空复用任务/账号代理' },
      { key: 'workpool.workspace_join.acquire_proxy', label: '无显式代理时从 proxy_pool 领取', type: 'switch' },
    ],
  },
  active: {
    id: 'workpool.active',
    title: 'WorkPool: active 配置',
    description: 'Codex 激活池：接在 sso_oauth 后，模拟 Codex Desktop 协议请求完成受邀账号激活。',
    fields: [
      { key: 'worker_concurrency.active', label: 'active 并发', type: 'number', placeholder: '默认 3' },
      { key: 'workpool.active.proxy_provider', label: '代理厂商', placeholder: '留空复用任务/账号代理' },
      { key: 'workpool.active.proxy_region', label: '代理 region', placeholder: 'US / JP / BR' },
      { key: 'workpool.active.proxy_ttl', label: '代理持续时间 t', type: 'number', placeholder: '默认 5' },
      { key: 'workpool.active.proxy_url', label: '显式代理 URL', type: 'proxy_select', placeholder: '从代理池选择；留空复用任务/账号代理' },
      { key: 'workpool.active.acquire_proxy', label: '无显式代理时从 proxy_pool 领取', type: 'switch' },
      { key: 'workpool.active.refresh_before_activation', label: '激活前刷新 access_token', type: 'switch' },
      { key: 'workpool.active.dry_run', label: '默认 dry-run', type: 'switch' },
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
    description: 'PayPal 手机号可复用：失败号码冷却到期后会自动重新进入候选。',
    fields: [
      { key: 'paypal_number_cooldown_seconds', label: '失败冷却秒数', type: 'number', placeholder: '默认 300（5 分钟）' },
    ],
  },
  proxy_pool: {
    id: 'resource.proxy_pool',
    title: 'ResourcePool: proxy_pool 配置',
    description: '代理资源本身在代理页维护；这里仅保留全局 fallback。账号链路优先使用注册时绑定的代理。',
    fields: [
      { key: 'default_proxy_enabled', label: '启用全局默认代理', type: 'switch' },
      { key: 'default_proxy_provider', label: '默认动态代理厂商', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'default_proxy_region', label: '默认动态代理地区', placeholder: 'US' },
      { key: 'default_proxy_ttl', label: '默认动态代理持续时间 t', type: 'number', placeholder: '5' },
      { key: 'default_proxy_url', label: '全局固定代理 URL 兜底', placeholder: '动态 provider 未配置时才用' },
      { key: 'proxy_provider.default', label: 'Provider 默认名', type: 'select', options: PROXY_PROVIDER_OPTIONS },
      { key: 'proxy_provider.arxlabs.enabled', label: 'arxlabs 启用', type: 'switch' },
      { key: 'proxy_provider.arxlabs.scheme', label: 'arxlabs scheme', placeholder: 'http' },
      { key: 'proxy_provider.arxlabs.host', label: 'arxlabs host', placeholder: 'us.arxlabs.io' },
      { key: 'proxy_provider.arxlabs.port', label: 'arxlabs port', type: 'number', placeholder: '3010' },
      { key: 'proxy_provider.arxlabs.username_template', label: 'arxlabs 用户名模板', placeholder: 'jnej1150915-region-{region}-sid-{sid}-t-{ttl}' },
      { key: 'proxy_provider.arxlabs.password', label: 'arxlabs 密码', type: 'password' },
      { key: 'proxy_provider.arxlabs.default_region', label: 'arxlabs 默认地区', placeholder: 'US' },
      { key: 'proxy_provider.arxlabs.default_ttl', label: 'arxlabs 默认持续时间 t', type: 'number', placeholder: '5' },
      { key: 'proxy_provider.arxlabs.sid_length', label: 'arxlabs sid 长度', type: 'number', placeholder: '8' },
      { key: 'proxy_provider.1024proxy.enabled', label: '1024proxy 启用', type: 'switch' },
      { key: 'proxy_provider.1024proxy.scheme', label: '1024proxy scheme', placeholder: 'socks5' },
      { key: 'proxy_provider.1024proxy.host', label: '1024proxy host', placeholder: 'us.1024proxy.io' },
      { key: 'proxy_provider.1024proxy.port', label: '1024proxy port', type: 'number', placeholder: '3000' },
      { key: 'proxy_provider.1024proxy.username_template', label: '1024proxy 用户名模板', placeholder: 'jbgk38874-region-{region}-sid-{sid}-t-{ttl}' },
      { key: 'proxy_provider.1024proxy.password', label: '1024proxy 密码', type: 'password' },
      { key: 'proxy_provider.1024proxy.default_region', label: '1024proxy 默认地区', placeholder: 'US' },
      { key: 'proxy_provider.1024proxy.default_ttl', label: '1024proxy 默认持续时间 t', type: 'number', placeholder: '5' },
      { key: 'proxy_provider.1024proxy.sid_length', label: '1024proxy sid 长度', type: 'number', placeholder: '8' },
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
