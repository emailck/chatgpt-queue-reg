import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Popconfirm, Progress, Space, Table, Tag, Typography, message } from 'antd'
import { KeyOutlined, ReloadOutlined } from '@ant-design/icons'

import { CopyButton } from '@/components/CopyButton'
import { StatusTag } from '@/components/StatusTag'
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

const SUB2API_STATUS_COLORS: Record<string, string> = {
  uploaded: 'blue',
  active: 'green',
  alive: 'green',
  ok: 'green',
  pending_upload: 'orange',
  upload_failed: 'red',
  sync_failed: 'red',
  dead: 'red',
  disabled: 'default',
  invalid: 'red',
  expired: 'red',
}

export default function SubscriptionAccounts() {
  const [rows, setRows] = useState<SubscriptionAccount[]>([])
  const [loading, setLoading] = useState(false)

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

  const fetchRefreshToken = async (row: SubscriptionAccount) => {
    if (!row.id) return
    try {
      const resp = await apiFetch<{ job_id: number; already_running: boolean }>(`/accounts/${row.id}/refresh-token`, {
        method: 'POST',
      })
      message.success(resp.already_running ? `RT 获取 job #${resp.job_id} 已在运行` : `已派发 RT 获取 job #${resp.job_id}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '派发失败')
    }
  }

  const columns = [
    { title: 'Pipeline', dataIndex: 'subscription_pipeline_id', width: 90, render: (v: number) => `#${v}` },
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string) => value ? (
        <Space size={2}>
          <Text>{value}</Text>
          <CopyButton value={value} />
        </Space>
      ) : <Text type="secondary">注册中</Text>,
    },
    {
      title: '注册状态',
      dataIndex: 'status',
      width: 120,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '订阅进度',
      width: 180,
      render: (_v: unknown, row: SubscriptionAccount) => (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Space size={4}>
            <StatusTag status={row.subscription_status} />
            <Text>{stageLabel(row.subscription_current_stage)}</Text>
          </Space>
          <Progress
            percent={Math.round((row.subscription_completed_steps / Math.max(row.subscription_total_steps, 1)) * 100)}
            size="small"
            format={() => `${row.subscription_completed_steps}/${row.subscription_total_steps}`}
            status={row.subscription_status === 'failed' ? 'exception' : row.subscription_status === 'succeeded' ? 'success' : 'active'}
          />
        </Space>
      ),
    },
    {
      title: '支付长链',
      dataIndex: 'last_payment_link_url',
      render: (value: string, row: SubscriptionAccount) => value ? (
        <Space>
          <a href={value} target="_blank" rel="noopener noreferrer">打开</a>
          <CopyButton value={value} />
        </Space>
      ) : row.payment_link_error ? <Text type="danger">失败</Text> : <Text type="secondary">-</Text>,
    },
    {
      title: 'Token',
      width: 150,
      render: (_v: unknown, row: SubscriptionAccount) => (
        <Space size={4} wrap>
          {row.has_access_token && <Tag color="green">AT</Tag>}
          {row.codex_token_has_refresh_token ? <Tag color="blue">Codex RT</Tag> : <Tag>无 RT</Tag>}
          {row.codex_token_id && !row.codex_token_alive && <Tag color="red">失效</Tag>}
        </Space>
      ),
    },
    {
      title: 'sub2api RT',
      width: 190,
      render: (_v: unknown, row: SubscriptionAccount) => row.codex_token_id ? (
        <Space direction="vertical" size={2}>
          <Space size={4} wrap>
            <Text>#{row.codex_token_id}</Text>
            <Tag color={SUB2API_STATUS_COLORS[row.sub2api_status] || 'default'}>{row.sub2api_status || 'unknown'}</Tag>
          </Space>
          {row.sub2api_external_id && <Text type="secondary" ellipsis style={{ maxWidth: 170 }}>{row.sub2api_external_id}</Text>}
          {row.codex_token_last_error && <Text type="danger" ellipsis style={{ maxWidth: 170 }}>{row.codex_token_last_error}</Text>}
        </Space>
      ) : row.refresh_token_job_id ? (
        <Space direction="vertical" size={2}>
          <Space size={4}>
            <Text>job #{row.refresh_token_job_id}</Text>
            <StatusTag status={row.refresh_token_job_status} />
          </Space>
          {row.refresh_token_job_error && <Text type="danger" ellipsis style={{ maxWidth: 170 }}>{row.refresh_token_job_error}</Text>}
        </Space>
      ) : <Text type="secondary">未获取</Text>,
    },
    { title: '代理', dataIndex: 'proxy_url', ellipsis: true, width: 180 },
    { title: '创建时间', width: 170, render: (_v: unknown, row: SubscriptionAccount) => formatDateTime(row.created_at) },
    {
      title: '操作',
      width: 150,
      render: (_v: unknown, row: SubscriptionAccount) => (
        <Space size={4}>
          <Popconfirm
            title="为该订阅号获取 RT?"
            onConfirm={() => fetchRefreshToken(row)}
            disabled={!row.id || row.codex_token_has_refresh_token}
          >
            <Button size="small" icon={<KeyOutlined />} disabled={!row.id || row.codex_token_has_refresh_token}>
              获取 RT
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ] as const

  return (
    <Card>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
        <Tag>共 {rows.length} 条</Tag>
      </Space>
      <Table
        rowKey={(row) => row.subscription_pipeline_id}
        dataSource={rows}
        columns={columns as never}
        loading={loading}
        pagination={{ pageSize: 20 }}
      />
    </Card>
  )
}
