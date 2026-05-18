import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Drawer,
  Form,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
  Input,
} from 'antd'
import { PlusOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { apiFetch, formatDateTime, formatDuration } from '@/lib/api'
import { stageLabel } from '@/lib/contracts'

const { Text } = Typography

interface Pipeline {
  id: number
  preset: string
  stages: string[]
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

  const openCreate = useCallback(async () => {
    // Always wipe leftover values from previous opens.
    form.resetFields()
    setCreateOpen(true)
    // Prefill the concurrency field with the current worker_concurrency.
    try {
      const settings = await apiFetch<Record<string, string>>('/settings')
      const current = Number(settings.worker_concurrency || 3)
      if (Number.isFinite(current) && current >= 1) {
        form.setFieldsValue({ concurrency: current })
      }
    } catch {
      // ignore
    }
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

  const submitCreate = async () => {
    const values = await form.validateFields()
    const mode = values.mode || 'free'
    const registrationMode = values.registration_mode || 'access_token_only'
    const concurrency = values.concurrency ? Number(values.concurrency) : undefined
    const specifiedEmail = values.use_specified_email ? String(values.email || '').trim() : ''
    const phoneExtraConfig = values.phone_verification_enabled
      ? {
          chatgpt_registration_mode: registrationMode,
          phone_verification_enabled: '1',
          phone_verification_provider: values.phone_verification_provider || 'smsbower',
        }
      : { chatgpt_registration_mode: registrationMode, phone_verification_enabled: '0' }
    const plan = values.plan || 'plus'
    const paymentProxyRegion = String(values.payment_proxy_region || '').trim()
    const registerInput = {
      email: specifiedEmail || undefined,
      fixed_email: specifiedEmail || undefined,
      password: values.password || undefined,
      registration_mode: registrationMode,
      also_record_to_at_pool: mode === 'free',
      extra_config: phoneExtraConfig,
    }
    const body = {
      count: Number(values.count || 1),
      preset: mode === 'free'
        ? (registrationMode === 'refresh_token' ? 'register_with_codex_rt' : 'register_only')
        : (registrationMode === 'refresh_token' ? 'account_paid_with_codex_rt' : 'link_only'),
      proxy_url: values.proxy_url || undefined,
      concurrency: concurrency ? { register: concurrency } : undefined,
      stage_inputs: {
        register: registerInput,
        payment_link: {
          plan,
          workspace_name: values.workspace_name || 'MyWorkspace',
          price_interval: values.price_interval || 'month',
          seat_quantity: Number(values.seat_quantity || 2),
          country: values.country || (plan === 'plus' ? 'ID' : 'US'),
          currency: values.currency || undefined,
        },
        payment: paymentProxyRegion ? { payment_proxy_region: paymentProxyRegion } : undefined,
      },
    }
    try {
      console.info('create pipeline payload', { mode, registration_mode: registrationMode, body })
      const resp = await apiFetch<{ pipeline_ids: number[]; concurrency?: Record<string, number> }>(
        '/pipelines',
        { method: 'POST', body: JSON.stringify(body) },
      )
      message.success(
        `已创建 ${resp.pipeline_ids.length} 条 pipeline${resp.concurrency?.register ? `，register 并发=${resp.concurrency.register}` : ''}`,
      )
      setCreateOpen(false)
      form.resetFields()
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (value: string) => <StatusTag status={value} />,
    },
    {
      title: '当前 Stage',
      dataIndex: 'current_stage',
      width: 180,
      render: (value: string, row: Pipeline) => (
        <Space direction="vertical" size={2}>
          <Text>{stageLabel(value)}</Text>
          <Progress
            percent={Math.round((row.completed_steps / Math.max(row.total_steps, 1)) * 100)}
            size="small"
            format={() => `${row.completed_steps}/${row.total_steps}`}
            status={row.status === 'failed' ? 'exception' : row.status === 'succeeded' ? 'success' : 'active'}
          />
        </Space>
      ),
    },
    {
      title: '账号',
      dataIndex: 'account_id',
      width: 80,
      render: (value: number | null) =>
        value ? <Tag color="cyan">#{value}</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: '长链',
      dataIndex: 'payment_link_id',
      width: 80,
      render: (value: number | null) =>
        value ? <Tag color="purple">#{value}</Tag> : <Text type="secondary">-</Text>,
    },
    { title: '代理', dataIndex: 'proxy_url', ellipsis: true },
    {
      title: '耗时',
      width: 100,
      render: (_v: unknown, row: Pipeline) =>
        formatDuration(row.created_at, row.finished_at),
    },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (value: string) => (value ? <Text type="danger">{value}</Text> : <Text type="secondary">-</Text>),
    },
    {
      title: '操作',
      width: 260,
      render: (_v: unknown, row: Pipeline) => (
        <Space size={4}>
          <Button size="small" onClick={() => openDetail(row.id)}>详情</Button>
          {(row.status === 'queued' || row.status === 'running') && (
            <Popconfirm title="取消该 pipeline?" onConfirm={() => cancelPipeline(row.id)}>
              <Button size="small" danger>取消</Button>
            </Popconfirm>
          )}
          {row.status !== 'queued' && row.status !== 'running' && (
            <Popconfirm title="删除该 pipeline?" onConfirm={() => deletePipeline(row.id)}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ] as const

  return (
    <>
      <Card>
        <Row justify="space-between" style={{ marginBottom: 12 }}>
          <Col>
            <Space wrap>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新建账号 pipeline
              </Button>
              <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
              <Popconfirm
                title={`确认删除选中的 ${selected.length} 条?（运行/排队中将跳过）`}
                onConfirm={batchDelete}
                disabled={!selected.length}
              >
                <Button icon={<DeleteOutlined />} danger disabled={!selected.length}>
                  批量删除（{selected.length}）
                </Button>
              </Popconfirm>
            </Space>
          </Col>
        </Row>
        <Table
          rowKey="id"
          dataSource={pipelines}
          columns={columns as never}
          loading={loading}
          pagination={{ pageSize: 20 }}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: setSelected,
          }}
        />
      </Card>

      <Modal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        title="创建 ChatGPT 账号 pipeline"
        onOk={submitCreate}
        okText="创建"
      >
        <CreateForm form={form} />
      </Modal>

      <Drawer
        open={!!detail}
        onClose={() => setDetail(null)}
        width={760}
        title={detail ? `Pipeline #${detail.pipeline.id}` : ''}
      >
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size={16}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="状态"><StatusTag status={detail.pipeline.status} /></Descriptions.Item>
              <Descriptions.Item label="当前 Stage">{stageLabel(detail.pipeline.current_stage)}</Descriptions.Item>
              <Descriptions.Item label="Stage Chain" span={2}>
                <Space size={4} wrap>
                  {detail.pipeline.stages.map((stage) => (
                    <Tag key={stage} color={stage === detail.pipeline.current_stage ? 'processing' : 'default'}>{stageLabel(stage)} <Text type="secondary">{stage}</Text></Tag>
                  ))}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="账号 ID">{detail.pipeline.account_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="长链 ID">{detail.pipeline.payment_link_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatDateTime(detail.pipeline.created_at)}</Descriptions.Item>
              <Descriptions.Item label="完成时间">{formatDateTime(detail.pipeline.finished_at)}</Descriptions.Item>
              <Descriptions.Item label="代理" span={2}>{detail.pipeline.proxy_url || '-'}</Descriptions.Item>
              {detail.pipeline.error && (
                <Descriptions.Item label="错误" span={2}>
                  <Text type="danger">{detail.pipeline.error}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>
            <Card size="small" title="子任务">
              <Table
                size="small"
                rowKey="id"
                dataSource={detail.jobs}
                pagination={false}
                columns={[
                  { title: 'ID', dataIndex: 'id', width: 60 },
                  { title: 'Stage', dataIndex: 'type', render: (value: string) => <Space direction="vertical" size={0}><Text>{stageLabel(value)}</Text><Text type="secondary" code>{value}</Text></Space> },
                  { title: '状态', dataIndex: 'status', render: (s: string) => <StatusTag status={s} /> },
                  { title: '尝试', render: (_v: unknown, row: Job) => `${row.attempt}/${row.max_attempts}` },
                  { title: '耗时', render: (_v: unknown, row: Job) => formatDuration(row.started_at, row.finished_at) },
                  {
                    title: '操作',
                    width: 100,
                    render: (_v: unknown, row: Job) => (
                      <Button size="small" onClick={() => setLogJobId(row.id)}>原始日志</Button>
                    ),
                  },
                ] as never}
              />
            </Card>
          </Space>
        )}
      </Drawer>

      <Drawer
        open={logJobId !== null}
        onClose={() => setLogJobId(null)}
        width={760}
        title={logJobId ? `Job #${logJobId} 原始日志` : ''}
      >
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </Drawer>
    </>
  )
}


import type { FormInstance } from 'antd'

function CreateForm({ form }: { form: FormInstance }) {
  const mode: string = (Form.useWatch('mode', form) as string) || 'free'
  const plan: string = (Form.useWatch('plan', form) as string) || 'plus'
  const useSpecifiedEmail = !!Form.useWatch('use_specified_email', form)
  const phoneVerificationEnabled = !!Form.useWatch('phone_verification_enabled', form)
  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        count: 1,
        concurrency: 3,
        mode: 'free',
        registration_mode: 'access_token_only',
        phone_verification_enabled: false,
        phone_verification_provider: 'smsbower',
        plan: 'plus',
        country: 'ID',
        seat_quantity: 2,
        price_interval: 'month',
        workspace_name: 'MyWorkspace',
        use_specified_email: false,
        payment_proxy_region: 'US',
      }}
      autoComplete="off"
    >
      <Row gutter={12}>
        <Col span={12}>
          <Form.Item label="数量" name="count" rules={[{ required: true }]}>
            <InputNumber min={1} max={200} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            label="注册 WorkPool 并发"
            name="concurrency"
            tooltip="只调整 register stage 的并发；其他 WorkPool 状态在池子页面查看"
          >
            <InputNumber min={1} max={64} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>
      <Form.Item label="模式" name="mode">
        <Select
          options={[
            { value: 'free', label: 'Free（单注册，落入 AT 号池）' },
            { value: 'subscription', label: '订阅（注册 + 获取支付长链）' },
          ]}
        />
      </Form.Item>

      <Form.Item label="是否获取 RT" name="registration_mode">
        <Select
          options={[
            { value: 'access_token_only', label: '不获取 RT（只拿 access_token / session_token）' },
            { value: 'refresh_token', label: '获取 RT（注册后追加 OAuth refresh_token）' },
          ]}
        />
      </Form.Item>

      <Form.Item name="phone_verification_enabled" valuePropName="checked">
        <Checkbox>开启 add-phone 手机接码（默认关闭，仅遇到手机验证时使用）</Checkbox>
      </Form.Item>
      {phoneVerificationEnabled && (
        <Form.Item label="接码平台" name="phone_verification_provider">
          <Select
            options={[
              { value: 'smsbower', label: 'SmsBower' },
              { value: 'fivesim', label: '5SIM' },
              { value: 'smsgiare', label: 'SmsGiaRe' },
            ]}
          />
        </Form.Item>
      )}

      {mode === 'subscription' && (
        <>
          <Form.Item label="支付套餐" name="plan">
            <Select
              options={[
                { value: 'plus', label: 'Plus Hosted（IDR / GoPay 通道）— 默认' },
                { value: 'team', label: 'Team Hosted（promo: STRIPEATLASGPT4BIZ050126）' },
              ]}
              onChange={(next) => {
                if (next === 'plus') form.setFieldsValue({ country: 'ID' })
                else form.setFieldsValue({ country: 'US' })
              }}
            />
          </Form.Item>
          <Form.Item
            label="支付代理 Region"
            name="payment_proxy_region"
            rules={[{ required: true, message: '请输入支付代理 region' }]}
            tooltip="payment stage 会按该 region 从 proxy_pool 选择不同于账号注册代理的 proxy"
          >
            <Input placeholder="例如 US / ID" />
          </Form.Item>
        </>
      )}

      <Form.Item name="use_specified_email" valuePropName="checked">
        <Checkbox>指定邮箱（不勾选则从微软池取）</Checkbox>
      </Form.Item>
      {useSpecifiedEmail && (
        <>
          <Form.Item label="指定邮箱" name="email">
            <Input placeholder="example@outlook.com" autoComplete="new-email" />
          </Form.Item>
          <Form.Item label="指定密码" name="password">
            <Input placeholder="可留空" autoComplete="new-password" />
          </Form.Item>
        </>
      )}
      <Form.Item label="代理 URL" name="proxy_url">
        <Input placeholder="例如 http://user:pass@host:port；留空走代理池" />
      </Form.Item>

      {mode === 'subscription' && plan === 'team' && (
        <>
          <Form.Item label="Workspace 名称" name="workspace_name">
            <Input />
          </Form.Item>
          <Form.Item label="付款周期" name="price_interval">
            <Select
              options={[
                { value: 'month', label: '按月' },
                { value: 'year', label: '按年' },
              ]}
            />
          </Form.Item>
          <Form.Item label="座位数" name="seat_quantity">
            <InputNumber min={1} max={99} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="国家" name="country">
            <Input placeholder="例如 US" />
          </Form.Item>
          <Form.Item label="货币（留空按国家自动）" name="currency">
            <Input placeholder="USD / SGD / HKD ..." />
          </Form.Item>
        </>
      )}

      {mode === 'subscription' && plan === 'plus' && (
        <>
          <Form.Item label="国家（默认 ID，对应 IDR 套餐）" name="country">
            <Input placeholder="ID" />
          </Form.Item>
          <Form.Item label="货币（留空按国家自动，ID → IDR）" name="currency">
            <Input placeholder="IDR" />
          </Form.Item>
        </>
      )}
    </Form>
  )
}
