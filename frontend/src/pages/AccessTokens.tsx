import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Form, Input, Popconfirm, Radio, Select, Space, Switch, Tag, Typography, message } from 'antd'
import { DeleteOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons'

import { API_BASE, apiFetch, formatDateTime } from '@/lib/api'
import { ActionCard, CardToolbar, EntityCard, EntityGrid, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges, SelectionSummary, Sub2ApiBadge, TokenBadges } from '@/components/ui/DomainBits'

const { Text, Paragraph } = Typography

type FreePool = 'at' | 'rt'

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
  codex_token_id: number | null
  codex_token_alive: boolean
  codex_token_has_refresh_token: boolean
  codex_token_last_error: string
  sub2api_external_id: string
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

export default function AccessTokens() {
  const [rows, setRows] = useState<AccessTokenAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [pool, setPool] = useState<FreePool>('at')
  const [showSecrets, setShowSecrets] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [exportOpen, setExportOpen] = useState(false)
  const [detail, setDetail] = useState<AccessTokenAccount | null>(null)
  const [fetchingRtId, setFetchingRtId] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const [exportForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<AccessTokenAccount[]>(`/access-tokens?pool=${pool}&include_secrets=${showSecrets}`)
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [pool, showSecrets])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    return () => clearTimeout(initial)
  }, [reload])

  const summary = useMemo(() => ({
    total: rows.length,
    at: rows.filter((row) => row.has_access_token).length,
    rt: rows.filter((row) => row.has_refresh_token || row.codex_token_has_refresh_token).length,
    sub2api: rows.filter((row) => ['active', 'alive', 'ok', 'uploaded'].includes(String(row.sub2api_status || '').toLowerCase())).length,
    invalid: rows.filter((row) => row.codex_token_id && !row.codex_token_alive).length,
    errors: rows.filter((row) => row.codex_token_last_error).length,
  }), [rows])

  const openDetail = useCallback(async (row: AccessTokenAccount) => {
    try {
      const full = await apiFetch<AccessTokenAccount>(`/access-tokens/${row.id}?include_secrets=true`)
      setDetail(full)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载详情失败')
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
    params.set('pool', pool)
    const url = `${API_BASE}/access-tokens/export?${params.toString()}`
    window.open(url, '_blank')
    setExportOpen(false)
  }

  const toggleSelected = (id: number, checked: boolean) => {
    setSelected((prev) => checked ? [...prev, id] : prev.filter((item) => Number(item) !== id))
  }

  const secretValue = (value: string, label: string) => showSecrets ? <CopyableText value={value} label={label} code /> : <Tag>{value ? 'present' : 'missing'}</Tag>

  return (
    <PageScaffold
      title="Free 号池"
      description="AT/RT 池按账号卡片展示，敏感 token 默认只显示状态；详情弹出卡片会按需拉取完整 secrets。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="当前池" value={pool === 'rt' ? 'RT' : 'AT'} hint={`${summary.total} 条`} tone="primary" />
        <StatCard label="AT present" value={summary.at} tone="success" />
        <StatCard label="RT present" value={summary.rt} tone="info" />
        <StatCard label="sub2api active" value={summary.sub2api} tone="success" />
        <StatCard label="invalid/dead" value={summary.invalid} tone={summary.invalid ? 'danger' : 'default'} />
        <StatCard label="errors" value={summary.errors} tone={summary.errors ? 'danger' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="Token 池操作"
        description="切换池子、导出、补 RT 和删除都保留原接口；完整 token 只在显式开关或详情弹出卡片中展示。"
        actions={(
          <CardToolbar>
            <Radio.Group
              value={pool}
              onChange={(e) => { setPool(e.target.value); setSelected([]); setPage(1) }}
              optionType="button"
              buttonStyle="solid"
              options={[{ value: 'at', label: 'Free AT 池' }, { value: 'rt', label: 'Free RT 池' }]}
            />
            <SelectionSummary count={selected.length} />
            <Space size={6}><Switch checked={showSecrets} onChange={setShowSecrets} /><Text>显示完整 token</Text></Space>
            <Button icon={<DownloadOutlined />} type="primary" onClick={() => setExportOpen(true)}>导出{selected.length ? `（${selected.length}）` : '全部'}</Button>
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
            title={<CopyableText value={row.email} label="邮箱" />}
            subtitle={`Token #${row.id}`}
            status={<Tag color={pool === 'rt' ? 'blue' : 'green'}>{pool.toUpperCase()} pool</Tag>}
            tone={row.codex_token_id && !row.codex_token_alive ? 'danger' : row.codex_token_has_refresh_token ? 'success' : 'default'}
            selected={selected.includes(row.id)}
            onSelect={(checked) => toggleSelected(row.id, checked)}
            badges={(
              <Space size={4} wrap>
                <TokenBadges accessToken={row.has_access_token ? 'yes' : ''} refreshToken={row.has_refresh_token ? 'yes' : ''} codexRt={row.codex_token_has_refresh_token ? 'yes' : ''} />
                <LinkedIdBadges pipelineId={row.pipeline_id} accountId={row.chatgpt_account_id} />
                {row.sub2api_status && <Sub2ApiBadge status={row.sub2api_status} />}
              </Space>
            )}
            footer={formatDateTime(row.created_at)}
            actions={(
              <>
                <Button size="small" onClick={() => openDetail(row)}>详情</Button>
                {pool === 'at' && !row.codex_token_has_refresh_token && <Button size="small" type="primary" loading={fetchingRtId === row.id} onClick={() => fetchRefreshToken(row)}>获取 RT</Button>}
                <Popconfirm title="删除该 token?" onConfirm={() => deleteOne(row)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </>
            )}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <KeyValueGrid>
                <KeyValue label="access_token" value={secretValue(row.access_token, 'access_token')} />
                <KeyValue label="refresh_token" value={secretValue(row.refresh_token, 'refresh_token')} />
                <KeyValue label="session_token" value={secretValue(row.session_token, 'session_token')} />
                <KeyValue label="workspace" value={<CopyableText value={row.workspace_id} label="workspace" code />} />
                <KeyValue label="代理" value={<CopyableText value={row.proxy_url} label="代理" />} />
                <KeyValue label="sub2api external" value={<CopyableText value={row.sub2api_external_id} label="sub2api external" code />} />
              </KeyValueGrid>
              <ErrorCallout error={row.codex_token_last_error} />
            </Space>
          </EntityCard>
        )}
      />

      <PopupCard open={exportOpen} title={`导出 ${pool === 'rt' ? 'Free RT 池' : 'Free AT 池'}`} onCancel={() => setExportOpen(false)} onOk={submitExport} okText="下载" width={620}>
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
                ['codex_refresh_token', detail.refresh_token],
                ['sub2api_status', detail.sub2api_status],
                ['sub2api_external_id', detail.sub2api_external_id],
                ['id_token', detail.id_token],
                ['session_token', detail.session_token],
                ['user_agent', detail.user_agent],
                ['proxy_url', detail.proxy_url],
              ].map(([label, value]) => <KeyValue key={label} label={label} value={<CopyableText value={value} label={label} code />} />)}
            </KeyValueGrid>
            <ErrorCallout error={detail.codex_token_last_error} />
          </Space>
        )}
      </PopupCard>
    </PageScaffold>
  )
}
