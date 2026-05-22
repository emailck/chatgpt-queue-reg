import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Checkbox, Col, Form, Input, InputNumber, Row, Select, Space, Tag, message } from 'antd'
import { BugOutlined, ReloadOutlined } from '@ant-design/icons'

import { ActionCard, CardToolbar, EntityCard, EntityGrid, KeyValue, KeyValueGrid, PageScaffold, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime } from '@/lib/api'

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
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(18)
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
    const initial = setTimeout(reload, 0)
    const t = setInterval(reload, 4000)
    return () => {
      clearTimeout(initial)
      clearInterval(t)
    }
  }, [reload])

  const summary = useMemo(() => ({
    total: sessions.length,
    alive: sessions.filter((row) => row.is_alive).length,
    closed: sessions.filter((row) => !row.is_alive || row.closed_at).length,
    errors: sessions.filter((row) => row.error).length,
    har: sessions.filter((row) => row.har_path).length,
  }), [sessions])

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

  return (
    <PageScaffold
      title="浏览器调试"
      description="Camoufox / Chromium 调试工作台：注入账号身份、代理、指纹和凭据，按 session 卡片追踪 HAR 与关闭状态。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新会话</Button>}
    >
      <SummaryGrid>
        <StatCard label="sessions" value={summary.total} tone="primary" />
        <StatCard label="alive" value={summary.alive} tone="success" />
        <StatCard label="closed" value={summary.closed} />
        <StatCard label="errors" value={summary.errors} tone={summary.errors ? 'danger' : 'default'} />
        <StatCard label="HAR recorded" value={summary.har} tone="info" />
      </SummaryGrid>

      <ActionCard title="启动浏览器" description="按 target、identity、injection、capture 分区组织字段；提交 payload 保持原样。">
        <Form form={form} layout="vertical" initialValues={{ browser_type: 'camoufox', inject_cookies: true, inject_local_storage: true, inject_fingerprint: true, record_har: true }}>
          <Row gutter={16}>
            <Col xs={24} lg={12}>
              <Form.Item name="target_url" label="目标 URL"><Input placeholder="留空则使用 payment_link.checkout_url 或 https://chatgpt.com/" /></Form.Item>
            </Col>
            <Col xs={12} lg={4}><Form.Item name="account_id" label="账号 ID"><InputNumber style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} lg={4}><Form.Item name="payment_link_id" label="长链 ID"><InputNumber style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={24} lg={4}><Form.Item name="browser_type" label="浏览器"><Select options={[{ value: 'camoufox', label: 'Camoufox' }, { value: 'chromium', label: 'Chromium' }]} /></Form.Item></Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} lg={8}><Form.Item name="proxy_url" label="代理 URL（覆盖账号默认代理）"><Input /></Form.Item></Col>
            <Col xs={12} lg={4}><Form.Item name="inject_cookies" valuePropName="checked"><Checkbox>注入 Cookies</Checkbox></Form.Item></Col>
            <Col xs={12} lg={4}><Form.Item name="inject_local_storage" valuePropName="checked"><Checkbox>注入 LocalStorage</Checkbox></Form.Item></Col>
            <Col xs={12} lg={4}><Form.Item name="inject_fingerprint" valuePropName="checked"><Checkbox>注入 UA / 指纹</Checkbox></Form.Item></Col>
            <Col xs={12} lg={4}><Form.Item name="record_har" valuePropName="checked"><Checkbox>记录 HAR</Checkbox></Form.Item></Col>
          </Row>
          <Button type="primary" icon={<BugOutlined />} onClick={submit}>打开浏览器</Button>
        </Form>
      </ActionCard>

      <ActionCard title="调试浏览器会话" actions={<CardToolbar><Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button></CardToolbar>} />

      <EntityGrid
        items={sessions}
        page={page}
        pageSize={pageSize}
        onPageChange={(nextPage, nextPageSize) => { setPage(nextPage); setPageSize(nextPageSize) }}
        showSizeChanger
        renderItem={(row) => (
          <EntityCard
            key={row.id}
            title={`Session #${row.id}`}
            subtitle={<CopyableText value={row.target_url} label="target url" />}
            status={<Tag color={row.is_alive ? 'green' : 'default'}>{row.is_alive ? '运行中' : row.status}</Tag>}
            tone={row.error ? 'danger' : row.is_alive ? 'success' : 'default'}
            badges={<LinkedIdBadges pipelineId={row.pipeline_id} accountId={row.account_id} paymentLinkId={row.payment_link_id} jobId={row.job_id} />}
            footer={formatDateTime(row.created_at)}
            actions={row.is_alive ? <Button size="small" danger onClick={() => closeSession(row.id)}>关闭</Button> : null}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <KeyValueGrid>
                <KeyValue label="Browser" value={row.browser_type} />
                <KeyValue label="代理" value={<CopyableText value={row.proxy_url} label="代理" />} />
                <KeyValue label="HAR" value={<CopyableText value={row.har_path} label="HAR" code />} />
                <KeyValue label="UA" value={<CopyableText value={row.user_agent} label="UA" />} />
                <KeyValue label="更新时间" value={formatDateTime(row.updated_at)} />
                <KeyValue label="关闭时间" value={formatDateTime(row.closed_at)} />
              </KeyValueGrid>
              <ErrorCallout error={row.error} />
            </Space>
          </EntityCard>
        )}
      />
    </PageScaffold>
  )
}
