import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormInstance } from 'antd'
import { Button, Col, Form, InputNumber, Popconfirm, Progress, Row, Select, Space, Tag, Typography, message } from 'antd'
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
  { value: 'oauth_codex', label: 'OAuth Codex 后停止' },
  { value: 'rt_keepalive', label: 'RT 保活后停止' },
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

export default function Pipelines() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()
  const [detail, setDetail] = useState<PipelineDetail | null>(null)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [page, setPage] = useState(1)

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
    const stopAfter = String(values.stop_after || '')
    const body = {
      count: Number(values.count || 1),
      preset: FULL_CHAIN_PRESET,
      stop_after: stopAfter || undefined,
    }

    try {
      const resp = await apiFetch<{ pipeline_ids: number[] }>(
        '/pipelines',
        { method: 'POST', body: JSON.stringify(body) },
      )
      message.success(`已创建 ${resp.pipeline_ids.length} 条完整链路 pipeline`)
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
      description="默认创建完整链路 register → payment_link → payment → oauth_codex → rt_keepalive，也可以在任一模块边界停止。"
      actions={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建完整链路</Button>}
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
        description="任务创建只表达数量、完整链路和停止点；代理、接码、支付等参数在各 WorkPool / ResourcePool 卡片配置。"
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
        pageSize={18}
        onPageChange={setPage}
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

      <PopupCard open={createOpen} onCancel={() => setCreateOpen(false)} title="创建完整链路 pipeline" onOk={submitCreate} okText="创建" width={560}>
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
  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        count: 1,
        stop_after: '',
      }}
      autoComplete="off"
    >
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="数量" name="count" rules={[{ required: true }]}>
            <InputNumber min={1} max={200} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="链路">
            <Tag color="blue">完整链路</Tag>
          </Form.Item>
        </Col>
      </Row>
      <Form.Item label="运行到哪一步停止" name="stop_after" tooltip="模块参数在各 WorkPool / ResourcePool 配置里维护">
        <Select options={STOP_OPTIONS} />
      </Form.Item>
    </Form>
  )
}
