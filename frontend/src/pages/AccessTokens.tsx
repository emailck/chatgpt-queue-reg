import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Form, Input, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { DeleteOutlined, DownloadOutlined, KeyOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'

import { API_BASE, apiFetch, formatDateTime } from '@/lib/api'
import { ActionCard, CardToolbar, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges, SelectionSummary, Sub2ApiBadge, TokenBadges } from '@/components/ui/DomainBits'

const { Text, Paragraph } = Typography

interface AccessTokenAccount {
  id: number
  pipeline_id: number | null
  chatgpt_account_id: number | null
  email: string
  password: string
  account_id: string
  workspace_id: string
  access_token: string
  refresh_token: string
  id_token: string
  session_token: string
  has_access_token: boolean
  has_refresh_token: boolean
  has_session_token: boolean
  refresh_token_id: number | null
  refresh_token_enabled: boolean
  refresh_token_has_token: boolean
  refresh_token_last_error: string
  sub2api_account_id: string
  sub2api_status: string
  sub2api_uploaded_at: string | null
  sub2api_status_checked_at: string | null
  user_agent: string
  proxy_url: string
  note: string
  metadata: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

const TXT_FIELD_OPTIONS = [
  'email',
  'password',
  'account_id',
  'workspace_id',
  'access_token',
  'refresh_token',
  'id_token',
  'session_token',
  'proxy_url',
  'user_agent',
] as const

function useIsMobile(breakpoint = 900) {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth <= breakpoint : false,
  )

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint}px)`)
    const onChange = (event: MediaQueryListEvent) => setIsMobile(event.matches)
    setIsMobile(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [breakpoint])

  return isMobile
}

export default function AccessTokens() {
  const isMobile = useIsMobile()
  const [rows, setRows] = useState<AccessTokenAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [showSecrets, setShowSecrets] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [exportOpen, setExportOpen] = useState(false)
  const [detail, setDetail] = useState<AccessTokenAccount | null>(null)
  const [mfaOpen, setMfaOpen] = useState(false)
  const [mfaDetail, setMfaDetail] = useState<AccessTokenAccount | null>(null)
  const [mfaCode, setMfaCode] = useState('')
  const [mfaLoading, setMfaLoading] = useState(false)
  const [fetchingRtId, setFetchingRtId] = useState<number | null>(null)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [exportForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      params.set('pool', 'at')
      params.set('include_secrets', String(showSecrets))
      if (search.trim()) params.set('search', search.trim())
      params.set('limit', '2000')
      const data = await apiFetch<AccessTokenAccount[]>(`/access-tokens?${params.toString()}`)
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [showSecrets, search])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    return () => clearTimeout(initial)
  }, [reload])

  const summary = useMemo(() => ({
    total: rows.length,
    at: rows.filter((row) => row.has_access_token).length,
    rt: rows.filter((row) => row.has_refresh_token || row.refresh_token_has_token).length,
    used: rows.filter((row) => row.refresh_token_id && !row.refresh_token_enabled).length,
    sub2api: rows.filter((row) => ['active', 'alive', 'ok', 'uploaded'].includes(String(row.sub2api_status || '').toLowerCase())).length,
    errors: rows.filter((row) => row.refresh_token_last_error).length,
  }), [rows])

  const openDetail = useCallback(async (row: AccessTokenAccount) => {
    try {
      const full = await apiFetch<AccessTokenAccount>(`/access-tokens/${row.id}?include_secrets=true`)
      setDetail(full)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载详情失败')
    }
  }, [])

  const openMfa = useCallback(async (row: AccessTokenAccount) => {
    setMfaOpen(true)
    setMfaDetail(null)
    setMfaCode('')
    setMfaLoading(true)
    try {
      const [full, data] = await Promise.all([
        apiFetch<AccessTokenAccount>(`/access-tokens/${row.id}?include_secrets=true`),
        apiFetch<{ code: string }>(`/access-tokens/${row.id}/mfa`),
      ])
      setMfaDetail(full)
      setMfaCode(String(data.code || '').trim())
    } catch (err) {
      message.error(err instanceof Error ? err.message : '获取 MFA 失败')
      setMfaOpen(false)
    } finally {
      setMfaLoading(false)
    }
  }, [])

  const deleteOne = async (row: AccessTokenAccount) => {
    try {
      await apiFetch(`/access-tokens/${row.id}`, { method: 'DELETE' })
      message.success('已删除')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const fetchRefreshToken = async (row: AccessTokenAccount) => {
    setFetchingRtId(row.id)
    try {
      const resp = await apiFetch<{ job_id: number | null; already_has_refresh_token: boolean; already_running: boolean }>(`/access-tokens/${row.id}/refresh-token`, { method: 'POST' })
      if (resp.already_has_refresh_token) {
        message.success('该账号已有 RT')
      } else if (resp.already_running) {
        message.info(`RT 任务已在运行：#${resp.job_id}`)
      } else {
        message.success(`已提交获取 RT 任务：#${resp.job_id}`)
      }
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '提交获取 RT 失败')
    } finally {
      setFetchingRtId(null)
    }
  }

  const rerunOauth = async (row: AccessTokenAccount) => {
    setFetchingRtId(row.id)
    try {
      const resp = await apiFetch<{ job_id: number | null; already_running: boolean }>(`/access-tokens/${row.id}/oauth`, { method: 'POST' })
      if (resp.already_running) {
        message.info(`OAuth 任务已在运行：#${resp.job_id}`)
      } else {
        message.success(`已提交 OAuth 任务：#${resp.job_id}`)
      }
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '提交 OAuth 失败')
    } finally {
      setFetchingRtId(null)
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/access-tokens/batch-delete', {
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

  const submitExport = async () => {
    const values = await exportForm.validateFields()
    const idsArg = selected.length ? selected.join(',') : ''
    const params = new URLSearchParams()
    params.set('fmt', values.fmt)
    if (idsArg) params.set('ids', idsArg)
    if (values.fmt === 'txt') {
      params.set('separator', values.separator || '----')
      const f = (values.fields || []) as string[]
      params.set('fields', f.length ? f.join(',') : 'email,password,access_token,refresh_token,session_token')
    }
    params.set('pool', 'at')
    const url = `${API_BASE}/access-tokens/export?${params.toString()}`
    window.open(url, '_blank')
    setExportOpen(false)
  }

  const toggleSelected = (id: number, checked: boolean) => {
    setSelected((prev) => checked ? [...prev, id] : prev.filter((item) => Number(item) !== id))
  }

  const secretValue = (value: string, label: string) => showSecrets ? <CopyableText value={value} label={label} code /> : <Tag>{value ? 'present' : 'missing'}</Tag>

  const columns = useMemo<TableColumnsType<AccessTokenAccount>>(() => {
    const base: TableColumnsType<AccessTokenAccount> = [
    {
      title: '邮箱',
      dataIndex: 'email',
      width: isMobile ? 180 : 220,
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          <CopyableText value={value} label="邮箱" />
          <Space size={4} wrap>
            <Tag color="green">FREE AT</Tag>
            {row.refresh_token_id && !row.refresh_token_enabled && <Tag color="orange">已使用</Tag>}
            {row.refresh_token_id && row.refresh_token_enabled && <Tag color="blue">RT</Tag>}
          </Space>
        </Space>
      ),
    },

    {
      title: 'Token',
      width: 220,
      render: (_: unknown, row) => (
        <Space direction="vertical" size={2}>
          <div>AT：{secretValue(row.access_token, 'access_token')}</div>
          <div>RT：{secretValue(row.refresh_token, 'refresh_token')}</div>
          <div>Session：{secretValue(row.session_token, 'session_token')}</div>
        </Space>
      ),
    },
    {
      title: '状态',
      width: 180,
      render: (_: unknown, row) => (
        <Space direction="vertical" size={2} wrap>
          <TokenBadges accessToken={row.has_access_token ? 'yes' : ''} refreshToken={(row.has_refresh_token || row.refresh_token_has_token) ? 'yes' : ''} />
          {row.sub2api_status ? <Sub2ApiBadge status={row.sub2api_status} /> : <Tag>未同步</Tag>}
          {row.refresh_token_last_error ? <Tag color="red">有错误</Tag> : null}
        </Space>
      ),
    },
    {
      title: '链接',
      width: 160,
      render: (_: unknown, row) => (
        <Space direction="vertical" size={2}>
          <LinkedIdBadges pipelineId={row.pipeline_id} accountId={row.chatgpt_account_id} />
          <Tag color={row.refresh_token_id ? (row.refresh_token_enabled ? 'green' : 'orange') : 'default'}>
            {row.refresh_token_id ? (row.refresh_token_enabled ? 'enabled' : 'used') : 'no rt'}
          </Tag>
        </Space>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: isMobile ? undefined : 'right',
      width: isMobile ? 220 : 240,
      render: (_: unknown, row) => (
        <Space size={6} wrap>
          <Button size="small" onClick={() => openDetail(row)}>详情</Button>
          <Button size="small" icon={<KeyOutlined />} onClick={() => openMfa(row)}>MFA</Button>
          <Button size="small" loading={fetchingRtId === row.id} onClick={() => rerunOauth(row)}>OAuth</Button>
          {!row.refresh_token_has_token && <Button size="small" type="primary" loading={fetchingRtId === row.id} onClick={() => fetchRefreshToken(row)}>获取 RT</Button>}
          <Popconfirm title="删除该 token?" onConfirm={() => deleteOne(row)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
    ]

    return base
  }, [fetchingRtId, isMobile, openDetail, openMfa, rerunOauth, showSecrets])

  return (
    <PageScaffold
      title="Free 池（已注册 AT）"
      description="已注册且已获取 ChatGPT AT 的账号池；RT 只是账号附加状态，不再作为独立业务池入口。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="Free AT" value={summary.total} hint="已注册账号" tone="primary" />
        <StatCard label="AT present" value={summary.at} tone="success" />
        <StatCard label="RT present" value={summary.rt} tone="info" />
        <StatCard label="已使用" value={summary.used} tone={summary.used ? 'warning' : 'default'} />
        <StatCard label="sub2api active" value={summary.sub2api} tone="success" />
        <StatCard label="errors" value={summary.errors} tone={summary.errors ? 'danger' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="Free 池操作"
        description="导出、补 RT 和删除都保留原接口；完整 token 只在显式开关或详情弹出卡片中展示。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Space size={6}><Switch checked={showSecrets} onChange={setShowSecrets} /><Text>显示完整 token</Text></Space>
            <Input.Search
              allowClear
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onSearch={(value) => setSearch(value.trim())}
              placeholder="搜索邮箱 / account / workspace"
              enterButton={<SearchOutlined />}
              style={{ width: 300 }}
            />
            <Button icon={<DownloadOutlined />} type="primary" onClick={() => setExportOpen(true)}>导出{selected.length ? `（${selected.length}）` : '全部'}</Button>
            <Popconfirm title={`确认删除选中的 ${selected.length} 条?`} onConfirm={batchDelete} disabled={!selected.length}>
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
        scroll={{ x: isMobile ? 1180 : 1500 }}
        pagination={{ defaultPageSize: 18, showSizeChanger: true, pageSizeOptions: [18, 36, 72, 144], showTotal: (total) => `共 ${total} 条` }}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys) }}
      />

      <PopupCard open={exportOpen} title="导出 Free AT 池" onCancel={() => setExportOpen(false)} onOk={submitExport} okText="下载" width={620}>
        <Paragraph type="secondary">{selected.length ? `当前选中 ${selected.length} 条，仅导出选中。` : '未选中任何条目，将导出全部。'}</Paragraph>
        <Form form={exportForm} layout="vertical" initialValues={{ fmt: 'txt', separator: '----', fields: ['email', 'password', 'access_token', 'refresh_token', 'session_token'] }}>
          <Form.Item label="格式" name="fmt">
            <Select options={[{ value: 'txt', label: 'TXT（行式，可指定字段顺序与分隔符）' }, { value: 'csv', label: 'CSV' }, { value: 'json', label: 'JSON（含完整 cookies / fingerprint）' }]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.fmt !== curr.fmt}>
            {({ getFieldValue }) => (getFieldValue('fmt') === 'txt' ? (
              <>
                <Form.Item label="分隔符" name="separator"><Input /></Form.Item>
                <Form.Item label="字段顺序" name="fields"><Select mode="multiple" options={TXT_FIELD_OPTIONS.map((f) => ({ value: f, label: f }))} placeholder="按需勾选并拖动排序" /></Form.Item>
              </>
            ) : null)}
          </Form.Item>
        </Form>
      </PopupCard>

      <PopupCard open={!!detail} onCancel={() => setDetail(null)} width={880} title={detail ? `Token #${detail.id} — ${detail.email}` : ''} footer={null}>
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <ActionCard title="完整凭据" description="详情弹出卡片按需请求 include_secrets=true，关闭后不在列表里继续展开 secrets。" />
            <KeyValueGrid>
              {[
                ['email', detail.email],
                ['password', detail.password],
                ['account_id', detail.account_id],
                ['workspace_id', detail.workspace_id],
                ['access_token', detail.access_token],
                ['refresh_token', detail.refresh_token],
                ['sub2api_status', detail.sub2api_status],
                ['sub2api_account_id', detail.sub2api_account_id],
                ['id_token', detail.id_token],
                ['session_token', detail.session_token],
                ['user_agent', detail.user_agent],
                ['proxy_url', detail.proxy_url],
              ].map(([label, value]) => <KeyValue key={label} label={label} value={<CopyableText value={value} label={label} code />} />)}
            </KeyValueGrid>
            <ErrorCallout error={detail.refresh_token_last_error} />
          </Space>
        )}
      </PopupCard>

      <PopupCard
        open={mfaOpen}
        onCancel={() => { setMfaOpen(false); setMfaCode(''); setMfaDetail(null) }}
        width={520}
        title="MFA"
        footer={null}
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <div>
            <Text type="secondary">密码</Text>
            <div style={{ marginTop: 8, fontSize: 16, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' }}>
              {mfaDetail?.password || '------'}
            </div>
          </div>
          <div>
            <Text type="secondary">6 位验证码</Text>
            <div style={{ marginTop: 8, minHeight: 56, display: 'flex', alignItems: 'center' }}>
              {mfaLoading ? (
                <Text type="secondary">获取中…</Text>
              ) : (
                <div style={{ fontSize: 44, fontWeight: 700, letterSpacing: 6, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' }}>
                  {mfaCode || '------'}
                </div>
              )}
            </div>
          </div>
        </Space>
      </PopupCard>
    </PageScaffold>
  )
}
