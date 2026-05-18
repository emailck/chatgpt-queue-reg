import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Descriptions, Drawer, Progress, Row, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { apiFetch, formatDateTime, formatDuration } from '@/lib/api'
import type { CardPoolStats, EmailPoolStats, Job, PoolsResponse, ProxyPoolStats, QueueStats, SmsPoolStats, StageMap, StageMeta } from '@/lib/contracts'
import { stageLabel } from '@/lib/contracts'

const { Text } = Typography

const EMPTY_QUEUE: QueueStats = { concurrency: {}, inflight: {}, counts: {} }
const JOB_STATUS_KEYS = ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']

function num(value: unknown): number {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n : 0
}

function poolValue(pool: Record<string, unknown> | undefined, key: string): number {
  return num(pool?.[key])
}

function resourceTags(items: string[]) {
  if (!items.length) return <Text type="secondary">-</Text>
  return (
    <Space size={4} wrap>
      {items.map((item) => <Tag key={item}>{item}</Tag>)}
    </Space>
  )
}

function stageView(stage: string) {
  return (
    <Space direction="vertical" size={0}>
      <Text strong>{stageLabel(stage)}</Text>
      <Text type="secondary" code>{stage}</Text>
    </Space>
  )
}

export default function Pools() {
  const [stages, setStages] = useState<StageMap>({})
  const [pools, setPools] = useState<PoolsResponse>({})
  const [queue, setQueue] = useState<QueueStats>(EMPTY_QUEUE)
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(false)
  const [logJobId, setLogJobId] = useState<number | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const [stageData, poolData, queueData, jobData] = await Promise.all([
        apiFetch<StageMap>('/stages'),
        apiFetch<PoolsResponse>('/pools'),
        apiFetch<QueueStats>('/queue/stats'),
        apiFetch<Job[]>('/jobs?limit=300'),
      ])
      setStages(stageData)
      setPools(poolData)
      setQueue(queueData)
      setJobs(jobData)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载池子失败')
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

  const totals = useMemo(() => {
    const totalConcurrency = Object.values(queue.concurrency || {}).reduce((sum, value) => sum + num(value), 0)
    const totalInflight = Object.values(queue.inflight || {}).reduce((sum, value) => sum + num(value), 0)
    return { totalConcurrency, totalInflight }
  }, [queue])

  const jobCountsByStage = useMemo(() => {
    const out: Record<string, Record<string, number>> = {}
    for (const job of jobs) {
      const stage = job.type || 'unknown'
      out[stage] = out[stage] || {}
      out[stage][job.status] = (out[stage][job.status] || 0) + 1
    }
    return out
  }, [jobs])

  const stageRows = useMemo(() => Object.values(stages), [stages])
  const emailPool = pools.email_pool as EmailPoolStats | undefined
  const cardPool = pools.card_pool as CardPoolStats | undefined
  const proxyPool = pools.proxy_pool as ProxyPoolStats | undefined
  const smsPool = pools.sms_pool as SmsPoolStats | undefined
  const knownPools = new Set(['email_pool', 'card_pool', 'proxy_pool', 'sms_pool'])
  const extraPools = Object.entries(pools).filter(([name]) => !knownPools.has(name))

  const stageColumns = [
    {
      title: 'WorkPool',
      dataIndex: 'name',
      width: 190,
      render: (value: string) => stageView(value),
    },
    {
      title: '运行状态',
      width: 110,
      render: (_v: unknown, row: StageMeta) => (
        <Tag color={row.implemented ? 'green' : 'default'}>{row.implemented ? 'implemented' : 'stub'}</Tag>
      ),
    },
    {
      title: '容量',
      width: 180,
      render: (_v: unknown, row: StageMeta) => {
        const concurrency = num(queue.concurrency?.[row.name] ?? row.default_concurrency)
        const inflight = num(queue.inflight?.[row.name])
        const percent = concurrency ? Math.min(100, Math.round((inflight / concurrency) * 100)) : 0
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Text>{inflight}/{concurrency}</Text>
            <Progress percent={percent} size="small" showInfo={false} status={inflight >= concurrency && concurrency ? 'exception' : 'active'} />
          </Space>
        )
      },
    },
    {
      title: '最近队列',
      width: 180,
      render: (_v: unknown, row: StageMeta) => {
        const counts = jobCountsByStage[row.name] || {}
        return (
          <Space size={4} wrap>
            <Tag>queued {counts.queued || 0}</Tag>
            <Tag color="processing">running {counts.running || 0}</Tag>
            <Tag color="red">failed {counts.failed || 0}</Tag>
          </Space>
        )
      },
    },
    { title: '必需资源', width: 180, render: (_v: unknown, row: StageMeta) => resourceTags(row.requires_resources || []) },
    { title: '可选资源', width: 180, render: (_v: unknown, row: StageMeta) => resourceTags(row.optional_resources || []) },
    {
      title: 'Schema',
      width: 180,
      render: (_v: unknown, row: StageMeta) => (
        <Space direction="vertical" size={0}>
          <Text code>{row.input_schema || '-'}</Text>
          <Text code>{row.output_schema || '-'}</Text>
        </Space>
      ),
    },
    { title: '说明', dataIndex: 'description', ellipsis: true },
  ]

  const jobColumns = [
    { title: 'Job', dataIndex: 'id', width: 70, render: (value: number) => <Text code>#{value}</Text> },
    { title: 'Stage', dataIndex: 'type', width: 170, render: (value: string) => stageView(value) },
    { title: '状态', dataIndex: 'status', width: 120, render: (value: string) => <StatusTag status={value} /> },
    { title: 'Pipeline', dataIndex: 'pipeline_id', width: 90, render: (value: number | null) => value ? <Tag>#{value}</Tag> : '-' },
    { title: 'Account', dataIndex: 'account_id', width: 90, render: (value: number | null) => value ? <Tag color="cyan">#{value}</Tag> : '-' },
    { title: 'PaymentLink', dataIndex: 'payment_link_id', width: 110, render: (value: number | null) => value ? <Tag color="purple">#{value}</Tag> : '-' },
    { title: '邮箱', dataIndex: 'email_address', ellipsis: true, render: (value: string) => value || '-' },
    { title: '代理', dataIndex: 'proxy_url', ellipsis: true, render: (value: string) => value || '-' },
    { title: '创建', width: 170, render: (_v: unknown, row: Job) => formatDateTime(row.created_at) },
    { title: '耗时', width: 90, render: (_v: unknown, row: Job) => formatDuration(row.started_at, row.finished_at) },
    {
      title: '操作',
      width: 110,
      render: (_v: unknown, row: Job) => <Button size="small" onClick={() => setLogJobId(row.id)}>原始日志</Button>,
    },
  ]

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="总并发">{totals.totalConcurrency}</Descriptions.Item>
              <Descriptions.Item label="运行中">{totals.totalInflight}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        {JOB_STATUS_KEYS.map((key) => (
          <Col xs={12} sm={8} lg={3} key={key}>
            <Card size="small">
              <Space direction="vertical" size={0}>
                <Text type="secondary">{key}</Text>
                <Text strong style={{ fontSize: 22 }}>{num(queue.counts?.[key])}</Text>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Card
        title="Stage WorkPools"
        extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
      >
        <Table rowKey="name" dataSource={stageRows} columns={stageColumns as never} loading={loading} pagination={false} />
      </Card>

      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12} xl={6}>
          <ResourceCard
            title="email_pool"
            values={{
              total: poolValue(emailPool, 'total'),
              available: poolValue(emailPool, 'available'),
              claimed: poolValue(emailPool, 'claimed'),
              consumed: poolValue(emailPool, 'consumed'),
              blacklist: poolValue(emailPool, 'blacklist'),
            }}
          />
        </Col>
        <Col xs={24} lg={12} xl={6}>
          <ResourceCard
            title="card_pool"
            values={{
              total: poolValue(cardPool, 'total'),
              available: poolValue(cardPool, 'available'),
              in_use: poolValue(cardPool, 'in_use'),
              used: poolValue(cardPool, 'used'),
              failed: poolValue(cardPool, 'failed'),
              banned: poolValue(cardPool, 'banned'),
            }}
          />
        </Col>
        <Col xs={24} lg={12} xl={6}>
          <ResourceCard
            title="proxy_pool"
            values={{
              total: poolValue(proxyPool, 'total'),
              enabled: poolValue(proxyPool, 'enabled'),
              disabled: poolValue(proxyPool, 'disabled'),
            }}
          />
        </Col>
        <Col xs={24} lg={12} xl={6}>
          <Card size="small" title="sms_pool">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="total">{poolValue(smsPool, 'total')}</Descriptions.Item>
              <Descriptions.Item label="enabled">{poolValue(smsPool, 'enabled')}</Descriptions.Item>
            </Descriptions>
            <Table
              size="small"
              rowKey="id"
              dataSource={smsPool?.projects || []}
              pagination={false}
              columns={[
                { title: '项目', dataIndex: 'name' },
                { title: 'provider', dataIndex: 'provider', width: 100 },
                { title: '状态', dataIndex: 'enabled', width: 80, render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? 'on' : 'off'}</Tag> },
              ] as never}
            />
          </Card>
        </Col>
        {extraPools.map(([name, value]) => (
          <Col xs={24} lg={12} key={name}>
            <Card size="small" title={name}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify(value, null, 2)}</pre>
            </Card>
          </Col>
        ))}
      </Row>

      <Card title="Recent Jobs">
        <Table rowKey="id" dataSource={jobs} columns={jobColumns as never} loading={loading} pagination={{ pageSize: 20 }} />
      </Card>

      <Drawer
        open={logJobId !== null}
        onClose={() => setLogJobId(null)}
        width={760}
        title={logJobId ? `Job #${logJobId} 原始日志` : ''}
      >
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </Drawer>
    </Space>
  )
}

function ResourceCard({ title, values }: { title: string; values: Record<string, number> }) {
  return (
    <Card size="small" title={title}>
      <Descriptions column={1} size="small">
        {Object.entries(values).map(([key, value]) => (
          <Descriptions.Item label={key} key={key}>{value}</Descriptions.Item>
        ))}
      </Descriptions>
    </Card>
  )
}
