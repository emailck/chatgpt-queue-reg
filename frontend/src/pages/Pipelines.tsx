import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormInstance } from 'antd'
import { Button, Col, Form, Input, InputNumber, Popconfirm, Progress, Radio, Row, Select, Space, Tag, Typography, message } from 'antd'
import { BugOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { ActionCard, CardToolbar, EntityCard, EntityGrid, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, LinkedIdBadges, ProgressLine, SelectionSummary } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime, formatDuration } from '@/lib/api'
import { stageLabel } from '@/lib/contracts'

const { Text } = Typography

const FULL_CHAIN_PRESET = 'full_chain'
const PIPELINE_STATUS_KEYS = ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']

const STOP_OPTIONS = [
  { value: '', label: '跑完整链路' },
  { value: 'register', label: '注册后停止' },
  { value: 'payment_link', label: '长链后停止' },
  { value: 'pp_long_link', label: 'PP 长链接后停止' },
  { value: 'payment', label: '付款模块后停止' },
  { value: 'chatgpt_session', label: 'Session 标准化后停止' },
  { value: 'sub2api_sync', label: 'sub2api 同步后停止' },
  { value: 'openai_oauth', label: 'OpenAI OAuth RT 后停止' },
  { value: 'sso_oauth', label: 'SSO OAuth RT 后停止' },
  { value: 'codex_invitation', label: 'Codex 邀请后停止' },
  { value: 'active', label: 'Codex 激活后停止' },
  { value: 'workspace_join', label: 'Workspace 加入后停止' },
  { value: 'codex_token', label: 'Codex 令牌后停止' },
]

const STAGE_OPTIONS: { value: string; label: string }[] = [
  { value: 'register', label: '注册' },
  { value: 'tinkmail_email_register', label: '自动注册 TinkMail 邮箱' },
  { value: 'payment_link', label: '生成长链' },
  { value: 'pp_long_link', label: 'PP 长链接' },
  { value: 'payment', label: '付款' },
  { value: 'chatgpt_session', label: 'ChatGPT Session' },
  { value: 'openai_oauth', label: 'OpenAI OAuth RT' },
  { value: 'sso_oauth', label: 'SSO OAuth RT' },
  { value: 'codex_invitation', label: 'Codex 邀请' },
  { value: 'codex_batch_invite', label: '批量 Codex 邀请' },
  { value: 'active', label: 'Codex 激活' },
  { value: 'workspace_join', label: 'Workspace 加入' },
  { value: 'codex_token', label: '创建 Codex 令牌' },
  { value: 'sub2api_sync', label: 'sub2api 同步' },
]

const PRESET_OPTIONS: { value: string; label: string }[] = [
  { value: 'full_chain', label: '完整链路 register→payment_link→payment→chatgpt_session→sub2api_sync' },
  { value: 'register_only', label: '仅注册 register' },
  { value: 'tinkmail_email_register', label: '自动注册 TinkMail 邮箱 tinkmail_email_register' },
  { value: 'register_with_refresh_token', label: '注册+RT register→chatgpt_session→openai_oauth→sub2api_sync' },
  { value: 'account_paid', label: '全自动付费 register→payment_link→payment' },
  { value: 'account_paid_with_refresh_token', label: '付费+RT 全部6步' },
  { value: 'link_only', label: '只到长链 register→payment_link' },
  { value: 'pp_long_link_only', label: '已有号生成 PP 长链接 pp_long_link' },
  { value: 'register_pp_long_link', label: '注册→PP 长链接 register→pp_long_link' },
  { value: 'refresh_token_only', label: '已有号补RT chatgpt_session→openai_oauth→sub2api_sync' },
  { value: 'codex_invitation_only', label: 'Codex 邀请 codex_invitation' },
  { value: 'codex_invite_sso_active', label: 'Codex 邀请+SSO+激活 codex_invitation→sso_oauth→active' },
  { value: 'codex_batch_invite_active', label: '批量 Codex 邀请→统一激活 codex_batch_invite' },
  { value: 'sso_workspace_join', label: 'SSO→Workspace 加入 sso_oauth→workspace_join' },
  { value: 'workspace_join_only', label: '仅 Workspace 加入 workspace_join' },
  { value: 'workspace_request_only', label: '仅 Workspace Request 申请加入 workspace_join(request)' },
  { value: 'register_workspace_request', label: '注册→Workspace Request 申请加入 register→workspace_join(request)' },
  { value: 'team_codex_token_sub2api', label: '切换Team→创建Codex令牌→上传sub codex_token→sub2api_sync' },
  { value: 'register_team_codex_token_sub2api', label: '注册→切换Team→创建Codex令牌→上传sub' },
  { value: 'active_only', label: '仅 Codex 激活 active' },
]

function pipelinePresetLabel(preset: string): string {
  return preset === FULL_CHAIN_PRESET ? '完整链路' : preset || '-'
}

function stopAfterLabel(stopAfter?: string | null): string {
  return stopAfter ? stageLabel(stopAfter) : '跑完全部'
}

function statusTone(status: string): 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'succeeded') return 'success'
  if (status === 'failed' || status === 'interrupted') return 'danger'
  if (status === 'running') return 'info'
  if (status === 'queued') return 'warning'
  return 'default'
}

interface Pipeline {
  id: number
  preset: string
  stages: string[]
  stop_after: string
  status: string
  current_stage: string
  total_steps: number
  completed_steps: number
  account_id: number | null
  payment_link_id: number | null
  proxy_url: string
  error: string
  created_at: string | null
  finished_at: string | null
  updated_at: string | null
}

interface Job {
  id: number
  type: string
  status: string
  pipeline_id: number | null
  account_id: number | null
  payment_link_id: number | null
  attempt: number
  max_attempts: number
  error: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  result: Record<string, unknown>
}

interface PipelineDetail {
  pipeline: Pipeline
  jobs: Job[]
}

function timeValue(value?: string | null): number {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function latestJobTime(job: Job): number {
  return Math.max(timeValue(job.finished_at), timeValue(job.started_at), timeValue(job.created_at))
}

function sortJobs(jobs: Job[]): Job[] {
  return [...jobs].sort((a, b) => latestJobTime(b) - latestJobTime(a))
}

function mergeJobs(...groups: Job[][]): Job[] {
  const seen = new Set<number>()
  const out: Job[] = []
  for (const group of groups) {
    for (const job of group) {
      if (seen.has(job.id)) continue
      seen.add(job.id)
      out.push(job)
    }
  }
  return sortJobs(out)
}

function resultText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean).join(', ')
  if (value === undefined || value === null) return ''
  return String(value || '').trim()
}

function jobResultHighlights(job: Job): { label: string; value: string }[] {
  const result = job.result || {}
  const rows: { label: string; value: string }[] = []
  const add = (label: string, key: string) => {
    const value = resultText(result[key])
    if (value) rows.push({ label, value })
  }
  if (job.type === 'codex_invitation') {
    add('受邀邮箱', 'invited_email')
    add('SSO邮箱', 'sso_email')
    add('全部邀请邮箱', 'emails')
    add('邀请母号', 'source_email')
  } else if (job.type === 'codex_batch_invite') {
    add('全部受邀邮箱', 'invited_emails')
    add('激活子流程', 'activation_pipeline_ids')
    add('失败数量', 'failed_count')
  } else if (job.type === 'sso_oauth') {
    add('SSO邮箱', 'sso_email')
    add('ChatGPT Account', 'chatgpt_account_id')
    add('RT ID', 'refresh_token_id')
  } else if (job.type === 'active') {
    add('激活邮箱', 'email')
    add('ChatGPT Account', 'chatgpt_account_id')
    add('激活结果', 'activated')
  } else if (job.type === 'workspace_join') {
    add('Workspace', 'workspace_ids')
    add('动作', 'route')
    add('成功数', 'success_count')
    add('失败数', 'failed_count')
    add('已切换空间', 'switched_workspace_id')
    add('切换结果', 'workspace_session_switched')
  } else if (job.type === 'codex_token') {
    add('邮箱', 'email')
    add('Workspace', 'workspace_ids')
    add('成功数', 'success_count')
    add('失败数', 'failed_count')
  } else if (job.type === 'openai_oauth') {
    add('RT ID', 'refresh_token_id')
    add('expires_in', 'expires_in')
    add('sub状态', 'sub2api_status')
  } else if (job.type === 'sub2api_sync') {
    add('RT ID', 'refresh_token_id')
    add('sub账号', 'sub2api_account_id')
    add('sub状态', 'sub2api_status')
    add('调度', 'schedulable')
  }
  return rows
}

export default function Pipelines() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()
  const [detail, setDetail] = useState<PipelineDetail | null>(null)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(18)

  const openCreate = useCallback(() => {
    form.resetFields()
    setCreateOpen(true)
  }, [form])

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<Pipeline[]>('/pipelines?limit=200')
      setPipelines(data)
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

  const counts = useMemo(() => {
    const out: Record<string, number> = {}
    for (const row of pipelines) out[row.status] = (out[row.status] || 0) + 1
    return out
  }, [pipelines])

  const openDetail = useCallback(async (id: number) => {
    try {
      const data = await apiFetch<PipelineDetail>(`/pipelines/${id}`)
      if (data.pipeline.account_id) {
        const accountJobs = await apiFetch<Job[]>(`/jobs?account_id=${data.pipeline.account_id}&limit=500`)
        const standaloneJobs = (accountJobs || []).filter((job) => job.pipeline_id === null)
        setDetail({ ...data, jobs: mergeJobs(data.jobs || [], standaloneJobs) })
      } else {
        setDetail({ ...data, jobs: sortJobs(data.jobs || []) })
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载详情失败')
    }
  }, [])

  const cancelPipeline = useCallback(async (id: number) => {
    try {
      await apiFetch(`/pipelines/${id}/cancel`, { method: 'POST' })
      message.success('已请求取消')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '取消失败')
    }
  }, [reload])

  const deletePipeline = useCallback(async (id: number) => {
    try {
      await apiFetch(`/pipelines/${id}`, { method: 'DELETE' })
      message.success('已删除')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }, [reload])

  const batchDelete = useCallback(async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number; skipped: { id: number; reason: string }[] }>(
        '/pipelines/batch-delete',
        {
          method: 'POST',
          body: JSON.stringify({ ids: selected.map((id) => Number(id)) }),
        },
      )
      message.success(
        resp.skipped?.length
          ? `已删除 ${resp.deleted}，${resp.skipped.length} 条因运行中跳过`
          : `已删除 ${resp.deleted}`,
      )
      setSelected([])
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '批量删除失败')
    }
  }, [reload, selected])

  const openPipelineDebug = useCallback(async (pipeline: Pipeline) => {
    if (!pipeline.account_id && !pipeline.payment_link_id) {
      message.warning('该 pipeline 还没有账号或长链，无法注入身份调试')
      return
    }
    try {
      const resp = await apiFetch<{ session_id: number; har_path: string }>('/browser-debug/open', {
        method: 'POST',
        body: JSON.stringify({
          account_id: pipeline.account_id,
          payment_link_id: pipeline.payment_link_id,
          pipeline_id: pipeline.id,
          browser_type: 'camoufox',
          inject_cookies: true,
          inject_local_storage: true,
          inject_fingerprint: true,
          record_har: true,
        }),
      })
      message.success(`Camoufox session #${resp.session_id} 已打开${resp.har_path ? `，HAR: ${resp.har_path}` : ''}`)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '调起 Camoufox 失败')
    }
  }, [])

  const submitCreate = async () => {
    const values = await form.validateFields()
    const mode = String(values.mode || 'preset')
    const stopAfter = String(values.stop_after || '')
    const body: Record<string, unknown> = {
      count: Number(values.count || 1),
    }

    if (mode === 'custom') {
      const stages = (values.stages as string[]) || []
      if (!stages.length) {
        message.warning('请至少选择一个阶段')
        return
      }
      body.stages = stages
      body.stop_after = stopAfter || undefined
    } else {
      const preset = String(values.preset || 'full_chain')
      body.preset = preset
      body.stop_after = stopAfter || undefined
    }

    const includesWorkspaceJoin = mode === 'custom'
      ? Array.isArray(body.stages) && body.stages.includes('workspace_join')
      : body.preset === 'sso_workspace_join'
        || body.preset === 'workspace_join_only'
        || body.preset === 'workspace_request_only'
        || body.preset === 'register_workspace_request'

    const includesCodex = mode === 'custom'
      ? Array.isArray(body.stages) && (body.stages.includes('codex_invitation') || body.stages.includes('codex_batch_invite'))
      : body.preset === 'codex_invitation_only' || body.preset === 'codex_invite_sso_active' || body.preset === 'codex_batch_invite_active'

    const includesPPLongLink = mode === 'custom'
      ? Array.isArray(body.stages) && body.stages.includes('pp_long_link')
      : body.preset === 'pp_long_link_only' || body.preset === 'register_pp_long_link'
    const includesCodexToken = mode === 'custom'
      ? Array.isArray(body.stages) && body.stages.includes('codex_token')
      : body.preset === 'team_codex_token_sub2api' || body.preset === 'register_team_codex_token_sub2api'
    const includesRegister = mode === 'custom'
      ? Array.isArray(body.stages) && body.stages.includes('register')
      : String(body.preset || '').startsWith('register') || ['full_chain', 'account_paid', 'account_paid_with_refresh_token', 'link_only'].includes(String(body.preset || ''))
    if (includesRegister) {
      for (const key of ['email', 'password']) {
        const value = values[key]
        if (value !== undefined && value !== null && value !== '') {
          body[key] = value
        }
      }
    }

    const includesTinkMailRegister = mode === 'custom'
      ? Array.isArray(body.stages) && body.stages.includes('tinkmail_email_register')
      : body.preset === 'tinkmail_email_register'
    if (includesTinkMailRegister) {
      const stageInputs = (body.stage_inputs as Record<string, Record<string, unknown>> | undefined) || {}
      const tinkInput: Record<string, unknown> = {
        enabled: values.tinkmail_enabled !== false,
        acquire_proxy: values.tinkmail_acquire_proxy === true,
      }
      const tinkFields: Array<[string, string]> = [
        ['tinkmail_account', 'account'],
        ['tinkmail_password', 'password'],
        ['tinkmail_secure_email', 'secure_email'],
        ['tinkmail_proxy_url', 'proxy_url'],
      ]
      for (const [formKey, payloadKey] of tinkFields) {
        const value = values[formKey]
        if (value !== undefined && value !== null && value !== '') tinkInput[payloadKey] = value
      }
      stageInputs.tinkmail_email_register = { ...(stageInputs.tinkmail_email_register || {}), ...tinkInput }
      body.stage_inputs = stageInputs
    }
    if (includesWorkspaceJoin) {
      const workspaceFields = [
        'account_id',
        'workspace_id',
        'workspace_ids',
        'workspace_account_id',
        'workspace_account_ids',
        'route',
        'interval_ms',
        'max_retries',
        'retry_backoff_ms',
        'refresh_before_request',
        'allow_partial',
        'switch_after_join',
        'access_token',
        'refresh_token',
        'id_token',
        'dry_run',
      ]
      for (const key of workspaceFields) {
        const value = values[key]
        if (value !== undefined && value !== null && value !== '') {
          body[key] = value
        }
      }
    }

    if (includesPPLongLink) {
      const stageInputs = (body.stage_inputs as Record<string, Record<string, unknown>> | undefined) || {}
      const ppInput: Record<string, unknown> = {}
      const ppFields: Array<[string, string]> = [
        ['pp_country', 'country'],
        ['pp_currency', 'currency'],
        ['pp_target_amount', 'target_amount'],
        ['pp_create_proxy_url', 'create_proxy_url'],
        ['pp_followup_proxy_url', 'followup_proxy_url'],
        ['pp_approve_proxy_url', 'approve_proxy_url'],
        ['pp_proxy_url', 'proxy_url'],
        ['pp_access_token', 'access_token'],
        ['pp_max_retries', 'max_retries'],
        ['pp_retry_backoff_ms', 'retry_backoff_ms'],
      ]
      for (const [formKey, payloadKey] of ppFields) {
        const value = values[formKey]
        if (value !== undefined && value !== null && value !== '') ppInput[payloadKey] = value
      }
      stageInputs.pp_long_link = { ...(stageInputs.pp_long_link || {}), ...ppInput }
      body.stage_inputs = stageInputs
    }

    if (includesCodexToken) {
      const codexTokenFields = [
        'account_id',
        'workspace_id',
        'workspace_ids',
        'workspace_account_id',
        'workspace_account_ids',
        'token_name',
        'ttl',
        'scope',
        'interval_ms',
        'max_retries',
        'retry_backoff_ms',
        'allow_partial',
        'upload_multiple',
        'dry_run',
      ]
      for (const key of codexTokenFields) {
        const value = values[key]
        if (value !== undefined && value !== null && value !== '') {
          body[key] = value
        }
      }
    }

    const includesOAuthRt = mode === 'custom'
      ? Array.isArray(body.stages) && (body.stages.includes('openai_oauth') || body.stages.includes('sso_oauth') || body.stages.includes('chatgpt_session'))
      : body.preset === 'refresh_token_only' || body.preset === 'sso_rt_only'
    if (includesOAuthRt) {
      for (const key of ['account_id', 'email', 'refresh_token_id', 'dry_run']) {
        const value = values[key]
        if (value !== undefined && value !== null && value !== '') {
          body[key] = value
        }
      }
    }

    if (includesCodex) {
      const codexFields = [
        'email_id',
        'email',
        'inviter_account_id',
        'inviter_email',
        'inviter_list',
        'inviter_emails',
        'inviter_account_ids',
        'invite_count_per_inviter',
        'activate_after_invite',
        'source_type',
        'invite_count',
        'prefix_len',
        'domain',
        'access_token',
        'chatgpt_account_id',
        'dry_run',
      ]
      for (const key of codexFields) {
        const value = values[key]
        if (value !== undefined && value !== null && value !== '') {
          body[key] = value
        }
      }
    }

    try {
      const resp = await apiFetch<{ pipeline_ids: number[] }>(
        '/pipelines',
        { method: 'POST', body: JSON.stringify(body) },
      )
      const label = mode === 'custom' ? '自定义' : '预设'
      message.success(`已创建 ${resp.pipeline_ids.length} 条${label} pipeline`)
      setCreateOpen(false)
      form.resetFields()
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
    }
  }

  const toggleSelected = (id: number, checked: boolean) => {
    setSelected((prev) => checked ? [...prev, id] : prev.filter((item) => Number(item) !== id))
  }

  return (
    <PageScaffold
      title="任务队列"
      description="默认创建完整链路 register → payment_link → payment → chatgpt_session → sub2api_sync，也可以在任一模块边界停止。"
      actions={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建链路</Button>}
    >
      <SummaryGrid>
        <StatCard label="Pipeline" value={pipelines.length} hint="最近 200 条" tone="primary" />
        {PIPELINE_STATUS_KEYS.map((key) => (
          <StatCard key={key} label={key} value={counts[key] || 0} tone={key === 'failed' ? 'danger' : key === 'running' ? 'info' : key === 'succeeded' ? 'success' : 'default'} />
        ))}
        <StatCard label="已选择" value={selected.length} hint="批量删除会跳过运行中" tone={selected.length ? 'warning' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="链路操作"
        description="选择预设链路或自定义阶段组合创建 Pipeline。代理、接码、支付等参数在各 WorkPool / ResourcePool 卡片配置。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>
            <Popconfirm title={`确认删除选中的 ${selected.length} 条?（运行/排队中将跳过）`} onConfirm={batchDelete} disabled={!selected.length}>
              <Button icon={<DeleteOutlined />} danger disabled={!selected.length}>批量删除</Button>
            </Popconfirm>
          </CardToolbar>
        )}
      />

      <EntityGrid
        items={pipelines}
        page={page}
        pageSize={pageSize}
        onPageChange={(nextPage, nextPageSize) => { setPage(nextPage); setPageSize(nextPageSize) }}
        showSizeChanger
        renderItem={(pipeline) => (
          <EntityCard
            key={pipeline.id}
            title={`Pipeline #${pipeline.id}`}
            subtitle={`${pipelinePresetLabel(pipeline.preset)} / 停止点：${stopAfterLabel(pipeline.stop_after)}`}
            status={<StatusTag status={pipeline.status} />}
            tone={statusTone(pipeline.status)}
            selected={selected.includes(pipeline.id)}
            onSelect={(checked) => toggleSelected(pipeline.id, checked)}
            badges={<LinkedIdBadges accountId={pipeline.account_id} paymentLinkId={pipeline.payment_link_id} />}
            footer={formatDateTime(pipeline.created_at)}
            actions={(
              <>
                <Button size="small" onClick={() => openDetail(pipeline.id)}>详情</Button>
                <Button size="small" icon={<BugOutlined />} disabled={!pipeline.account_id && !pipeline.payment_link_id} onClick={() => openPipelineDebug(pipeline)}>抓 HAR</Button>
                {(pipeline.status === 'queued' || pipeline.status === 'running') ? (
                  <Popconfirm title="取消该 pipeline?" onConfirm={() => cancelPipeline(pipeline.id)}>
                    <Button size="small" danger>取消</Button>
                  </Popconfirm>
                ) : (
                  <Popconfirm title="删除该 pipeline?" onConfirm={() => deletePipeline(pipeline.id)}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                )}
              </>
            )}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <Progress
                percent={Math.round((pipeline.completed_steps / Math.max(pipeline.total_steps, 1)) * 100)}
                size="small"
                format={() => `${pipeline.completed_steps}/${pipeline.total_steps}`}
                status={pipeline.status === 'failed' ? 'exception' : pipeline.status === 'succeeded' ? 'success' : 'active'}
              />
              <KeyValueGrid>
                <KeyValue label="当前 Stage" value={<Text>{stageLabel(pipeline.current_stage)}</Text>} />
                <KeyValue label="耗时" value={formatDuration(pipeline.created_at, pipeline.finished_at)} />
                <KeyValue label="代理" value={<CopyableText value={pipeline.proxy_url} label="代理" />} />
                <KeyValue label="更新" value={formatDateTime(pipeline.updated_at)} />
              </KeyValueGrid>
              <ErrorCallout error={pipeline.error} />
            </Space>
          </EntityCard>
        )}
      />

      <PopupCard open={createOpen} onCancel={() => setCreateOpen(false)} title="创建 pipeline" onOk={submitCreate} okText="创建" width={600}>
        <CreateForm form={form} />
      </PopupCard>

      <PopupCard
        open={!!detail}
        onCancel={() => setDetail(null)}
        title={detail ? `Pipeline #${detail.pipeline.id}` : ''}
        width={980}
        footer={null}
        className="popup-card-wide"
      >
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <ActionCard
              title="Pipeline 详情"
              description={detail.pipeline.status === 'succeeded' && detail.pipeline.stop_after ? `已停在 ${stageLabel(detail.pipeline.stop_after)} 模块边界` : stopAfterLabel(detail.pipeline.stop_after)}
              actions={<Button size="small" icon={<BugOutlined />} disabled={!detail.pipeline.account_id && !detail.pipeline.payment_link_id} onClick={() => openPipelineDebug(detail.pipeline)}>Camoufox 抓 HAR</Button>}
            />
            <SummaryGrid>
              <StatCard label="状态" value={<StatusTag status={detail.pipeline.status} />} tone={statusTone(detail.pipeline.status)} />
              <StatCard label="当前 Stage" value={stageLabel(detail.pipeline.current_stage)} hint={detail.pipeline.current_stage} tone="info" />
              <StatCard label="进度" value={`${detail.pipeline.completed_steps}/${detail.pipeline.total_steps}`} tone="primary" />
              <StatCard label="耗时" value={formatDuration(detail.pipeline.created_at, detail.pipeline.finished_at)} />
              <StatCard label="独立 Jobs" value={detail.jobs.filter((job) => job.pipeline_id === null).length} tone={detail.jobs.some((job) => job.pipeline_id === null) ? 'info' : 'default'} />
            </SummaryGrid>
            <KeyValueGrid>
              <KeyValue label="账号" value={detail.pipeline.account_id || '-'} />
              <KeyValue label="长链" value={detail.pipeline.payment_link_id || '-'} />
              <KeyValue label="代理" value={<CopyableText value={detail.pipeline.proxy_url} label="代理" />} />
              <KeyValue label="完成时间" value={formatDateTime(detail.pipeline.finished_at)} />
            </KeyValueGrid>
            <Space size={4} wrap>
              {detail.pipeline.stages.map((stage) => (
                <Tag key={stage} color={stage === detail.pipeline.current_stage ? 'processing' : 'default'}>{stageLabel(stage)} <Text type="secondary">{stage}</Text></Tag>
              ))}
            </Space>
            <ErrorCallout error={detail.pipeline.error} />
            <div className="entity-grid">
              {detail.jobs.map((job) => (
                <EntityCard
                  key={job.id}
                  title={`Job #${job.id}`}
                  subtitle={<Space size={4}><Text code>{job.type}</Text>{job.pipeline_id === null && <Tag color="geekblue">账号级独立</Tag>}</Space>}
                  status={<StatusTag status={job.status} />}
                  tone={statusTone(job.status)}
                  footer={formatDateTime(job.created_at)}
                  actions={<Button size="small" onClick={() => setLogJobId(job.id)}>原始日志</Button>}
                >
                  <Space direction="vertical" size="small" style={{ width: '100%' }}>
                    <ProgressLine current={job.attempt} total={job.max_attempts} status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : 'active'} />
                    <KeyValueGrid>
                      <KeyValue label="Stage" value={stageLabel(job.type)} />
                      <KeyValue label="耗时" value={formatDuration(job.started_at, job.finished_at)} />
                    </KeyValueGrid>
                    {jobResultHighlights(job).length > 0 && (
                      <KeyValueGrid>
                        {jobResultHighlights(job).map((item) => (
                          <KeyValue key={item.label} label={item.label} value={<CopyableText value={item.value} label={item.label} />} />
                        ))}
                      </KeyValueGrid>
                    )}
                    <ErrorCallout error={job.error} />
                  </Space>
                </EntityCard>
              ))}
            </div>
          </Space>
        )}
      </PopupCard>

      <PopupCard open={logJobId !== null} onCancel={() => setLogJobId(null)} title={logJobId ? `Job #${logJobId} 原始日志` : ''} width={900} footer={null}>
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </PopupCard>
    </PageScaffold>
  )
}

function CreateForm({ form }: { form: FormInstance }) {
  const [mode, setMode] = useState<string>('preset')
  const [savedConfigs, setSavedConfigs] = useState<{ id: number; name: string; stages: string[]; stop_after: string }[]>([])
  const [saveName, setSaveName] = useState('')
  const [loadingConfigs, setLoadingConfigs] = useState(false)
  const selectedMode = Form.useWatch('mode', form) || mode
  const selectedPreset = Form.useWatch('preset', form)
  const selectedStages = Form.useWatch('stages', form) as string[] | undefined
  const oauthEmail = Form.useWatch('email', form)
  const oauthAccountId = Form.useWatch('account_id', form)
  const showCodexFields = selectedMode === 'preset'
    ? selectedPreset === 'codex_invitation_only' || selectedPreset === 'codex_invite_sso_active'
    : Array.isArray(selectedStages) && selectedStages.includes('codex_invitation')
  const showBatchCodexFields = selectedMode === 'preset'
    ? selectedPreset === 'codex_batch_invite_active'
    : Array.isArray(selectedStages) && selectedStages.includes('codex_batch_invite')
  const showWorkspaceJoinFields = selectedMode === 'preset'
    ? selectedPreset === 'sso_workspace_join'
      || selectedPreset === 'workspace_join_only'
      || selectedPreset === 'workspace_request_only'
      || selectedPreset === 'register_workspace_request'
    : Array.isArray(selectedStages) && selectedStages.includes('workspace_join')
  const isWorkspaceRequestPreset = selectedMode === 'preset'
    && (selectedPreset === 'workspace_request_only' || selectedPreset === 'register_workspace_request')
  const showPPLongLinkFields = selectedMode === 'preset'
    ? selectedPreset === 'pp_long_link_only' || selectedPreset === 'register_pp_long_link'
    : Array.isArray(selectedStages) && selectedStages.includes('pp_long_link')
  const showCodexTokenFields = selectedMode === 'preset'
    ? selectedPreset === 'team_codex_token_sub2api' || selectedPreset === 'register_team_codex_token_sub2api'
    : Array.isArray(selectedStages) && selectedStages.includes('codex_token')
  const showOAuthRtFields = selectedMode === 'preset'
    ? selectedPreset === 'refresh_token_only' || selectedPreset === 'sso_rt_only'
    : Array.isArray(selectedStages) && (selectedStages.includes('openai_oauth') || selectedStages.includes('sso_oauth') || selectedStages.includes('chatgpt_session'))
  const showRegisterFields = selectedMode === 'preset'
    ? ['full_chain', 'register_only', 'register_with_refresh_token', 'account_paid', 'account_paid_with_refresh_token', 'link_only', 'register_pp_long_link', 'register_workspace_request', 'register_team_codex_token_sub2api'].includes(String(selectedPreset || ''))
    : Array.isArray(selectedStages) && selectedStages.includes('register')
  const showTinkMailFields = selectedMode === 'preset'
    ? selectedPreset === 'tinkmail_email_register'
    : Array.isArray(selectedStages) && selectedStages.includes('tinkmail_email_register')

  const loadConfigs = useCallback(async () => {
    setLoadingConfigs(true)
    try {
      const data = await apiFetch<{ id: number; name: string; stages: string[]; stop_after: string }[]>('/pipeline-configs')
      setSavedConfigs(data || [])
    } catch { /* ignore */ }
    finally { setLoadingConfigs(false) }
  }, [])

  useEffect(() => { loadConfigs() }, [loadConfigs])
  useEffect(() => {
    if (isWorkspaceRequestPreset) {
      form.setFieldsValue({ route: 'request', switch_after_join: false, refresh_before_request: false })
    }
  }, [form, isWorkspaceRequestPreset])

  const handleSave = async () => {
    const name = saveName.trim()
    if (!name) { message.warning('请输入配置名称'); return }
    const currentStages = form.getFieldValue('stages') as string[] || []
    if (!currentStages.length) { message.warning('请选择至少一个阶段'); return }
    try {
      await apiFetch('/pipeline-configs', { method: 'POST', body: JSON.stringify({ name, stages: currentStages, stop_after: form.getFieldValue('stop_after') || '' }) })
      message.success(`已保存 "${name}"`)
      setSaveName('')
      loadConfigs()
    } catch (err) { message.error(err instanceof Error ? err.message : '保存失败') }
  }

  const handleDelete = async (id: number, name: string) => {
    try {
      await apiFetch(`/pipeline-configs/${id}`, { method: 'DELETE' })
      message.success(`已删除 "${name}"`)
      loadConfigs()
    } catch (err) { message.error(err instanceof Error ? err.message : '删除失败') }
  }

  const applyConfig = (cfg: { stages: string[]; stop_after: string }) => {
    setMode('custom')
    form.setFieldsValue({ mode: 'custom', stages: cfg.stages, stop_after: cfg.stop_after || '' })
  }

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{ count: 1, mode: 'preset', preset: 'full_chain', stages: [], stop_after: '', source_type: 'auto', invite_count: 1, prefix_len: 20, dry_run: false, invite_count_per_inviter: 5, activate_after_invite: true, route: 'request', interval_ms: 1500, max_retries: 3, retry_backoff_ms: 5000, refresh_before_request: true, allow_partial: false, switch_after_join: true, upload_multiple: true, ttl: 7776000, token_name: 'codex', scope: 'chatgpt.workspace.feature.allow-codex-local-access.access', pp_country: 'US', pp_currency: 'USD', pp_max_retries: 3, pp_retry_backoff_ms: 5000, tinkmail_enabled: true, tinkmail_acquire_proxy: false }}
      autoComplete="off"
    >
      <Row gutter={16}>
        <Col span={8}>
          <Form.Item label="数量" name="count" rules={[{ required: true }]}>
            <InputNumber min={1} max={200} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col span={16}>
          <Form.Item label="创建模式" name="mode">
            <Radio.Group
              optionType="button" buttonStyle="solid"
              onChange={(e) => { setMode(e.target.value); form.setFieldsValue({ stages: [], stop_after: '' }) }}
            >
              <Radio.Button value="preset">预设链路</Radio.Button>
              <Radio.Button value="custom">自定义阶段</Radio.Button>
            </Radio.Group>
          </Form.Item>
        </Col>
      </Row>

      {selectedMode === 'preset' ? (
        <>
          <Form.Item label="预设链路" name="preset" rules={[{ required: true }]}>
            <Select options={PRESET_OPTIONS} />
          </Form.Item>
          <Form.Item label="运行到哪一步停止" name="stop_after">
            <Select options={STOP_OPTIONS} allowClear placeholder="跑完全部" />
          </Form.Item>
        </>
      ) : (
        <>
          {/* Saved configs selector + management */}
          <Form.Item label="已保存的配置">
            <Space style={{ width: '100%' }} direction="vertical" size={6}>
              <Select
                loading={loadingConfigs}
                placeholder={savedConfigs.length ? `选择配置 (${savedConfigs.length}个)` : '暂无保存的配置'}
                allowClear
                value={undefined}
                onChange={(val: number) => {
                  const cfg = savedConfigs.find((c) => c.id === val)
                  if (cfg) applyConfig(cfg)
                }}
                options={savedConfigs.map((c) => ({
                  value: c.id,
                  label: `${c.name} — ${c.stages.join(' → ')}${c.stop_after ? ` [停:${c.stop_after}]` : ''}`,
                }))}
              />
              {savedConfigs.length > 0 && (
                <Space wrap size={[4, 4]}>
                  {savedConfigs.map((c) => (
                    <Popconfirm key={c.id} title={`删除 "${c.name}"？`} onConfirm={() => handleDelete(c.id, c.name)}>
                      <Tag closable color="blue" onClose={(e) => { e.preventDefault() }}
                        style={{ cursor: 'pointer' }}
                        onClick={() => applyConfig(c)}
                      >
                        {c.name}
                      </Tag>
                    </Popconfirm>
                  ))}
                </Space>
              )}
            </Space>
          </Form.Item>

          <Form.Item
            label="选择阶段（按顺序执行）"
            name="stages"
            rules={[{ required: true, type: 'array', min: 1, message: '至少选择一个阶段' }]}
          >
            <Select mode="multiple" options={STAGE_OPTIONS} placeholder="拖拽排序或点击添加阶段..." style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item label="运行到哪一步停止（可选）" name="stop_after">
            <Select options={STOP_OPTIONS.filter((o) => o.value !== '')} allowClear placeholder="执行全部" />
          </Form.Item>

          <Form.Item label="保存为配置模板">
            <Space>
              <Input placeholder="输入配置名称..." value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                style={{ width: 200 }} onPressEnter={handleSave}
              />
              <Button icon={<PlusOutlined />} onClick={handleSave}>保存</Button>
            </Space>
          </Form.Item>
        </>
      )}

      {showTinkMailFields && (
        <>
          <ActionCard
            title="自动注册 TinkMail 邮箱"
            description="创建一个新的 @tinkmail.me 邮箱并自动写入邮箱池；本地名/密码留空会自动生成。"
          />
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="本地名 account（可选）" name="tinkmail_account">
                <Input placeholder="留空自动生成，例如 tmxxxx" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="TinkMail 密码（可选）" name="tinkmail_password">
                <Input.Password placeholder="留空自动生成" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="恢复邮箱 secure_email（可选）" name="tinkmail_secure_email">
            <Input placeholder="留空使用全局配置/自动值" />
          </Form.Item>
          <Form.Item label="代理 URL 覆盖（可选）" name="tinkmail_proxy_url">
            <Input placeholder="留空走 workpool.tinkmail_email_register.proxy_url / 默认代理" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="创建后放入可用邮箱池" name="tinkmail_enabled">
                <Radio.Group>
                  <Radio value={true}>是</Radio>
                  <Radio value={false}>否</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="无代理时从代理池领取" name="tinkmail_acquire_proxy">
                <Radio.Group>
                  <Radio value={false}>否</Radio>
                  <Radio value={true}>是</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>
        </>
      )}

      {showRegisterFields && (
        <>
          <ActionCard
            title="注册邮箱选择"
            description="留空则从邮箱池自动领取；填具体邮箱则按邮箱池记录使用对应收件方式：QQ=邮箱----mailapi_url，TinkMail=@tinkmail.me。"
          />
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="指定邮箱（可选）" name="email">
                <Input placeholder="例如 2213584103@qq.com 或 xxx@tinkmail.me；留空自动取邮箱池" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="注册密码覆盖（可选）" name="password">
                <Input.Password placeholder="留空则自动生成/使用邮箱策略" />
              </Form.Item>
            </Col>
          </Row>
        </>
      )}

      {showOAuthRtFields && (
        <>
          <ActionCard
            title="OAuth / RT 参数"
            description="已有账号补 RT/上传 sub：可填账号池 account_id；如果本地账号池没有该账号，也可以只填邮箱，后台会自动创建 OAuth 占位账号并用邮箱验证码登录。"
          />
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="已有账号 ID"
                name="account_id"
                dependencies={["email"]}
                rules={[{
                  validator: async () => {
                    if (selectedPreset !== 'refresh_token_only' && selectedPreset !== 'sso_rt_only') return
                    if (oauthAccountId || String(oauthEmail || '').trim()) return
                    throw new Error('已有账号补 RT 请填账号ID；如果账号池没有，就填邮箱')
                  },
                }]}
              >
                <InputNumber min={1} style={{ width: '100%' }} placeholder="账号池 account_id；没有账号时可留空改填邮箱" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="邮箱覆盖 / 无账号时 OAuth 邮箱"
                name="email"
                dependencies={["account_id"]}
                rules={[{
                  validator: async () => {
                    if (selectedPreset !== 'refresh_token_only') return
                    if (oauthAccountId || String(oauthEmail || '').trim()) return
                    throw new Error('账号池没有账号时，请在这里填邮箱')
                  },
                }]}
              >
                <Input placeholder="默认从账号池读取；账号池没有时填邮箱触发 OAuth 占位账号" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="已有 RT ID（可选；只同步已有 RT 时填）" name="refresh_token_id">
            <InputNumber min={1} style={{ width: '100%' }} placeholder="一般留空，让 openai_oauth 新取 RT" />
          </Form.Item>
          <Form.Item label="执行模式" name="dry_run">
            <Radio.Group>
              <Radio value={false}>实际获取 RT 并上传 sub</Radio>
              <Radio value={true}>Dry-run 只检查参数</Radio>
            </Radio.Group>
          </Form.Item>
        </>
      )}

      {showCodexTokenFields && (
        <>
          <ActionCard
            title="Team Codex 令牌上传 sub 参数"
            description="复用账号缓存 cookie，先切换到 Team Workspace，再在 /admin 创建 Codex 令牌；sub2api_sync 会上传这个 Codex 令牌而不是普通 AT。"
          />
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="已有账号 ID" name="account_id" rules={[{ required: selectedPreset === 'team_codex_token_sub2api', message: '已有账号流程必须填账号ID' }]}>
                <InputNumber min={1} style={{ width: '100%' }} placeholder="账号池 account_id" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="令牌名前缀" name="token_name">
                <Input placeholder="codex" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="TTL 秒" name="ttl">
                <InputNumber min={60} max={31536000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="Team Workspace ID 列表" name="workspace_ids" rules={[{ required: true, message: '请输入 Team Workspace ID' }]}>
            <Input.TextArea rows={4} placeholder={"一行一个或逗号分隔，例如:\n7dc92548-255c-4e45-a570-ef25d793ab23"} />
          </Form.Item>
          <Form.Item label="也可填 Team 母号账号 ID（可选）" name="workspace_account_ids">
            <Input placeholder="多个账号ID用逗号/换行分隔；系统从账号池 workspace_id 字段解析" />
          </Form.Item>
          <Form.Item label="Scope" name="scope">
            <Input placeholder="chatgpt.workspace.feature.allow-codex-local-access.access" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="Workspace 间隔毫秒" name="interval_ms">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="最大重试" name="max_retries">
                <InputNumber min={0} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="重试退避毫秒" name="retry_backoff_ms">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="多空间上传多条 sub" name="upload_multiple">
                <Radio.Group>
                  <Radio value={true}>是</Radio>
                  <Radio value={false}>否</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="执行模式" name="dry_run">
                <Radio.Group>
                  <Radio value={false}>实际创建并上传</Radio>
                  <Radio value={true}>Dry-run 只检查参数</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>
        </>
      )}


      {showWorkspaceJoinFields && (
        <>
          <ActionCard
            title={isWorkspaceRequestPreset ? 'Workspace Request 参数' : 'Workspace Join Request 参数'}
            description={isWorkspaceRequestPreset
              ? '等价于浏览器脚本的 Request：使用当前账号 AT 向母号 Workspace 发送加入申请，只走 /invites/request，Workspace ID 可配置。'
              : 'AT 会自动使用前置 sso_oauth / openai_oauth 的 access_token；只需要填写母号 Workspace ID。request = 子号主动申请加入；accept = 接受已有邀请。'}
          />
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="已有账号 ID（子号）" name="account_id">
                <InputNumber min={1} style={{ width: '100%' }} placeholder="账号池 account_id，例如 353" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="说明">
                <Text type="secondary">已有号加入空间时填；注册→加入链路可留空。</Text>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="母号 Workspace ID 列表" name="workspace_ids">
            <Input.TextArea rows={4} placeholder={"一行一个或逗号分隔，例如:\nacfb4e38-524c-4dc8-b4cf-fb3d0ce28b25"} />
          </Form.Item>
          <Form.Item label="也可填母号账号 ID（可选）" name="workspace_account_ids">
            <Input placeholder="多个账号ID用逗号/换行分隔；系统从账号池 workspace_id 字段解析" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="动作" name="route">
                <Select
                  disabled={isWorkspaceRequestPreset}
                  options={[
                    { value: 'request', label: 'request 主动申请' },
                    { value: 'accept', label: 'accept 接受邀请' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="间隔毫秒" name="interval_ms">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="最大重试" name="max_retries">
                <InputNumber min={0} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="重试退避毫秒" name="retry_backoff_ms">
                <InputNumber min={0} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="请求前刷新 AT" name="refresh_before_request">
                <Radio.Group>
                  <Radio value={true}>是</Radio>
                  <Radio value={false}>否</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="部分成功是否通过" name="allow_partial">
                <Radio.Group>
                  <Radio value={false}>否</Radio>
                  <Radio value={true}>是</Radio>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="加入后切换到该 Workspace" name="switch_after_join">
            <Radio.Group>
              <Radio value={true} disabled={isWorkspaceRequestPreset}>是，后续 chatgpt_session 直接取新空间 AT</Radio>
              <Radio value={false}>否，只加入不切换</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="access_token 覆盖（可选；仅 workspace_join_only 且没有前置 token 时填写）" name="access_token">
            <Input.Password placeholder="Bearer token 原文，不要带 Bearer 前缀" />
          </Form.Item>
          <Form.Item label="执行模式" name="dry_run">
            <Radio.Group>
              <Radio value={false}>实际发送</Radio>
              <Radio value={true}>Dry-run 只检查参数</Radio>
            </Radio.Group>
          </Form.Item>
        </>
      )}

      {showPPLongLinkFields && (
        <>
          <ActionCard
            title="PP 长链接参数"
            description="这里配置失败重试次数；不是生成多条。总尝试次数 = 1 + 最大失败重试。"
          />
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="国家" name="pp_country">
                <Input placeholder="US" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="币种" name="pp_currency">
                <Input placeholder="USD" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="金额覆盖（可选）" name="pp_target_amount">
                <Input placeholder="留空使用 app.py 默认" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="失败重试次数" name="pp_max_retries">
                <InputNumber min={0} max={20} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="重试退避毫秒" name="pp_retry_backoff_ms">
                <InputNumber min={0} max={300000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="access_token 覆盖（可选；已有号单独生成时可填）" name="pp_access_token">
            <Input.Password placeholder="Bearer token 原文，不要带 Bearer 前缀" />
          </Form.Item>
          <Form.Item label="统一代理 URL（可选）" name="pp_proxy_url">
            <Input placeholder="留空复用任务/账号代理" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="create 代理（可选）" name="pp_create_proxy_url">
                <Input placeholder="留空使用统一代理" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="followup 代理（可选）" name="pp_followup_proxy_url">
                <Input placeholder="留空使用 create 代理" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="approve 代理（可选）" name="pp_approve_proxy_url">
                <Input placeholder="留空使用 followup 代理" />
              </Form.Item>
            </Col>
          </Row>
        </>
      )}

      {showBatchCodexFields && (
        <>
          <ActionCard
            title="批量 Codex 邀请参数"
            description="一次输入多个邀请母号；每个母号最多邀请 5 个。所有母号邀请完成后，系统会自动为所有受邀邮箱创建 sso_oauth → active 子流程。"
          />
          <Form.Item label="邀请母号列表" name="inviter_list" rules={[{ required: true, message: '请输入至少一个邀请母号' }]}>
            <Input.TextArea rows={5} placeholder={"每行一个母号邮箱或账号ID，例如:\njr3q7pganb@aicoco.xyz\nother@aicoco.xyz\n12"} />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="母号来源" name="source_type">
                <Select
                  options={[
                    { value: 'auto', label: 'auto 自动识别' },
                    { value: 'chatgpt_account', label: '账号池 ChatGPTAccount ID' },
                    { value: 'access_token_account', label: 'Free AT Token ID' },
                    { value: 'email_account', label: '邮箱池 EmailAccount ID' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="每个母号邀请数量" name="invite_count_per_inviter">
                <InputNumber min={1} max={5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="随机前缀长度" name="prefix_len">
                <InputNumber min={3} max={64} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="后续动作" name="activate_after_invite">
            <Radio.Group>
              <Radio value={true}>全部邀请完成后自动激活</Radio>
              <Radio value={false}>只邀请，不创建激活子流程</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="执行模式" name="dry_run">
            <Radio.Group>
              <Radio value={false}>实际发送邀请</Radio>
              <Radio value={true}>Dry-run 只生成/预检</Radio>
            </Radio.Group>
          </Form.Item>
        </>
      )}

      {showCodexFields && (
        <>
          <ActionCard
            title="Codex 邀请参数"
            description="这里填写“邀请母号”：可以填母号邮箱，系统会从账号池/Free AT 池查 token；也可以填母号在账号池/AT池/邮箱池里的 ID。邀请成功后会把生成的受邀邮箱传给后面的 SSO OAuth 和 active。"
          />
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="邀请母号邮箱" name="inviter_email">
                <Input placeholder="mother@example.com" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="邀请母号账号/资源 ID" name="inviter_account_id">
                <InputNumber min={1} style={{ width: '100%' }} placeholder="例如账号池 Account #12" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="母号来源" name="source_type">
                <Select
                  options={[
                    { value: 'auto', label: 'auto 自动识别' },
                    { value: 'chatgpt_account', label: '账号池 ChatGPTAccount ID' },
                    { value: 'access_token_account', label: 'Free AT Token ID' },
                    { value: 'email_account', label: '邮箱池 EmailAccount ID' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item label="邀请数量" name="invite_count">
                <InputNumber min={1} max={200} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item label="前缀长度" name="prefix_len">
                <InputNumber min={3} max={64} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="覆盖域名（可选）" name="domain">
            <Input placeholder="留空则使用源邮箱 @ 后面的域名，例如 example.com" />
          </Form.Item>
          <Form.Item label="access_token 覆盖（邮箱池源必填；账号/Token 源可留空）" name="access_token">
            <Input.Password placeholder="Bearer token 原文，不要带 Bearer 前缀" />
          </Form.Item>
          <Form.Item label="chatgpt-account-id 覆盖（邮箱池源必填；账号/Token 源可留空）" name="chatgpt_account_id">
            <Input placeholder="acct_... / account id" />
          </Form.Item>
          <Form.Item label="执行模式" name="dry_run">
            <Radio.Group>
              <Radio value={false}>实际发送邀请</Radio>
              <Radio value={true}>Dry-run 只生成/预检</Radio>
            </Radio.Group>
          </Form.Item>
        </>
      )}
    </Form>
  )
}
