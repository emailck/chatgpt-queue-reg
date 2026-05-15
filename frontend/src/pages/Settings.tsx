import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, Space, Typography, message } from 'antd'
import { SaveOutlined } from '@ant-design/icons'

import { apiFetch } from '@/lib/api'

const KNOWN_KEYS: { key: string; label: string; placeholder?: string }[] = [
  { key: 'worker_concurrency', label: '并发 worker 数', placeholder: '默认 3' },
  { key: 'default_proxy_enabled', label: '启用全局默认代理 (1/0)', placeholder: '0' },
  { key: 'default_proxy_url', label: '全局默认代理', placeholder: 'http://user:pass@host:port' },
  { key: 'email_domain_rule_enabled', label: '启用邮箱域名规则 (1/0)', placeholder: '0' },
  { key: 'email_domain_level_count', label: '邮箱域名级数', placeholder: '2' },
  { key: 'email_poll_interval_seconds', label: '邮件轮询间隔(秒)', placeholder: '5' },
]

export default function Settings() {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    apiFetch<Record<string, string>>('/settings').then((data) => {
      form.setFieldsValue(data)
    })
  }, [form])

  const submit = async () => {
    setLoading(true)
    try {
      const values = form.getFieldsValue()
      await apiFetch('/settings', { method: 'PUT', body: JSON.stringify({ data: values }) })
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
          所有键都是字符串。改 worker 并发后请重启后端生效。其它键随取随用。
        </Typography.Paragraph>
        <Form form={form} layout="vertical">
          {KNOWN_KEYS.map((item) => (
            <Form.Item key={item.key} label={item.label} name={item.key}>
              <Input placeholder={item.placeholder || ''} />
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
