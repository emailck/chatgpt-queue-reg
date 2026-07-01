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
  { value: 'payment', label: '付款模块后停止' },
  { value: 'chatgpt_session', label: 'Session 标准化后停止' },
  { value: 'sub2api_sync', label: 'sub2api 同步后停止' },
  { value: 'openai_oauth', label: 'OpenAI OAuth RT 后停止' },
  { value: 'sso_oauth', label: 'SSO OAuth RT 后停止' },
  { value: 'codex_invitation', label: 'Codex 邀请后停止' },
  { value: 'active', label: 'Codex 激活后停止' },
]

const STAGE_OPTIONS: { value: string; label: string }[] = [
  { value: 'register', label: '注册' },
  { value: 'payment_link', label: '生成长链' },
  { value: 'payment', label: '付款' },
  { value: 'chatgpt_session', label: 'ChatGPT Session' },
  { value: 'openai_oauth', label: 'OpenAI OAuth RT' },
  { value: 'sso_oauth', label: 'SSO OAuth RT' },
  { value: 'codex_invitation', label: 'Codex 邀请' },
  { value: 'codex_batch_invite', label: '批量 Codex 邀请' },
  { value: 'active', label: 'Codex 激活' },
  { value: 'sub2api_sync', label: 'sub2api 同步' },
]

const PRESET_OPTIONS: { value: string; label: string }[] = [
  { value: 'full_chain', label: '完整链路 register→payment_link→payment→chatgpt_session→sub2api_sync' },
  { value: 'register_only', label: '仅注册 register' },
  { value: 'register_with_refresh_token', label: '注册+RT register→chatgpt_session→openai_oauth→sub2api_sync' },
  { value: 'account_paid', label: '全自动付费 register→payment_link→payment' },
  { value: 'account_paid_with_refresh_token', label: '付费+RT 全部6步' },
  { value: 'link_only', label: '只到长链 register→payment_link' },
  { value: 'refresh_token_only', label: '已有号补RT chatgpt_session→openai_oauth→sub2api_sync' },
  { value: 'codex_invitation_only', label: 'Codex 邀请 codex_invitation' },
  { value: 'codex_invite_sso_active', label: 'Codex 邀请+SSO+激活 codex_invitation→sso_oauth→active' },
  { value: 'codex_batch_invite_active', label: '批量 Codex 邀请→统一激活 codex_batch_invite' },
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
      setDetail(data)
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

    const includesCodex = mode === 'custom'
      ? Array.isArray(body.stages) && (body.stages.includes('codex_invitation') || body.stages.includes('codex_batch_invite'))
      : body.preset === 'codex_invitation_only' || body.preset === 'codex_invite_sso_active' || body.preset === 'codex_batch_invite_active'
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
                  subtitle={<Text code>{job.type}</Text>}
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
  const showCodexFields = selectedMode === 'preset'
    ? selectedPreset === 'codex_invitation_only' || selectedPreset === 'codex_invite_sso_active'
    : Array.isArray(selectedStages) && selectedStages.includes('codex_invitation')
  const showBatchCodexFields = selectedMode === 'preset'
    ? selectedPreset === 'codex_batch_invite_active'
    : Array.isArray(selectedStages) && selectedStages.includes('codex_batch_invite')

  const loadConfigs = useCallback(async () => {
    setLoadingConfigs(true)
    try {
      const data = await apiFetch<{ id: number; name: string; stages: string[]; stop_after: string }[]>('/pipeline-configs')
      setSavedConfigs(data || [])
    } catch { /* ignore */ }
    finally { setLoadingConfigs(false) }
  }, [])

  useEffect(() => { loadConfigs() }, [loadConfigs])

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
      initialValues={{ count: 1, mode: 'preset', preset: 'full_chain', stages: [], stop_after: '', source_type: 'auto', invite_count: 1, prefix_len: 20, dry_run: false, invite_count_per_inviter: 5, activate_after_invite: true }}
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
