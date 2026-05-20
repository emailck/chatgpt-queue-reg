import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Dropdown, Input, Popconfirm, Space, Tag, Typography, message } from 'antd'
import { BugOutlined, DeleteOutlined, MailOutlined, ReloadOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { ActionCard, CardToolbar, EntityCard, EntityGrid, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, SelectionSummary, Sub2ApiBadge, TokenBadges, UrlAction } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

interface Account {
  id: number
  email: string
  password: string
  status: string
  account_id: string
  workspace_id: string
  proxy_url: string
  last_error: string
  last_payment_link_id: number | null
  last_payment_link_url: string
  user_agent: string
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
}

function accountTone(row: Account): 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (row.last_error) return 'danger'
  if (row.status === 'registered') return 'success'
  if (row.status === 'registering') return 'info'
  if (row.status === 'failed') return 'danger'
  return 'default'
}

export default function Accounts() {
  const [rows, setRows] = useState<Account[]>([])
  const [loading, setLoading] = useState(false)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [emailModalAccount, setEmailModalAccount] = useState<Account | null>(null)
  const [emailKeyword, setEmailKeyword] = useState('')
  const [selected, setSelected] = useState<React.Key[]>([])
  const [page, setPage] = useState(1)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<Account[]>('/accounts?limit=300')
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
    registered: rows.filter((row) => row.status === 'registered').length,
    failed: rows.filter((row) => row.status === 'failed' || !!row.last_error).length,
    at: rows.filter((row) => row.has_access_token).length,
    codexRt: rows.filter((row) => row.codex_token_has_refresh_token).length,
    sub2api: rows.filter((row) => ['active', 'alive', 'ok', 'uploaded'].includes(String(row.sub2api_status || '').toLowerCase())).length,
  }), [rows])

  const triggerReadEmail = async (account: Account, keyword: string) => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/accounts/${account.id}/read-email`, {
        method: 'POST',
        body: JSON.stringify({ keyword, timeout_seconds: 120 }),
      })
      message.success(`已派发收邮件 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
      setEmailModalAccount(null)
      setEmailKeyword('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '收邮件失败')
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

  const retryPaymentLink = async (account: Account, plan: 'team' | 'plus') => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/accounts/${account.id}/payment-link/retry`, {
        method: 'POST',
        body: JSON.stringify({ plan }),
      })
      message.success(`已重试 ${plan} 长链生成 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重试失败')
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

  const toggleSelected = (id: number, checked: boolean) => {
    setSelected((prev) => checked ? [...prev, id] : prev.filter((item) => Number(item) !== id))
  }

  return (
    <PageScaffold
      title="账号"
      description="账号卡片展示注册状态、身份绑定、代理一致性、Token 与最近支付长链；操作仍按账号边界执行。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="账号总数" value={summary.total} tone="primary" />
        <StatCard label="已注册" value={summary.registered} tone="success" />
        <StatCard label="失败/异常" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="有 AT" value={summary.at} tone="info" />
        <StatCard label="Codex RT" value={summary.codexRt} tone="info" />
        <StatCard label="sub2api 活跃" value={summary.sub2api} tone="success" />
      </SummaryGrid>

      <ActionCard
        title="账号池操作"
        description="收邮件、浏览器调试、重试支付长链都从账号卡片发起；日志用居中弹出卡片展示原始 transcript。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>
            <Popconfirm title={`确认删除选中的 ${selected.length} 个账号?`} onConfirm={batchDelete} disabled={!selected.length}>
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
            title={<CopyableText value={row.email} label="邮箱" />}
            subtitle={`Account #${row.id}`}
            status={<StatusTag status={row.status} />}
            tone={accountTone(row)}
            selected={selected.includes(row.id)}
            onSelect={(checked) => toggleSelected(row.id, checked)}
            badges={(
              <Space size={4} wrap>
                <TokenBadges accessToken={row.has_access_token ? 'yes' : ''} refreshToken={row.has_refresh_token ? 'yes' : ''} codexRt={row.codex_token_has_refresh_token ? 'yes' : ''} />
                {row.codex_token_id && <Tag color={row.codex_token_alive ? 'blue' : 'red'}>Codex #{row.codex_token_id}</Tag>}
                {row.sub2api_status && <Sub2ApiBadge status={row.sub2api_status} />}
              </Space>
            )}
            footer={formatDateTime(row.created_at)}
            actions={(
              <>
                <Button size="small" icon={<MailOutlined />} onClick={() => setEmailModalAccount(row)}>收邮件</Button>
                <Button size="small" icon={<BugOutlined />} onClick={() => triggerDebugBrowser(row)}>调试浏览器</Button>
                {row.status !== 'registering' && (
                  <Dropdown
                    menu={{
                      items: [
                        { key: 'team', label: '生成 Team 长链' },
                        { key: 'plus', label: '生成 Plus 长链 (IDR)' },
                      ],
                      onClick: ({ key }) => retryPaymentLink(row, key as 'team' | 'plus'),
                    }}
                  >
                    <Button size="small" type="dashed">支付长链 ▾</Button>
                  </Dropdown>
                )}
                <Popconfirm title="删除该账号?" onConfirm={() => deleteAccount(row)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </>
            )}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <KeyValueGrid>
                <KeyValue label="密码" value={<CopyableText value={row.password} label="密码" code />} />
                <KeyValue label="OpenAI Account" value={<CopyableText value={row.account_id} label="Account ID" code />} />
                <KeyValue label="Workspace" value={<CopyableText value={row.workspace_id} label="Workspace" code />} />
                <KeyValue label="最近长链" value={row.last_payment_link_id ? <Tag color="purple">#{row.last_payment_link_id}</Tag> : <Text type="secondary">-</Text>} />
                <KeyValue label="长链 URL" value={<UrlAction url={row.last_payment_link_url} />} />
                <KeyValue label="代理" value={<CopyableText value={row.proxy_url} label="代理" />} />
              </KeyValueGrid>
              <ErrorCallout error={row.last_error || row.codex_token_last_error} />
            </Space>
          </EntityCard>
        )}
      />

      <PopupCard
        open={!!emailModalAccount}
        onCancel={() => { setEmailModalAccount(null); setEmailKeyword('') }}
        onOk={() => emailModalAccount && triggerReadEmail(emailModalAccount, emailKeyword)}
        title={emailModalAccount ? `收 ${emailModalAccount.email} 的邮件` : ''}
        okText="开始读取"
        width={560}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text>关键字 (subject/body 包含；留空匹配最新一封)</Text>
          <Input value={emailKeyword} onChange={(e) => setEmailKeyword(e.target.value)} placeholder="例如 ChatGPT" />
        </Space>
      </PopupCard>

      <PopupCard open={logJobId !== null} onCancel={() => setLogJobId(null)} width={900} title={logJobId ? `Job #${logJobId} 原始日志` : ''} footer={null}>
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </PopupCard>
    </PageScaffold>
  )
}
