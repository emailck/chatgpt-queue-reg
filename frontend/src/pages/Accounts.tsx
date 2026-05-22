import { useCallback, useEffect, useMemo, useState } from 'react'
import type { TableColumnsType } from 'antd'
import { Button, Dropdown, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { BugOutlined, CloudDownloadOutlined, CloudSyncOutlined, DeleteOutlined, MailOutlined, ReloadOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { ActionCard, CardToolbar, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, SelectionSummary, Sub2ApiBadge, TokenBadges } from '@/components/ui/DomainBits'
import { API_BASE, apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

type SoldFilter = 'all' | 'unsold' | 'sold'

function sub2apiError(value: string) {
  const text = String(value || '').trim()
  if (['success', 'ok', 'synced', 'active', 'alive'].includes(text.toLowerCase())) return ''
  return text
}

interface EmailHistoryMessage {
  id: number | string
  email: string
  provider: string
  subject: string
  sender: string
  body_text: string
  code: string
  received_at: string | null
  created_at: string | null
  folder?: string
}

interface Account {
  id: number
  email: string
  password: string
  status: string
  account_id: string
  workspace_id: string
  plan_type: string
  sold: boolean
  sold_at: string | null
  proxy_url: string
  last_error: string
  last_payment_link_id: number | null
  last_payment_link_url: string
  last_payment_link_status: string
  user_agent: string
  has_access_token: boolean
  has_refresh_token: boolean
  has_session_token: boolean
  refresh_token_id: number | null
  refresh_token_enabled: boolean
  refresh_token_has_token: boolean
  refresh_token_last_error: string
  sub2api_account_id: string
  sub2api_status: string
  sub2api_auth_mode: string
  sub2api_schedulable: boolean | null
  sub2api_relogin_required: boolean
  sub2api_last_error: string
  sub2api_uploaded_at: string | null
  sub2api_status_checked_at: string | null
  created_at: string | null
  registered_at: string | null
  updated_at: string | null
}

export default function Accounts() {
  const [rows, setRows] = useState<Account[]>([])
  const [loading, setLoading] = useState(false)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [emailModalAccount, setEmailModalAccount] = useState<Account | null>(null)
  const [emailHistory, setEmailHistory] = useState<EmailHistoryMessage[]>([])
  const [emailHistoryLoading, setEmailHistoryLoading] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [soldFilter, setSoldFilter] = useState<SoldFilter>('all')
  const [exportingSold, setExportingSold] = useState(false)
  const [refreshingSub2ApiStatus, setRefreshingSub2ApiStatus] = useState(false)
  const [refreshingAccessToken, setRefreshingAccessToken] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ paid_only: 'true', limit: '300' })
      if (soldFilter !== 'all') params.set('sold', soldFilter === 'sold' ? 'true' : 'false')
      const data = await apiFetch<Account[]>(`/accounts?${params.toString()}`)
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [soldFilter])

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
    unsold: rows.filter((row) => !row.sold).length,
    sold: rows.filter((row) => row.sold).length,
    schedulable: rows.filter((row) => row.sub2api_schedulable === true && !row.sub2api_relogin_required).length,
    failed: rows.filter((row) => row.sub2api_status === 'sync_failed' || !!sub2apiError(row.sub2api_last_error)).length,
    relogin: rows.filter((row) => row.sub2api_relogin_required).length,
  }), [rows])

  const showEmailHistory = async (account: Account) => {
    setEmailModalAccount(account)
    setEmailHistory([])
    setEmailHistoryLoading(true)
    try {
      const data = await apiFetch<EmailHistoryMessage[]>(`/accounts/${account.id}/email-history?limit=10`)
      setEmailHistory(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '读取邮件历史失败')
    } finally {
      setEmailHistoryLoading(false)
    }
  }

  const triggerDebugBrowser = async (account: Account) => {
    try {
      const resp = await apiFetch<{ session_id: number; har_path: string }>(`/accounts/${account.id}/debug-browser`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      message.success(`Camoufox session #${resp.session_id} 已打开${resp.har_path ? `，HAR: ${resp.har_path}` : ''}`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '调起浏览器失败')
    }
  }

  const triggerSub2ApiSync = async (account: Account) => {
    try {
      const resp = await apiFetch<{ job_id: number; already_running: boolean }>(`/accounts/${account.id}/sub2api-sync`, {
        method: 'POST',
      })
      message.success(resp.already_running ? `sub2api_sync job #${resp.job_id} 已在运行` : `已派发 sub2api_sync job #${resp.job_id}`)
      setLogJobId(resp.job_id)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'sub2api_sync 失败')
    }
  }

  const refreshSub2ApiStatus = async (ids: number[]) => {
    if (!ids.length) return
    setRefreshingSub2ApiStatus(true)
    try {
      const resp = await apiFetch<{ refreshed: number; failed: number }>('/accounts/sub2api-status-refresh', {
        method: 'POST',
        body: JSON.stringify({ ids }),
      })
      message.success(`已刷新 sub2api 状态 ${resp.refreshed} 个${resp.failed ? `，失败 ${resp.failed} 个` : ''}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '刷新 sub2api 状态失败')
    } finally {
      setRefreshingSub2ApiStatus(false)
    }
  }

  const refreshAccessToken = async (ids: number[]) => {
    if (!ids.length) return
    setRefreshingAccessToken(true)
    try {
      const resp = await apiFetch<{ enqueued: number; already_running: number; jobs: { job_id: number; already_running: boolean }[] }>('/accounts/access-token-refresh', {
        method: 'POST',
        body: JSON.stringify({ ids }),
      })
      message.success(`已派发 AT 刷新 ${resp.enqueued} 个${resp.already_running ? `，运行中 ${resp.already_running} 个` : ''}`)
      const firstJob = resp.jobs.find((job) => job.job_id)?.job_id
      if (firstJob) setLogJobId(firstJob)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '刷新 AT 失败')
    } finally {
      setRefreshingAccessToken(false)
    }
  }

  const deleteAccount = async (account: Account) => {
    try {
      await apiFetch(`/accounts/${account.id}`, { method: 'DELETE' })
      message.success(`已删除 ${account.email}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/accounts/batch-delete', {
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

  const exportSelectedSub2Api = async () => {
    if (!selected.length) return
    setExportingSold(true)
    try {
      const response = await fetch(`${API_BASE}/accounts/sub2api-export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: selected.map((id) => Number(id)), mark_sold: true }),
      })
      if (!response.ok) {
        const text = await response.text()
        let detail = text || response.statusText
        try {
          const data = JSON.parse(text)
          detail = String(data?.detail || detail)
        } catch {
          // keep raw detail
        }
        throw new Error(detail)
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'plus-sub2api-accounts.json'
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      message.success(`已导出并标记已售 ${selected.length} 个账号`)
      setSelected([])
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '导出失败')
    } finally {
      setExportingSold(false)
    }
  }

  const emailHistoryColumns: TableColumnsType<EmailHistoryMessage> = [
    {
      title: '时间',
      key: 'time',
      width: 170,
      render: (_, row) => formatDateTime(row.received_at || row.created_at),
    },
    {
      title: '主题',
      dataIndex: 'subject',
      width: 240,
      ellipsis: true,
      render: (value: string) => value || '无主题',
    },
    {
      title: '发件人',
      dataIndex: 'sender',
      width: 220,
      ellipsis: true,
      render: (value: string) => <CopyableText value={value} label="发件人" />,
    },
    {
      title: '文件夹',
      dataIndex: 'folder',
      width: 100,
      render: (value: string | undefined) => value || '-',
    },
    {
      title: '正文预览',
      dataIndex: 'body_text',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
  ]

  const columns: TableColumnsType<Account> = [
    {
      title: '账号',
      dataIndex: 'email',
      width: 280,
      fixed: 'left',
      render: (value: string, row) => (
        <Space direction="vertical" size={2} style={{ maxWidth: 260 }}>
          <CopyableText value={value} label="邮箱" />
          <Space size={4} wrap>
            <Text type="secondary">Account #{row.id}</Text>
            {row.last_payment_link_id && <Tag color="green">已支付</Tag>}
            <Tag color={row.sold ? 'default' : 'green'}>{row.sold ? '已售出' : '可售'}</Tag>
            {row.plan_type && <Tag color="blue">{row.plan_type}</Tag>}
          </Space>
          {row.sold && <Text type="secondary">售出 {formatDateTime(row.sold_at)}</Text>}
        </Space>
      ),
    },
    {
      title: 'sub2api 状态',
      key: 'sub2api_status',
      width: 260,
      render: (_, row) => (
        <Space direction="vertical" size={4}>
          <Space size={4} wrap>
            <Sub2ApiBadge status={row.sub2api_status} />
            {row.sub2api_auth_mode && <Tag color="blue">{row.sub2api_auth_mode}</Tag>}
            {row.sub2api_schedulable === true && !row.sub2api_relogin_required && <Tag color="green">schedulable</Tag>}
            {row.sub2api_schedulable === false && <Tag color="orange">unschedulable</Tag>}
            {row.sub2api_relogin_required && <Tag color="red">relogin</Tag>}
          </Space>
          <ErrorCallout error={sub2apiError(row.sub2api_last_error)} />
        </Space>
      ),
    },
    {
      title: 'sub2api ID',
      dataIndex: 'sub2api_account_id',
      width: 210,
      ellipsis: true,
      render: (value: string) => value ? <CopyableText value={value} label="sub2api ID" code /> : <Text type="secondary">待同步</Text>,
    },
    {
      title: '同步时间',
      key: 'sub2api_times',
      width: 190,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Text type="secondary">同步 {formatDateTime(row.sub2api_uploaded_at)}</Text>
          <Text type="secondary">检查 {formatDateTime(row.sub2api_status_checked_at)}</Text>
        </Space>
      ),
    },
    {
      title: '密码',
      dataIndex: 'password',
      width: 180,
      ellipsis: true,
      render: (value: string) => <CopyableText value={value} label="密码" code />,
    },
    {
      title: 'Token',
      key: 'tokens',
      width: 220,
      render: (_, row) => (
        <Space direction="vertical" size={4}>
          <TokenBadges accessToken={row.has_access_token ? 'yes' : ''} refreshToken={(row.has_refresh_token || row.refresh_token_has_token) ? 'yes' : ''} />
          {row.refresh_token_id && <Tag color={row.refresh_token_enabled ? 'blue' : 'red'}>RT #{row.refresh_token_id}</Tag>}
        </Space>
      ),
    },
    {
      title: 'OpenAI Account',
      dataIndex: 'account_id',
      width: 220,
      ellipsis: true,
      render: (value: string) => <CopyableText value={value} label="Account ID" code />,
    },
    {
      title: 'Workspace',
      dataIndex: 'workspace_id',
      width: 200,
      ellipsis: true,
      render: (value: string) => <CopyableText value={value} label="Workspace" code />,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 330,
      render: (_, row) => (
        <Space size={6} wrap>
          <Button size="small" icon={<CloudSyncOutlined />} onClick={() => refreshSub2ApiStatus([row.id])}>刷新状态</Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => refreshAccessToken([row.id])}>重新获取 AT</Button>
          <Dropdown
            menu={{
              items: [
                { key: 'sync', icon: <CloudSyncOutlined />, label: '同步 sub2api' },
                { key: 'mail', icon: <MailOutlined />, label: '收邮件' },
                { key: 'debug', icon: <BugOutlined />, label: '调试浏览器' },
              ],
              onClick: ({ key }) => {
                if (key === 'sync') triggerSub2ApiSync(row)
                else if (key === 'mail') showEmailHistory(row)
                else if (key === 'debug') triggerDebugBrowser(row)
              },
            }}
          >
            <Button size="small">更多 ▾</Button>
          </Dropdown>
          <Popconfirm title="删除该账号?" onConfirm={() => deleteAccount(row)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <PageScaffold
      title="Plus 池（sub2api）"
      description="已完成支付并同步到 sub2api 的账号池；sub2api_sync 写回的状态是这里的主状态。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="Plus 账号" value={summary.total} tone="primary" />
        <StatCard label="可售" value={summary.unsold} tone="success" />
        <StatCard label="已售出" value={summary.sold} tone={summary.sold ? 'warning' : 'default'} />
        <StatCard label="可调度" value={summary.schedulable} tone="success" />
        <StatCard label="同步失败" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="需重登" value={summary.relogin} tone={summary.relogin ? 'danger' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="Plus 池操作"
        description="列表只包含 Plus 账号；导出会生成 sub2api-data 文件，并将 sub2api 账号迁移到已售出分组后本地标记已售。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Select<SoldFilter>
              value={soldFilter}
              onChange={(value) => { setSoldFilter(value); setSelected([]) }}
              style={{ width: 132 }}
              options={[{ value: 'all', label: '全部' }, { value: 'unsold', label: '可售' }, { value: 'sold', label: '已售出' }]}
            />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>
            <Button icon={<CloudSyncOutlined />} loading={refreshingSub2ApiStatus} disabled={!selected.length} onClick={() => refreshSub2ApiStatus(selected.map((id) => Number(id)))}>刷新 sub2api 状态</Button>
            <Button icon={<ReloadOutlined />} loading={refreshingAccessToken} disabled={!selected.length} onClick={() => refreshAccessToken(selected.map((id) => Number(id)))}>重新获取 AT</Button>
            <Popconfirm title={`导出选中的 ${selected.length} 个账号并迁移到 sub2api 已售出分组?`} onConfirm={exportSelectedSub2Api} disabled={!selected.length}>
              <Button icon={<CloudDownloadOutlined />} type="primary" loading={exportingSold} disabled={!selected.length}>导出 sub2api 并标记已售</Button>
            </Popconfirm>
            <Popconfirm title={`确认删除选中的 ${selected.length} 个账号?`} onConfirm={batchDelete} disabled={!selected.length}>
              <Button icon={<DeleteOutlined />} danger disabled={!selected.length}>批量删除</Button>
            </Popconfirm>
          </CardToolbar>
        )}
      />

      <Table
        className="surface-table"
        rowKey="id"
        columns={columns}
        dataSource={rows}
        loading={loading}
        scroll={{ x: 2000 }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (total) => `共 ${total} 条` }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: setSelected,
        }}
      />

      <PopupCard
        open={!!emailModalAccount}
        onCancel={() => { setEmailModalAccount(null); setEmailHistory([]) }}
        title={emailModalAccount ? `${emailModalAccount.email} 最近 10 封邮件` : ''}
        footer={null}
        width={980}
      >
        <Table
          rowKey={(row) => String(row.id || `${row.received_at}-${row.subject}`)}
          columns={emailHistoryColumns}
          dataSource={emailHistory}
          loading={emailHistoryLoading}
          pagination={false}
          scroll={{ x: 900, y: 480 }}
          size="small"
        />
      </PopupCard>

      <PopupCard open={logJobId !== null} onCancel={() => setLogJobId(null)} width={900} title={logJobId ? `Job #${logJobId} 原始日志` : ''} footer={null}>
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </PopupCard>
    </PageScaffold>
  )
}
