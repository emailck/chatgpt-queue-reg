import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Checkbox, Col, Form, Input, InputNumber, Row, Select, Space, Table, Tag, Typography, message } from 'antd'
import { BugOutlined, ReloadOutlined } from '@ant-design/icons'

import { apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

interface Session {
  id: number
  status: string
  is_alive: boolean
  target_url: string
  browser_type: string
  proxy_url: string
  user_agent: string
  har_path: string
  account_id: number | null
  payment_link_id: number | null
  pipeline_id: number | null
  job_id: number | null
  error: string
  created_at: string | null
  updated_at: string | null
  closed_at: string | null
}

export default function BrowserDebug() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<Session[]>('/browser-debug/sessions')
      setSessions(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 4000)
    return () => clearInterval(t)
  }, [reload])

  const submit = async () => {
    const values = await form.validateFields()
    const body = {
      target_url: values.target_url || null,
      account_id: values.account_id || null,
      payment_link_id: values.payment_link_id || null,
      proxy_url: values.proxy_url || null,
      browser_type: values.browser_type || 'camoufox',
      inject_cookies: values.inject_cookies ?? true,
      inject_local_storage: values.inject_local_storage ?? true,
      inject_fingerprint: values.inject_fingerprint ?? true,
      record_har: values.record_har ?? true,
      omit_har_content: !!values.omit_har_content,
    }
    try {
      const resp = await apiFetch<{ session_id: number; har_path: string }>('/browser-debug/open', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      message.success(`session #${resp.session_id} 已打开 (HAR: ${resp.har_path || 'off'})`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '调起失败')
    }
  }

  const closeSession = async (id: number) => {
    try {
      await apiFetch(`/browser-debug/sessions/${id}/close`, { method: 'POST' })
      message.success('已请求关闭')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '关闭失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '状态',
      render: (_v: unknown, row: Session) => (
        <Space>
          <Tag color={row.is_alive ? 'green' : 'default'}>{row.is_alive ? '运行中' : row.status}</Tag>
        </Space>
      ),
    },
    { title: '类型', dataIndex: 'browser_type', width: 100 },
    { title: 'URL', dataIndex: 'target_url', ellipsis: true },
    { title: '代理', dataIndex: 'proxy_url', ellipsis: true },
    { title: 'HAR', dataIndex: 'har_path', ellipsis: true },
    {
      title: '关联',
      render: (_v: unknown, row: Session) => (
        <Space size={4}>
          {row.account_id && <Tag color="cyan">账号 #{row.account_id}</Tag>}
          {row.payment_link_id && <Tag color="purple">长链 #{row.payment_link_id}</Tag>}
          {row.pipeline_id && <Tag color="blue">pipeline #{row.pipeline_id}</Tag>}
          {row.job_id && <Tag color="default">job #{row.job_id}</Tag>}
        </Space>
      ),
    },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (value: string) => (value ? <Text type="danger">{value}</Text> : <Text type="secondary">-</Text>),
    },
    { title: '打开时间', render: (_v: unknown, row: Session) => formatDateTime(row.created_at) },
    {
      title: '操作',
      width: 100,
      render: (_v: unknown, row: Session) =>
        row.is_alive ? (
          <Button size="small" danger onClick={() => closeSession(row.id)}>关闭</Button>
        ) : null,
    },
  ] as const

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="调起 Camoufox / Chromium" extra={<Button icon={<ReloadOutlined />} onClick={reload}>刷新会话</Button>}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            browser_type: 'camoufox',
            inject_cookies: true,
            inject_local_storage: true,
            inject_fingerprint: true,
            record_har: true,
          }}
        >
          <Row gutter={12}>
            <Col span={16}>
              <Form.Item name="target_url" label="目标 URL">
                <Input placeholder="留空则使用 payment_link.checkout_url 或 https://chatgpt.com/" />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item name="account_id" label="账号 ID">
                <InputNumber style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item name="payment_link_id" label="长链 ID">
                <InputNumber style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="proxy_url" label="代理 URL（覆盖账号默认代理）">
                <Input />
              </Form.Item>
            </Col>
            <Col span={4}>
              <Form.Item name="browser_type" label="浏览器">
                <Select options={[{ value: 'camoufox', label: 'Camoufox' }, { value: 'chromium', label: 'Chromium' }]} />
              </Form.Item>
            </Col>
            <Col span={3}><Form.Item name="inject_cookies" valuePropName="checked"><Checkbox>注入 Cookies</Checkbox></Form.Item></Col>
            <Col span={3}><Form.Item name="inject_local_storage" valuePropName="checked"><Checkbox>注入 LocalStorage</Checkbox></Form.Item></Col>
            <Col span={3}><Form.Item name="inject_fingerprint" valuePropName="checked"><Checkbox>注入 UA / 指纹</Checkbox></Form.Item></Col>
            <Col span={3}><Form.Item name="record_har" valuePropName="checked"><Checkbox>记录 HAR</Checkbox></Form.Item></Col>
          </Row>
          <Form.Item>
            <Button type="primary" icon={<BugOutlined />} onClick={submit}>打开浏览器</Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="调试浏览器会话">
        <Table rowKey="id" dataSource={sessions} columns={columns as never} loading={loading} pagination={false} />
      </Card>
    </Space>
  )
}
