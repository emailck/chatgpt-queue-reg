import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Popconfirm, Progress, Space, Tag, Typography, message } from 'antd'
import { KeyOutlined, ReloadOutlined } from '@ant-design/icons'

import { StatusTag } from '@/components/StatusTag'
import { ActionCard, CardToolbar, EntityCard, EntityGrid, KeyValue, KeyValueGrid, PageScaffold, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges, Sub2ApiBadge, TokenBadges, UrlAction } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime } from '@/lib/api'
import { stageLabel } from '@/lib/contracts'

const { Text } = Typography

interface SubscriptionAccount {
  id: number | null
  email: string
  password: string
  status: string
  account_id: string
  workspace_id: string
  proxy_url: string
  last_error: string
  last_payment_link_id: number | null
  last_payment_link_url: string
  has_access_token: boolean
  has_refresh_token: boolean
  has_session_token: boolean
  codex_token_id: number | null
  codex_token_alive: boolean
  codex_token_has_refresh_token: boolean
  codex_token_last_error: string
  sub2api_external_id: string
  sub2api_status: string
  sub2api_uploaded_at: string | null
  sub2api_status_checked_at: string | null
  created_at: string | null
  registered_at: string | null
  updated_at: string | null
  subscription_pipeline_id: number
  subscription_status: string
  subscription_current_stage: string
  subscription_completed_steps: number
  subscription_total_steps: number
  payment_link_status: string
  payment_link_error: string
  refresh_token_job_id: number | null
  refresh_token_job_status: string
  refresh_token_job_error: string
}

function tone(row: SubscriptionAccount): 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (row.last_error || row.payment_link_error || row.refresh_token_job_error || row.subscription_status === 'failed') return 'danger'
  if (row.subscription_status === 'succeeded') return 'success'
  if (row.subscription_status === 'running') return 'info'
  if (row.subscription_status === 'queued') return 'warning'
  return 'default'
}

export default function SubscriptionAccounts() {
  const [rows, setRows] = useState<SubscriptionAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<SubscriptionAccount[]>('/accounts/subscriptions?limit=500')
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    const t = setInterval(reload, 5000)
    return () => {
      clearTimeout(initial)
      clearInterval(t)
    }
  }, [reload])

  const summary = useMemo(() => ({
    total: rows.length,
    running: rows.filter((row) => row.subscription_status === 'running').length,
    queued: rows.filter((row) => row.subscription_status === 'queued').length,
    succeeded: rows.filter((row) => row.subscription_status === 'succeeded').length,
    failed: rows.filter((row) => row.subscription_status === 'failed' || row.last_error || row.payment_link_error).length,
    paymentReady: rows.filter((row) => row.last_payment_link_url || row.payment_link_status === 'ready').length,
    rt: rows.filter((row) => row.codex_token_has_refresh_token).length,
  }), [rows])

  const fetchRefreshToken = async (row: SubscriptionAccount) => {
    if (!row.id) return
    try {
      const resp = await apiFetch<{ job_id: number; already_running: boolean }>(`/accounts/${row.id}/refresh-token`, { method: 'POST' })
      message.success(resp.already_running ? `RT 获取 job #${resp.job_id} 已在运行` : `已派发 RT 获取 job #${resp.job_id}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '派发失败')
    }
  }

  return (
    <PageScaffold
      title="订阅号池"
      description="订阅账号按 pipeline 卡片展示完整链路进度、支付长链、Codex RT 和 sub2api 状态。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="总数" value={summary.total} tone="primary" />
        <StatCard label="queued" value={summary.queued} tone="warning" />
        <StatCard label="running" value={summary.running} tone="info" />
        <StatCard label="succeeded" value={summary.succeeded} tone="success" />
        <StatCard label="failed" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="长链 ready" value={summary.paymentReady} tone="info" />
        <StatCard label="Codex RT" value={summary.rt} tone="success" />
      </SummaryGrid>

      <ActionCard
        title="订阅链路"
        description="这里是订阅账号的结果池视图；获取 RT 仍走账号边界接口，轮询保持 5 秒。"
        actions={<CardToolbar><Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新状态</Button></CardToolbar>}
      />

      <EntityGrid
        items={rows}
        page={page}
        pageSize={18}
        onPageChange={setPage}
        renderItem={(row) => (
          <EntityCard
            key={row.subscription_pipeline_id}
            title={row.email ? <CopyableText value={row.email} label="邮箱" /> : '注册中'}
            subtitle={`Pipeline #${row.subscription_pipeline_id}`}
            status={<StatusTag status={row.subscription_status} />}
            tone={tone(row)}
            badges={(
              <Space size={4} wrap>
                <LinkedIdBadges pipelineId={row.subscription_pipeline_id} accountId={row.id} paymentLinkId={row.last_payment_link_id} />
                <TokenBadges accessToken={row.has_access_token ? 'yes' : ''} refreshToken={row.has_refresh_token ? 'yes' : ''} codexRt={row.codex_token_has_refresh_token ? 'yes' : ''} />
                {row.sub2api_status && <Sub2ApiBadge status={row.sub2api_status} />}
              </Space>
            )}
            footer={formatDateTime(row.created_at)}
            actions={(
              <Popconfirm title="为该订阅号获取 RT?" onConfirm={() => fetchRefreshToken(row)} disabled={!row.id || row.codex_token_has_refresh_token}>
                <Button size="small" icon={<KeyOutlined />} disabled={!row.id || row.codex_token_has_refresh_token}>获取 RT</Button>
              </Popconfirm>
            )}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <Progress
                percent={Math.round((row.subscription_completed_steps / Math.max(row.subscription_total_steps, 1)) * 100)}
                size="small"
                format={() => `${row.subscription_completed_steps}/${row.subscription_total_steps}`}
                status={row.subscription_status === 'failed' ? 'exception' : row.subscription_status === 'succeeded' ? 'success' : 'active'}
              />
              <KeyValueGrid>
                <KeyValue label="当前 Stage" value={<Space size={4}><Text>{stageLabel(row.subscription_current_stage)}</Text><Text code type="secondary">{row.subscription_current_stage}</Text></Space>} />
                <KeyValue label="注册状态" value={<StatusTag status={row.status} />} />
                <KeyValue label="支付长链" value={<UrlAction url={row.last_payment_link_url} />} />
                <KeyValue label="payment status" value={row.payment_link_status || '-'} />
                <KeyValue label="sub2api external" value={<CopyableText value={row.sub2api_external_id} label="sub2api external" code />} />
                <KeyValue label="代理" value={<CopyableText value={row.proxy_url} label="代理" />} />
              </KeyValueGrid>
              {row.refresh_token_job_id && !row.codex_token_has_refresh_token && (
                <Tag color="processing">RT job #{row.refresh_token_job_id} / {row.refresh_token_job_status || '-'}</Tag>
              )}
              <ErrorCallout error={row.last_error || row.payment_link_error || row.codex_token_last_error || row.refresh_token_job_error} />
            </Space>
          </EntityCard>
        )}
      />
    </PageScaffold>
  )
}
