import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, InputNumber, Select, Space, Switch, Typography, message } from 'antd'
import { SaveOutlined } from '@ant-design/icons'

import { apiFetch } from '@/lib/api'

type FieldType = 'text' | 'switch' | 'number' | 'select' | 'password'

interface SettingField {
  key: string
  label: string
  type?: FieldType
  placeholder?: string
  options?: { value: string; label: string }[]
}

const SETTING_FIELDS: SettingField[] = [
  { key: 'worker_concurrency', label: '并发 worker 数', type: 'number', placeholder: '默认 3' },
  { key: 'default_proxy_enabled', label: '启用全局默认代理', type: 'switch' },
  { key: 'default_proxy_url', label: '全局默认代理', placeholder: 'http://user:pass@host:port' },
  { key: 'email_domain_rule_enabled', label: '启用邮箱域名规则', type: 'switch' },
  { key: 'email_domain_level_count', label: '邮箱域名级数', type: 'number', placeholder: '2' },
  { key: 'email_poll_interval_seconds', label: '邮件轮询间隔(秒)', type: 'number', placeholder: '5' },
  { key: 'phone_verification_enabled', label: '全局开启 add-phone 接码', type: 'switch' },
  {
    key: 'phone_verification_provider',
    label: '接码平台',
    type: 'select',
    options: [
      { value: 'smsbower', label: 'SmsBower' },
      { value: 'fivesim', label: '5SIM' },
      { value: 'smsgiare', label: 'SmsGiaRe' },
    ],
  },
  { key: 'phone_verification_use_proxy', label: '接码平台 API 走注册代理', type: 'switch' },
  { key: 'phone_verification_max_attempts', label: '手机接码最大取号次数', type: 'number', placeholder: '3' },
  { key: 'phone_verification_poll_timeout_seconds', label: '手机接码等待秒数', type: 'number', placeholder: '180' },
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
  { key: 'smsbower_api_key', label: 'SmsBower API Key', type: 'password' },
  { key: 'smsbower_base_url', label: 'SmsBower API 地址', placeholder: 'https://smsbower.page/stubs/handler_api.php' },
  { key: 'smsbower_service', label: 'SmsBower 服务代码', placeholder: 'dr' },
  { key: 'smsbower_country', label: 'SmsBower 国家 ID', type: 'number', placeholder: '0' },
  { key: 'fivesim_api_key', label: '5SIM API Key', type: 'password' },
  { key: 'fivesim_service', label: '5SIM 服务代码', placeholder: 'openai' },
  { key: 'fivesim_country', label: '5SIM 国家', placeholder: 'any' },
  { key: 'fivesim_operator', label: '5SIM 运营商', placeholder: 'any' },
  { key: 'fivesim_max_price', label: '5SIM 最高价格', type: 'number', placeholder: '0' },
]

const FIELD_BY_KEY = Object.fromEntries(SETTING_FIELDS.map((field) => [field.key, field]))

function isTruthy(value: unknown): boolean {
  return ['1', 'true', 'yes', 'on', 'enabled'].includes(String(value || '').trim().toLowerCase())
}

function toFormValues(data: Record<string, string>) {
  const values: Record<string, unknown> = { ...data }
  for (const field of SETTING_FIELDS) {
    const raw = data[field.key]
    if (field.type === 'switch') values[field.key] = isTruthy(raw)
    if (field.type === 'number' && raw !== undefined && raw !== '') {
      const parsed = Number(raw)
      values[field.key] = Number.isFinite(parsed) ? parsed : undefined
    }
  }
  return values
}

function toSettingsValues(values: Record<string, unknown>) {
  const data: Record<string, string> = {}
  for (const [key, value] of Object.entries(values)) {
    const field = FIELD_BY_KEY[key]
    if (field?.type === 'switch') {
      data[key] = value ? '1' : '0'
    } else if (value === undefined || value === null) {
      data[key] = ''
    } else {
      data[key] = String(value)
    }
  }
  return data
}

function renderControl(field: SettingField) {
  if (field.type === 'switch') {
    return <Switch checkedChildren="开" unCheckedChildren="关" />
  }
  if (field.type === 'number') {
    return <InputNumber style={{ width: '100%' }} placeholder={field.placeholder || ''} />
  }
  if (field.type === 'select') {
    return <Select options={field.options || []} placeholder={field.placeholder || ''} />
  }
  if (field.type === 'password') {
    return <Input.Password placeholder={field.placeholder || ''} autoComplete="new-password" />
  }
  return <Input placeholder={field.placeholder || ''} />
}

export default function Settings() {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const initial = setTimeout(() => {
      apiFetch<Record<string, string>>('/settings').then((data) => {
        form.setFieldsValue(toFormValues(data))
      })
    }, 0)
    return () => clearTimeout(initial)
  }, [form])

  const submit = async () => {
    setLoading(true)
    try {
      const values = form.getFieldsValue()
      await apiFetch('/settings', { method: 'PUT', body: JSON.stringify({ data: toSettingsValues(values) }) })
      message.success('已保存')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Typography.Paragraph type="secondary">
          开关类配置会保存为 1/0；数字和文本配置会保存为字符串。改 worker 并发后请重启后端生效。
        </Typography.Paragraph>
        <Form form={form} layout="vertical">
          {SETTING_FIELDS.map((item) => (
            <Form.Item
              key={item.key}
              label={item.label}
              name={item.key}
              valuePropName={item.type === 'switch' ? 'checked' : undefined}
            >
              {renderControl(item)}
            </Form.Item>
          ))}
        </Form>
        <Button type="primary" icon={<SaveOutlined />} onClick={submit} loading={loading}>
          保存
        </Button>
      </Space>
    </Card>
  )
}
