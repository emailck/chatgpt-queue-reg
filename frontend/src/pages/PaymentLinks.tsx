import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Form, Input, Popconfirm, Space, Tag, Typography, message } from 'antd'
import { BugOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { ActionCard, CardToolbar, EntityCard, EntityGrid, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges, SelectionSummary, UrlAction } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

interface PaymentLink {
  id: number
  account_id: number
  pipeline_id: number | null
  job_id: number | null
  plan: string
  promo_code: string
  checkout_url: string
  checkout_session_id: string
  status: string
  error: string
  created_at: string | null
  updated_at: string | null
}

function linkTone(row: PaymentLink): 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (row.error || row.status === 'failed') return 'danger'
  if (row.status === 'ready' || row.status === 'open') return 'success'
  if (row.status === 'opening') return 'info'
  return 'default'
}

export default function PaymentLinks() {
  const [rows, setRows] = useState<PaymentLink[]>([])
  const [loading, setLoading] = useState(false)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [paymentTarget, setPaymentTarget] = useState<PaymentLink | null>(null)
  const [paymentLoading, setPaymentLoading] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [page, setPage] = useState(1)
  const [paymentForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<PaymentLink[]>('/payment-links?limit=300')
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    const t = setInterval(reload, 6000)
    return () => {
      clearTimeout(initial)
      clearInterval(t)
    }
  }, [reload])

  const summary = useMemo(() => ({
    total: rows.length,
    ready: rows.filter((row) => row.status === 'ready' || row.status === 'open').length,
    failed: rows.filter((row) => row.status === 'failed' || row.error).length,
    plus: rows.filter((row) => row.plan === 'plus').length,
    team: rows.filter((row) => row.plan === 'team').length,
  }), [rows])

  const debugBrowser = async (row: PaymentLink) => {
    try {
      const resp = await apiFetch<{ session_id: number; har_path: string }>(`/payment-links/${row.id}/debug-browser`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      message.success(`Camoufox session #${resp.session_id} 已打开${resp.har_path ? `，HAR: ${resp.har_path}` : ''}`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '调起失败')
    }
  }

  const openPayment = (row: PaymentLink) => {
    setPaymentTarget(row)
    paymentForm.setFieldsValue({ payment_proxy_region: '' })
  }

  const triggerPayment = async () => {
    if (!paymentTarget) return
    const values = await paymentForm.validateFields()
    setPaymentLoading(true)
    try {
      const resp = await apiFetch<{ job_id: number }>(`/payment-links/${paymentTarget.id}/payment`, {
        method: 'POST',
        body: JSON.stringify({ payment_proxy_region: values.payment_proxy_region }),
      })
      message.success(`已派发支付 job #${resp.job_id}`)
      setPaymentTarget(null)
      paymentForm.resetFields()
      setLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '请求失败')
    } finally {
      setPaymentLoading(false)
    }
  }

  const deleteOne = async (row: PaymentLink) => {
    try {
      await apiFetch(`/payment-links/${row.id}`, { method: 'DELETE' })
      message.success('已删除')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/payment-links/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ ids: selected.map((id) => Number(id)) }),
      })
      message.success(`已删除 ${resp.deleted}`)
      setSelected([])
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '批量删除失败')
    }
  }

  const toggleSelected = (id: number, checked: boolean) => {
    setSelected((prev) => checked ? [...prev, id] : prev.filter((item) => Number(item) !== id))
  }

  return (
    <PageScaffold
      title="支付长链"
      description="Payment link 按长链卡片展示 checkout URL、session、套餐、关联账号与支付调试入口。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="总数" value={summary.total} tone="primary" />
        <StatCard label="ready/open" value={summary.ready} tone="success" />
        <StatCard label="failed" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="Plus" value={summary.plus} tone="info" />
        <StatCard label="Team" value={summary.team} tone="info" />
        <StatCard label="已选择" value={selected.length} tone={selected.length ? 'warning' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="长链操作"
        description="抓 HAR、支付和删除仍沿用原接口；支付会提交 payment_proxy_region，PayPal 号码由 paypal_number_pool 领取。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>
            <Popconfirm title={`确认删除选中的 ${selected.length} 条?`} onConfirm={batchDelete} disabled={!selected.length}>
              <Button icon={<DeleteOutlined />} danger disabled={!selected.length}>批量删除</Button>
            </Popconfirm>
          </CardToolbar>
        )}
      />

      <EntityGrid
        items={rows}
        page={page}
        pageSize={18}
        onPageChange={setPage}
        renderItem={(row) => (
          <EntityCard
            key={row.id}
            title={`Payment Link #${row.id}`}
            subtitle={<CopyableText value={row.checkout_session_id} label="checkout session" code />}
            status={<StatusTag status={row.status} />}
            tone={linkTone(row)}
            selected={selected.includes(row.id)}
            onSelect={(checked) => toggleSelected(row.id, checked)}
            badges={(
              <Space size={4} wrap>
                <Tag color={row.plan === 'plus' ? 'magenta' : 'blue'}>{(row.plan || '-').toUpperCase()}</Tag>
                <LinkedIdBadges pipelineId={row.pipeline_id} accountId={row.account_id} jobId={row.job_id} />
                {row.promo_code && <Tag color="gold">promo {row.promo_code}</Tag>}
              </Space>
            )}
            footer={formatDateTime(row.created_at)}
            actions={(
              <>
                <Button size="small" icon={<BugOutlined />} onClick={() => debugBrowser(row)}>抓 HAR</Button>
                <Button size="small" type="dashed" onClick={() => openPayment(row)}>支付</Button>
                <Popconfirm title="删除该长链记录?" onConfirm={() => deleteOne(row)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </>
            )}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <KeyValueGrid>
                <KeyValue label="Checkout URL" value={<UrlAction url={row.checkout_url} />} />
                <KeyValue label="Session ID" value={<CopyableText value={row.checkout_session_id} label="session" code />} />
                <KeyValue label="更新时间" value={formatDateTime(row.updated_at)} />
                <KeyValue label="账号" value={<Text>#{row.account_id}</Text>} />
              </KeyValueGrid>
              <ErrorCallout error={row.error} />
            </Space>
          </EntityCard>
        )}
      />

      <PopupCard
        open={!!paymentTarget}
        title={paymentTarget ? `派发支付任务 #${paymentTarget.id}` : ''}
        onCancel={() => { if (!paymentLoading) { setPaymentTarget(null); paymentForm.resetFields() } }}
        onOk={triggerPayment}
        okText="派发"
        confirmLoading={paymentLoading}
        maskClosable={!paymentLoading}
        closable={!paymentLoading}
        width={560}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Paragraph type="secondary">支付阶段会按 region 从 proxy_pool 领取与账号注册代理不同的支付代理，并从 paypal_number_pool 领取 PayPal 手机号资源。</Typography.Paragraph>
          <Form form={paymentForm} layout="vertical">
            <Form.Item name="payment_proxy_region" label="支付代理 region" rules={[{ required: true, message: '请输入 payment_proxy_region' }]}>
              <Input placeholder="例如 US / ID" />
            </Form.Item>
          </Form>
        </Space>
      </PopupCard>

      <PopupCard open={logJobId !== null} onCancel={() => setLogJobId(null)} width={900} title={logJobId ? `Job #${logJobId} 原始日志` : ''} footer={null}>
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </PopupCard>
    </PageScaffold>
  )
}
