import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Empty, Input, Popconfirm, Progress, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { ActionCard, CardToolbar, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges, Sub2ApiBadge, TokenBadges } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime, formatDuration } from '@/lib/api'
import type { Job, JobRetryResponse, Pipeline, PipelineDetail } from '@/lib/contracts'
import { stageLabel } from '@/lib/contracts'

const { Text } = Typography

const ACTIVE_STATUSES = new Set(['created', 'queued', 'running'])
const FAILED_STATUSES = new Set(['failed', 'interrupted', 'cancelled'])

interface AccountSummary {
  id: number
  email: string
  status: string
  last_error: string
  proxy_id: number | null
  proxy_url: string
  has_access_token?: boolean
  has_refresh_token?: boolean
  codex_token_has_refresh_token?: boolean
  codex_token_alive?: boolean
  sub2api_status?: string | null
}

interface JobTrackerRow {
  key: string
  accountId: number | null
  email: string
  account?: AccountSummary
  pipeline: Pipeline
  pipelines: Pipeline[]
  jobs: Job[]
  currentJob?: Job
  latestJob?: Job
  isPendingAccount: boolean
}

interface StageBreakdownRow {
  key: string
  stage: string
  job?: Job
  index: number
}

function timeValue(value?: string | null): number {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function latestTime(pipeline: Pipeline): number {
  return Math.max(timeValue(pipeline.updated_at), timeValue(pipeline.finished_at), timeValue(pipeline.started_at), timeValue(pipeline.created_at))
}

function latestJobTime(job: Job): number {
  return Math.max(timeValue(job.updated_at), timeValue(job.finished_at), timeValue(job.started_at), timeValue(job.created_at))
}

function compareLatestPipeline(a: Pipeline, b: Pipeline): number {
  const activeDelta = Number(ACTIVE_STATUSES.has(b.status)) - Number(ACTIVE_STATUSES.has(a.status))
  if (activeDelta) return activeDelta
  return latestTime(b) - latestTime(a)
}

function sortJobs(jobs: Job[]): Job[] {
  return [...jobs].sort((a, b) => latestJobTime(b) - latestJobTime(a))
}

function pickCurrentJob(pipeline: Pipeline, jobs: Job[]) {
  const sorted = sortJobs(jobs)
  return (
    sorted.find((job) => job.status === 'running') ||
    sorted.find((job) => job.status === 'queued' && job.type === pipeline.current_stage) ||
    sorted.find((job) => job.type === pipeline.current_stage) ||
    sorted[0]
  )
}

function progressPercent(pipeline: Pipeline) {
  const total = Math.max(Number(pipeline.total_steps || 0), 1)
  return Math.min(100, Math.round((Number(pipeline.completed_steps || 0) / total) * 100))
}

function progressStatus(status: string): 'normal' | 'exception' | 'active' | 'success' {
  if (status === 'succeeded') return 'success'
  if (FAILED_STATUSES.has(status)) return 'exception'
  if (ACTIVE_STATUSES.has(status)) return 'active'
  return 'normal'
}

function mergedError(row: JobTrackerRow) {
  return row.pipeline.error || row.latestJob?.error || row.account?.last_error || ''
}

function buildRows(pipelines: Pipeline[], jobs: Job[], accounts: AccountSummary[]): JobTrackerRow[] {
  const accountsById = new Map(accounts.map((account) => [account.id, account]))
  const jobsByPipeline = new Map<number, Job[]>()
  const jobsByAccount = new Map<number, Job[]>()

  for (const job of jobs) {
    if (job.pipeline_id !== null) {
      const list = jobsByPipeline.get(job.pipeline_id) || []
      list.push(job)
      jobsByPipeline.set(job.pipeline_id, list)
    }
    if (job.account_id !== null) {
      const list = jobsByAccount.get(job.account_id) || []
      list.push(job)
      jobsByAccount.set(job.account_id, list)
    }
  }

  const pipelinesByAccount = new Map<number, Pipeline[]>()
  const pendingPipelines: Pipeline[] = []
  for (const pipeline of pipelines) {
    if (pipeline.account_id === null) {
      pendingPipelines.push(pipeline)
      continue
    }
    const list = pipelinesByAccount.get(pipeline.account_id) || []
    list.push(pipeline)
    pipelinesByAccount.set(pipeline.account_id, list)
  }

  const rows: JobTrackerRow[] = []
  for (const [accountId, accountPipelines] of pipelinesByAccount.entries()) {
    const sortedPipelines = [...accountPipelines].sort(compareLatestPipeline)
    const pipeline = sortedPipelines[0]
    const account = accountsById.get(accountId)
    const pipelineJobs = sortedPipelines.flatMap((item) => jobsByPipeline.get(item.id) || [])
    const accountJobs = jobsByAccount.get(accountId) || []
    const rowJobs = pipelineJobs.length ? pipelineJobs : accountJobs
    const currentJob = pickCurrentJob(pipeline, jobsByPipeline.get(pipeline.id) || rowJobs)
    rows.push({
      key: `account-${accountId}`,
      accountId,
      email: account?.email || currentJob?.email_address || '',
      account,
      pipeline,
      pipelines: sortedPipelines,
      jobs: sortJobs(rowJobs),
      currentJob,
      latestJob: sortJobs(rowJobs)[0],
      isPendingAccount: false,
    })
  }

  for (const pipeline of pendingPipelines.sort(compareLatestPipeline)) {
    const pipelineJobs = jobsByPipeline.get(pipeline.id) || []
    const currentJob = pickCurrentJob(pipeline, pipelineJobs)
    rows.push({
      key: `pipeline-${pipeline.id}`,
      accountId: null,
      email: currentJob?.email_address || '',
      pipeline,
      pipelines: [pipeline],
      jobs: sortJobs(pipelineJobs),
      currentJob,
      latestJob: sortJobs(pipelineJobs)[0],
      isPendingAccount: true,
    })
  }

  return rows.sort((a, b) => latestTime(b.pipeline) - latestTime(a.pipeline))
}

function stageOptionsFromRows(rows: JobTrackerRow[]) {
  const stages = new Set<string>()
  for (const row of rows) {
    if (row.pipeline.current_stage) stages.add(row.pipeline.current_stage)
    for (const stage of row.pipeline.stages || []) stages.add(stage)
  }
  return Array.from(stages).map((stage) => ({ value: stage, label: `${stageLabel(stage)} / ${stage}` }))
}

function StatusPair({ pipeline, job }: { pipeline: Pipeline; job?: Job }) {
  return (
    <Space direction="vertical" size={2}>
      <StatusTag status={pipeline.status} />
      {job && <Text type="secondary">Job <StatusTag status={job.status} /></Text>}
    </Space>
  )
}

export default function Jobs() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [accounts, setAccounts] = useState<AccountSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [stageFilter, setStageFilter] = useState<string | undefined>()
  const [query, setQuery] = useState('')
  const [detail, setDetail] = useState<PipelineDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [selectedLogJobId, setSelectedLogJobId] = useState<number | null>(null)
  const [retryingJobId, setRetryingJobId] = useState<number | null>(null)
  const [stoppingJobId, setStoppingJobId] = useState<number | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const [pipelineData, jobData, accountData] = await Promise.all([
        apiFetch<Pipeline[]>('/pipelines?limit=500'),
        apiFetch<Job[]>('/jobs?limit=500'),
        apiFetch<AccountSummary[]>('/accounts?limit=500'),
      ])
      setPipelines(Array.isArray(pipelineData) ? pipelineData : [])
      setJobs(Array.isArray(jobData) ? jobData : [])
      setAccounts(Array.isArray(accountData) ? accountData : [])
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载 Jobs 追踪失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    const interval = setInterval(reload, 5000)
    return () => {
      clearTimeout(initial)
      clearInterval(interval)
    }
  }, [reload])

  const rows = useMemo(() => buildRows(pipelines, jobs, accounts), [accounts, jobs, pipelines])

  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return rows.filter((row) => {
      if (statusFilter && row.pipeline.status !== statusFilter) return false
      if (stageFilter && row.pipeline.current_stage !== stageFilter) return false
      if (!needle) return true
      const haystack = [
        row.key,
        row.email,
        row.accountId,
        row.pipeline.id,
        row.pipeline.payment_link_id,
        row.pipeline.current_stage,
        row.pipeline.status,
        row.pipeline.preset,
        row.currentJob?.id,
        row.latestJob?.id,
      ].map((value) => String(value || '').toLowerCase())
      return haystack.some((value) => value.includes(needle))
    })
  }, [query, rows, stageFilter, statusFilter])

  const stageOptions = useMemo(() => stageOptionsFromRows(rows), [rows])

  const summary = useMemo(() => ({
    total: rows.length,
    running: rows.filter((row) => row.pipeline.status === 'running').length,
    queued: rows.filter((row) => row.pipeline.status === 'queued' || row.pipeline.status === 'created').length,
    succeeded: rows.filter((row) => row.pipeline.status === 'succeeded').length,
    failed: rows.filter((row) => FAILED_STATUSES.has(row.pipeline.status)).length,
    pendingAccount: rows.filter((row) => row.isPendingAccount).length,
  }), [rows])

  const openDetail = useCallback(async (pipelineId: number) => {
    setDetailLoading(true)
    try {
      const data = await apiFetch<PipelineDetail>(`/pipelines/${pipelineId}`)
      const detailJobs = data.jobs || []
      const defaultJob = pickCurrentJob(data.pipeline, detailJobs)
      setDetail(data)
      setSelectedLogJobId(defaultJob?.id || null)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载详情失败')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const closeDetail = () => {
    setDetail(null)
    setSelectedLogJobId(null)
  }

  const retryStageJob = useCallback(async (jobId: number, pipelineId: number) => {
    setRetryingJobId(jobId)
    try {
      const resp = await apiFetch<JobRetryResponse>(`/jobs/${jobId}/retry`, { method: 'POST' })
      message.success(`已重新入队 Job #${resp.job_id}（${stageLabel(resp.stage)}）`)
      await Promise.all([reload(), openDetail(pipelineId)])
      setSelectedLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重试失败')
    } finally {
      setRetryingJobId(null)
    }
  }, [openDetail, reload])

  const forceStopJob = useCallback(async (jobId: number, pipelineId: number) => {
    setStoppingJobId(jobId)
    try {
      await apiFetch(`/jobs/${jobId}/force-stop`, { method: 'POST' })
      message.success(`Job #${jobId} 已强制停止`)
      await Promise.all([reload(), openDetail(pipelineId)])
    } catch (err) {
      message.error(err instanceof Error ? err.message : '强制停止失败')
    } finally {
      setStoppingJobId(null)
    }
  }, [openDetail, reload])

  const tableColumns: TableColumnsType<JobTrackerRow> = [
    {
      title: '账号 / Pipeline',
      key: 'identity',
      width: 260,
      render: (_, row) => (
        <Space direction="vertical" size={4}>
          {row.email ? <CopyableText value={row.email} label="邮箱" /> : <Text strong>注册中 / Pipeline #{row.pipeline.id}</Text>}
          <LinkedIdBadges pipelineId={row.pipeline.id} accountId={row.accountId} paymentLinkId={row.pipeline.payment_link_id} jobId={row.currentJob?.id || row.latestJob?.id} />
          <Space size={4} wrap>
            <Tag>{row.pipeline.preset || 'pipeline'}</Tag>
            {row.pipelines.length > 1 && <Tag color="blue">{row.pipelines.length} pipelines</Tag>}
          </Space>
        </Space>
      ),
    },
    {
      title: '当前池',
      key: 'stage',
      width: 170,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Text strong>{stageLabel(row.pipeline.current_stage)}</Text>
          <Text code>{row.pipeline.current_stage || '-'}</Text>
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 145,
      render: (_, row) => <StatusPair pipeline={row.pipeline} job={row.currentJob} />,
    },
    {
      title: '进度',
      key: 'progress',
      width: 160,
      render: (_, row) => (
        <Progress
          percent={progressPercent(row.pipeline)}
          size="small"
          status={progressStatus(row.pipeline.status)}
          format={() => `${row.pipeline.completed_steps || 0}/${row.pipeline.total_steps || 0}`}
        />
      ),
    },
    {
      title: '耗时',
      key: 'duration',
      width: 170,
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Text>{formatDuration(row.pipeline.created_at, row.pipeline.finished_at)}</Text>
          <Text type="secondary">{formatDateTime(row.pipeline.updated_at || row.pipeline.created_at)}</Text>
        </Space>
      ),
    },
    {
      title: '账号状态 / Token',
      key: 'account',
      width: 220,
      render: (_, row) => row.account ? (
        <Space direction="vertical" size={4}>
          <StatusTag status={row.account.status} />
          <TokenBadges accessToken={row.account.has_access_token ? 'yes' : ''} refreshToken={row.account.has_refresh_token ? 'yes' : ''} codexRt={row.account.codex_token_has_refresh_token ? 'yes' : ''} />
          <Sub2ApiBadge status={row.account.sub2api_status} />
        </Space>
      ) : <Text type="secondary">-</Text>,
    },
    {
      title: '代理 / 支付',
      key: 'proxy',
      width: 230,
      render: (_, row) => (
        <Space direction="vertical" size={4}>
          <CopyableText value={row.pipeline.proxy_url || row.account?.proxy_url || ''} label="代理" />
          {row.pipeline.payment_link_id ? <Tag color="purple">Link #{row.pipeline.payment_link_id}</Tag> : <Text type="secondary">no payment link</Text>}
        </Space>
      ),
    },
    {
      title: '错误',
      key: 'error',
      ellipsis: true,
      render: (_, row) => {
        const error = mergedError(row)
        return error ? <Tooltip title={error}><Text type="danger" ellipsis>{error}</Text></Tooltip> : <Text type="secondary">-</Text>
      },
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 110,
      render: (_, row) => <Button size="small" onClick={() => openDetail(row.pipeline.id)}>详情 / 日志</Button>,
    },
  ]

  const detailPipeline = detail?.pipeline
  const detailJobs = useMemo(() => detail?.jobs || [], [detail])
  const jobsByStage = useMemo(() => {
    const out = new Map<string, Job[]>()
    for (const job of detailJobs) {
      const list = out.get(job.type) || []
      list.push(job)
      out.set(job.type, sortJobs(list))
    }
    return out
  }, [detailJobs])

  const stageRows: StageBreakdownRow[] = useMemo(() => {
    if (!detailPipeline) return []
    return (detailPipeline.stages || []).map((stage, index) => ({
      key: `${stage}-${index}`,
      stage,
      index,
      job: jobsByStage.get(stage)?.[0],
    }))
  }, [detailPipeline, jobsByStage])

  const stageColumns: TableColumnsType<StageBreakdownRow> = [
    {
      title: '模块 / Stage',
      key: 'stage',
      render: (_, row) => (
        <Space direction="vertical" size={2}>
          <Text strong>{row.index + 1}. {stageLabel(row.stage)}</Text>
          <Text code>{row.stage}</Text>
        </Space>
      ),
    },
    {
      title: 'Job',
      key: 'job',
      width: 100,
      render: (_, row) => row.job ? <Tag color="blue">#{row.job.id}</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: '状态',
      key: 'status',
      width: 130,
      render: (_, row) => row.job ? <StatusTag status={row.job.status} /> : <Text type="secondary">pending</Text>,
    },
    {
      title: 'Attempt',
      key: 'attempt',
      width: 110,
      render: (_, row) => row.job ? `${row.job.attempt}/${row.job.max_attempts}` : '-',
    },
    {
      title: '耗时',
      key: 'duration',
      width: 120,
      render: (_, row) => row.job ? formatDuration(row.job.started_at, row.job.finished_at) : '-',
    },
    {
      title: '创建 / 完成',
      key: 'time',
      width: 230,
      render: (_, row) => row.job ? (
        <Space direction="vertical" size={2}>
          <Text>{formatDateTime(row.job.created_at)}</Text>
          <Text type="secondary">{formatDateTime(row.job.finished_at)}</Text>
        </Space>
      ) : '-',
    },
    {
      title: '错误',
      key: 'error',
      ellipsis: true,
      render: (_, row) => row.job?.error ? <Tooltip title={row.job.error}><Text type="danger" ellipsis>{row.job.error}</Text></Tooltip> : <Text type="secondary">-</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 250,
      render: (_, row) => {
        const registerIdx = (detailPipeline?.stages || []).indexOf('register')
        const canRetry = Boolean(
          row.job &&
          row.job.status === 'failed' &&
          detailPipeline?.status === 'failed' &&
          detailPipeline?.current_stage === row.stage &&
          registerIdx >= 0 &&
          row.index > registerIdx,
        )
        const canForceStop = Boolean(
          row.job &&
          row.job.status !== 'succeeded' &&
          row.job.status !== 'failed',
        )
        return (
          <Space size={4} wrap>
            <Button size="small" disabled={!row.job} onClick={() => row.job && setSelectedLogJobId(row.job.id)}>日志</Button>
            {canForceStop && (
              <Popconfirm title="强制停止该 job 并将 pipeline 标记为失败？" onConfirm={() => row.job && detailPipeline && forceStopJob(row.job.id, detailPipeline.id)}>
                <Button
                  size="small"
                  danger
                  loading={row.job ? stoppingJobId === row.job.id : false}
                >
                  强制停止
                </Button>
              </Popconfirm>
            )}
            {canRetry && (
              <Button
                size="small"
                type="primary"
                ghost
                disabled={row.job ? retryingJobId === row.job.id : false}
                loading={row.job ? retryingJobId === row.job.id : false}
                onClick={() => row.job && detailPipeline && retryStageJob(row.job.id, detailPipeline.id)}
              >
                重试
              </Button>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <PageScaffold
      title="Jobs 追踪"
      description="按账号或注册中 pipeline 聚合任务状态：看当前池、进度、耗时和模块日志，不再一条 job 一张卡。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="追踪行" value={summary.total} tone="primary" />
        <StatCard label="running" value={summary.running} tone={summary.running ? 'info' : 'default'} />
        <StatCard label="queued/created" value={summary.queued} tone={summary.queued ? 'warning' : 'default'} />
        <StatCard label="succeeded" value={summary.succeeded} tone="success" />
        <StatCard label="failed/cancelled" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="注册中" value={summary.pendingAccount} tone={summary.pendingAccount ? 'warning' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="账号级 Jobs 表"
        description="一行代表一个账号当前链路；未产生账号前按 Pipeline 单独展示。点击详情查看模块分解和原始日志。"
        actions={(
          <CardToolbar>
            <Input prefix={<SearchOutlined />} allowClear placeholder="搜索邮箱 / ID / stage" value={query} onChange={(evt) => setQuery(evt.target.value)} style={{ width: 220 }} />
            <Select allowClear placeholder="Pipeline 状态" value={statusFilter} onChange={setStatusFilter} style={{ width: 160 }} options={['created', 'queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'].map((value) => ({ value, label: value }))} />
            <Select allowClear placeholder="当前池" value={stageFilter} onChange={setStageFilter} style={{ width: 190 }} options={stageOptions} />
            <Button onClick={() => { setQuery(''); setStatusFilter(undefined); setStageFilter(undefined) }}>清空筛选</Button>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>
          </CardToolbar>
        )}
      />

      <Table
        className="surface-table"
        rowKey="key"
        columns={tableColumns}
        dataSource={filteredRows}
        loading={loading}
        scroll={{ x: 1500 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        onRow={(row) => ({ onDoubleClick: () => openDetail(row.pipeline.id) })}
      />

      <PopupCard open={!!detail || detailLoading} onCancel={closeDetail} width={1120} title={detailPipeline ? `Pipeline #${detailPipeline.id} 模块日志` : '加载中'} footer={null} className="popup-card-wide">
        {detailPipeline ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <ActionCard
              title={detailPipeline.account_id ? `Account #${detailPipeline.account_id}` : `注册中 Pipeline #${detailPipeline.id}`}
              description={<LinkedIdBadges pipelineId={detailPipeline.id} accountId={detailPipeline.account_id} paymentLinkId={detailPipeline.payment_link_id} jobId={selectedLogJobId} />}
              actions={<StatusTag status={detailPipeline.status} />}
            />
            <SummaryGrid>
              <StatCard label="当前池" value={stageLabel(detailPipeline.current_stage)} hint={detailPipeline.current_stage || '-'} tone="primary" />
              <StatCard label="进度" value={`${detailPipeline.completed_steps || 0}/${detailPipeline.total_steps || 0}`} tone="info" />
              <StatCard label="耗时" value={formatDuration(detailPipeline.created_at, detailPipeline.finished_at)} />
              <StatCard label="Jobs" value={detailJobs.length} />
            </SummaryGrid>
            <KeyValueGrid>
              <KeyValue label="preset" value={<Text code>{detailPipeline.preset || '-'}</Text>} />
              <KeyValue label="stop_after" value={<Text code>{detailPipeline.stop_after || '-'}</Text>} />
              <KeyValue label="proxy" value={<CopyableText value={detailPipeline.proxy_url} label="代理" />} />
              <KeyValue label="updated" value={formatDateTime(detailPipeline.updated_at || detailPipeline.created_at)} />
            </KeyValueGrid>
            <ErrorCallout error={detailPipeline.error} />
            <Table rowKey="key" size="small" columns={stageColumns} dataSource={stageRows} pagination={false} scroll={{ x: 960 }} />
            <ActionCard title={selectedLogJobId ? `Job #${selectedLogJobId} 原始日志` : '原始日志'} description="选择上方模块行的 job 查看对应 transcript。" />
            {selectedLogJobId ? (
              <JobLogPanel jobId={selectedLogJobId} onTerminal={() => { reload(); openDetail(detailPipeline.id) }} />
            ) : (
              <Empty description="该 pipeline 暂无 job 日志" />
            )}
          </Space>
        ) : (
          <Empty description="正在加载详情" />
        )}
      </PopupCard>
    </PageScaffold>
  )
}
