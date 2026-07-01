import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Input, Popconfirm, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

import { CopyableText, ErrorCallout } from '@/components/ui/DomainBits'
import { ActionCard, CardToolbar, KeyValue, KeyValueGrid, PageScaffold, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

interface CodexInviteRow {
  job_id: number
  pipeline_id: number | null
  type: string
  status: string
  ok: boolean
  sent: boolean
  dry_run: boolean
  source_type: string
  source_id: number | string
  source_email: string
  domain: string
  emails: string[]
  invited_email: string
  remaining_invites: number | null
  status_code: number | null
  error: string
  activation_pipeline_ids?: number[]
  created_at: string | null
  finished_at: string | null
}

interface CodexInviteResponse {
  rows: CodexInviteRow[]
  summary: {
    rows: number
    inviters: number
    inviter_emails: string[]
    total_emails: number
    sent_emails: number
    dry_run_emails: number
    failed_rows: number
  }
}

function boolTag(row: CodexInviteRow) {
  if (row.dry_run) return <Tag color="default">dry-run</Tag>
  if (row.sent) return <Tag color="green">已发送</Tag>
  if (row.status === 'failed' || row.error) return <Tag color="red">失败</Tag>
  return <Tag color="blue">记录</Tag>
}

function rowKey(row: CodexInviteRow): string {
  return `${row.job_id}-${row.source_email}-${(row.emails || []).join('|')}`
}

export default function CodexInvites() {
  const [rows, setRows] = useState<CodexInviteRow[]>([])
  const [summary, setSummary] = useState<CodexInviteResponse['summary'] | null>(null)
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [sentOnly, setSentOnly] = useState(false)
  const [includeDryRun, setIncludeDryRun] = useState(true)
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([])
  const [activating, setActivating] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      params.set('limit', '1000')
      if (query.trim()) params.set('inviter', query.trim())
      if (sentOnly) params.set('sent_only', 'true')
      params.set('include_dry_run', includeDryRun ? 'true' : 'false')
      const data = await apiFetch<CodexInviteResponse>(`/codex-invites?${params.toString()}`)
      setRows(data.rows || [])
      setSummary(data.summary || null)
      setSelectedKeys([])
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载 Codex 邀请记录失败')
    } finally {
      setLoading(false)
    }
  }, [includeDryRun, query, sentOnly])

  useEffect(() => { reload() }, [reload])

  const selectedEmails = useMemo(() => {
    const keySet = new Set(selectedKeys.map(String))
    const emails: string[] = []
    for (const row of rows) {
      if (!keySet.has(rowKey(row))) continue
      for (const email of row.emails || []) {
        const value = String(email || '').trim().toLowerCase()
        if (value && !emails.includes(value)) emails.push(value)
      }
    }
    return emails
  }, [rows, selectedKeys])

  const activateSelected = useCallback(async () => {
    if (!selectedEmails.length) {
      message.warning('请先选择包含受邀邮箱的记录')
      return
    }
    setActivating(true)
    try {
      const resp = await apiFetch<{ created: number; pipeline_ids: number[] }>('/codex-invites/activate', {
        method: 'POST',
        body: JSON.stringify({ emails: selectedEmails, dry_run: false }),
      })
      message.success(`已创建 ${resp.created} 条激活子流程${resp.pipeline_ids?.length ? `：${resp.pipeline_ids.join(', ')}` : ''}`)
      setSelectedKeys([])
      await reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建激活子流程失败')
    } finally {
      setActivating(false)
    }
  }, [reload, selectedEmails])

  const columns: TableColumnsType<CodexInviteRow> = useMemo(() => [
    {
      title: '邀请母号',
      dataIndex: 'source_email',
      width: 260,
      render: (value: string, row) => (
        <Space direction="vertical" size={2}>
          {value ? <CopyableText value={value} label="邀请母号" /> : <Text type="secondary">-</Text>}
          <Space size={4} wrap>
            <Tag>{row.source_type || 'unknown'}</Tag>
            {row.source_id ? <Tag>ID {row.source_id}</Tag> : null}
          </Space>
        </Space>
      ),
    },
    {
      title: '受邀邮箱',
      dataIndex: 'emails',
      width: 360,
      render: (emails: string[]) => (
        <Space direction="vertical" size={2}>
          {(emails || []).length ? emails.map((email) => <CopyableText key={email} value={email} label="受邀邮箱" />) : <Text type="secondary">-</Text>}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'state',
      width: 150,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          {boolTag(row)}
          <Text code>{row.status_code || row.status}</Text>
        </Space>
      ),
    },
    {
      title: '数量',
      key: 'count',
      width: 100,
      render: (_, row) => <Text strong>{(row.emails || []).length}</Text>,
    },
    {
      title: '关联',
      key: 'links',
      width: 220,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Text>Job #{row.job_id}</Text>
          {row.pipeline_id ? <Text>Pipeline #{row.pipeline_id}</Text> : <Text type="secondary">standalone</Text>}
          {row.activation_pipeline_ids?.length ? <Text>激活子流程: {row.activation_pipeline_ids.join(', ')}</Text> : null}
        </Space>
      ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 190,
      render: (value: string | null, row) => (
        <Space direction="vertical" size={2}>
          <Text>{formatDateTime(value)}</Text>
          <Text type="secondary">完成 {formatDateTime(row.finished_at)}</Text>
        </Space>
      ),
    },
    {
      title: '错误',
      dataIndex: 'error',
      width: 300,
      render: (value: string) => value ? <ErrorCallout error={value} /> : <Text type="secondary">-</Text>,
    },
  ], [])

  return (
    <PageScaffold
      title="Codex 邀请记录"
      description="汇总 codex_invitation / codex_batch_invite 任务结果：按邀请母号查看实际发送、dry-run、失败记录以及所有受邀邮箱。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="记录" value={summary?.rows || 0} tone="primary" />
        <StatCard label="母号" value={summary?.inviters || 0} tone="info" />
        <StatCard label="实际发送邮箱" value={summary?.sent_emails || 0} tone="success" />
        <StatCard label="dry-run 邮箱" value={summary?.dry_run_emails || 0} />
        <StatCard label="失败记录" value={summary?.failed_rows || 0} tone={summary?.failed_rows ? 'danger' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="筛选"
        description="输入母号邮箱或账号 ID 可以只看某个母号。"
        actions={(
          <CardToolbar>
            <Input allowClear placeholder="母号邮箱 / ID" value={query} onChange={(e) => setQuery(e.target.value)} onPressEnter={reload} style={{ width: 220 }} />
            <Space><Text>只看已发送</Text><Switch checked={sentOnly} onChange={setSentOnly} /></Space>
            <Space><Text>包含 dry-run</Text><Switch checked={includeDryRun} onChange={setIncludeDryRun} /></Space>
            <Button onClick={reload}>查询</Button>
            <Popconfirm
              title={`为选中的 ${selectedEmails.length} 个受邀邮箱创建 sso_oauth → active 子流程？`}
              onConfirm={activateSelected}
              disabled={!selectedEmails.length}
            >
              <Button type="primary" loading={activating} disabled={!selectedEmails.length}>激活选中邮箱</Button>
            </Popconfirm>
          </CardToolbar>
        )}
      >
        <KeyValueGrid>
          <KeyValue label="提示" value="已发送=服务端邀请接口返回 200；dry-run 不会消耗邀请额度。" />
        </KeyValueGrid>
      </ActionCard>

      <Table
        rowKey={rowKey}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: setSelectedKeys,
          getCheckboxProps: (row) => ({ disabled: !(row.emails || []).length || row.dry_run || !!row.error }),
        }}
        size="small"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        scroll={{ x: 1600 }}
      />
    </PageScaffold>
  )
}
