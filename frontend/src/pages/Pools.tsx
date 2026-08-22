import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Col, Divider, Empty, Form, Input, InputNumber, Progress, Row, Select, Space, Switch, Tag, Typography, message } from 'antd'
import { ReloadOutlined, SaveOutlined, SettingOutlined } from '@ant-design/icons'

import { ActionCard, CardToolbar, CodeSurface, EntityCard, KeyValue, KeyValueGrid, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { apiFetch } from '@/lib/api'
import type { CardPoolStats, EmailPoolStats, Job, PayPalNumberPoolStats, PoolsResponse, ProxyPoolStats, QueueStats, SmsPoolStats, StageMap, StageMeta } from '@/lib/contracts'
import { stageLabel } from '@/lib/contracts'
import type { PoolSettingGroup, SettingField } from '@/lib/poolSettings'
import { RESOURCEPOOL_SETTING_GROUPS, WORKPOOL_SETTING_GROUPS, toFormValues, toSettingsValues } from '@/lib/poolSettings'

const { Text } = Typography

const EMPTY_QUEUE: QueueStats = { concurrency: {}, inflight: {}, counts: {} }
const JOB_STATUS_KEYS = ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']

function renderSettingControl(field: SettingField) {
  if (field.type === 'switch') return <Switch checkedChildren="开" unCheckedChildren="关" />
  if (field.type === 'number') return <InputNumber style={{ width: '100%' }} placeholder={field.placeholder || ''} />
  if (field.type === 'select') return <Select options={field.options || []} placeholder={field.placeholder || ''} allowClear />
  if (field.type === 'password') return <Input.Password placeholder={field.placeholder || ''} autoComplete="new-password" />
  return <Input placeholder={field.placeholder || ''} />
}

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

function stageTone(stage: StageMeta): 'success' | 'warning' {
  return stage.implemented ? 'success' : 'warning'
}

export default function Pools() {
  const [stages, setStages] = useState<StageMap>({})
  const [pools, setPools] = useState<PoolsResponse>({})
  const [queue, setQueue] = useState<QueueStats>(EMPTY_QUEUE)
  const [jobs, setJobs] = useState<Job[]>([])
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [proxyRows, setProxyRows] = useState<{ id: number; url: string; label: string; region: string; provider?: string; duration?: string; enabled: boolean }[]>([])
  const [loading, setLoading] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [configGroup, setConfigGroup] = useState<PoolSettingGroup | null>(null)
  const [configForm] = Form.useForm()
  const [templateGroupOpen, setTemplateGroupOpen] = useState(false)
  const [templateSaving, setTemplateSaving] = useState(false)
  const [templateForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const [stageData, poolData, queueData, jobData, settingsData, proxyData] = await Promise.all([
        apiFetch<StageMap>('/stages'),
        apiFetch<PoolsResponse>('/pools'),
        apiFetch<QueueStats>('/queue/stats'),
        apiFetch<Job[]>('/jobs?limit=300'),
        apiFetch<Record<string, string>>('/settings'),
        apiFetch<{ id: number; url: string; label: string; region: string; provider?: string; duration?: string; enabled: boolean }[]>('/proxies?limit=2000').catch(() => []),
      ])
      setStages(stageData)
      setPools(poolData)
      setQueue(queueData)
      setJobs(jobData)
      setSettings(settingsData)
      setProxyRows(proxyData)
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

  const openConfig = useCallback((group: PoolSettingGroup | undefined) => {
    if (!group) {
      message.info('该池子暂无配置项')
      return
    }
    setConfigGroup(group)
    configForm.setFieldsValue(toFormValues(group.fields, settings))
  }, [configForm, settings])

  const saveConfig = useCallback(async () => {
    if (!configGroup) return
    setSavingSettings(true)
    try {
      const values = configForm.getFieldsValue()
      const data = toSettingsValues(configGroup.fields, values)
      await apiFetch('/settings', { method: 'PUT', body: JSON.stringify({ data }) })
      setSettings((prev) => ({ ...prev, ...data }))
      message.success('已保存')
      setConfigGroup(null)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSavingSettings(false)
    }
  }, [configForm, configGroup])

  const stageRows = useMemo(() => Object.values(stages), [stages])
  const emailPool = pools.email_pool as EmailPoolStats | undefined
  const cardPool = pools.card_pool as CardPoolStats | undefined
  const paypalNumberPool = pools.paypal_number_pool as PayPalNumberPoolStats | undefined
  const proxyPool = pools.proxy_pool as ProxyPoolStats | undefined
  const smsPool = pools.sms_pool as SmsPoolStats | undefined
  const knownPools = new Set(['email_pool', 'card_pool', 'paypal_number_pool', 'proxy_pool', 'sms_pool'])
  const extraPools = Object.entries(pools).filter(([name]) => !knownPools.has(name))

  const proxyOptions = useMemo(() => proxyRows.map((row) => ({
    value: row.url,
    label: `${row.label || `Proxy #${row.id}`} · ${row.region || 'no-region'}${row.provider ? ` · ${row.provider}` : ''}${row.duration ? ` · t=${row.duration}` : ''}`,
  })), [proxyRows])

  const providerNames = useMemo(() => {
    const names = new Set<string>(['arxlabs', '1024proxy'])
    for (const key of Object.keys(settings)) {
      const match = key.match(/^proxy_provider\.([^.]+)\./)
      if (match?.[1]) names.add(match[1])
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b))
  }, [settings])

  const providerOptions = useMemo(() => [
    ...providerNames.map((name) => ({ value: name, label: name })),
    { value: 'custom', label: 'custom / 其他' },
  ], [providerNames])

  const templateRows = useMemo(() => providerNames
    .filter((name) => name !== 'custom')
    .map((name) => ({
      name,
      enabled: String(settings[`proxy_provider.${name}.enabled`] ?? '') !== '0' && String(settings[`proxy_provider.${name}.enabled`] ?? '') !== 'false',
      scheme: settings[`proxy_provider.${name}.scheme`] || 'http',
      host: settings[`proxy_provider.${name}.host`] || '',
      port: settings[`proxy_provider.${name}.port`] || '',
      username_template: settings[`proxy_provider.${name}.username_template`] || '',
      default_region: settings[`proxy_provider.${name}.default_region`] || '',
      default_ttl: settings[`proxy_provider.${name}.default_ttl`] || '',
      sid_length: settings[`proxy_provider.${name}.sid_length`] || '',
      password: settings[`proxy_provider.${name}.password`] || '',
    })), [providerNames, settings])

  const renderField = (field: SettingField) => {
    if (field.type === 'proxy_select') return <Select options={proxyOptions} placeholder={field.placeholder || ''} allowClear showSearch optionFilterProp="label" />
    if (field.key === 'default_proxy_provider' || field.key === 'proxy_provider.default' || field.key.endsWith('.proxy_provider')) {
      return <Select options={providerOptions} placeholder={field.placeholder || ''} allowClear showSearch optionFilterProp="label" />
    }
    return renderSettingControl(field)
  }

  const openTemplateEditor = useCallback((name?: string) => {
    const provider = (name || '').trim() || providerNames[0] || 'arxlabs'
    templateForm.setFieldsValue({
      provider,
      enabled: String(settings[`proxy_provider.${provider}.enabled`] ?? '1') !== '0' && String(settings[`proxy_provider.${provider}.enabled`] ?? '1') !== 'false',
      scheme: settings[`proxy_provider.${provider}.scheme`] || 'http',
      host: settings[`proxy_provider.${provider}.host`] || '',
      port: settings[`proxy_provider.${provider}.port`] || '',
      username_template: settings[`proxy_provider.${provider}.username_template`] || '',
      password: settings[`proxy_provider.${provider}.password`] || '',
      default_region: settings[`proxy_provider.${provider}.default_region`] || '',
      default_ttl: settings[`proxy_provider.${provider}.default_ttl`] || '',
      sid_length: settings[`proxy_provider.${provider}.sid_length`] || '8',
    })
    setTemplateGroupOpen(true)
  }, [providerNames, settings, templateForm])

  const saveTemplate = useCallback(async () => {
    const values = await templateForm.validateFields()
    const provider = String(values.provider || '').trim()
    if (!provider) {
      message.warning('请先填写模板名')
      return
    }
    setTemplateSaving(true)
    try {
      const data: Record<string, string> = {
        [`proxy_provider.${provider}.enabled`]: values.enabled ? '1' : '0',
        [`proxy_provider.${provider}.scheme`]: String(values.scheme || 'http'),
        [`proxy_provider.${provider}.host`]: String(values.host || ''),
        [`proxy_provider.${provider}.port`]: String(values.port || ''),
        [`proxy_provider.${provider}.username_template`]: String(values.username_template || ''),
        [`proxy_provider.${provider}.password`]: String(values.password || ''),
        [`proxy_provider.${provider}.default_region`]: String(values.default_region || ''),
        [`proxy_provider.${provider}.default_ttl`]: String(values.default_ttl || ''),
        [`proxy_provider.${provider}.sid_length`]: String(values.sid_length || ''),
      }
      await apiFetch('/settings', { method: 'PUT', body: JSON.stringify({ data }) })
      setSettings((prev) => ({ ...prev, ...data }))
      message.success('模板已保存')
      setTemplateGroupOpen(false)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存模板失败')
    } finally {
      setTemplateSaving(false)
    }
  }, [templateForm])

  return (
    <PageScaffold
      title="WorkPools"
      description="Stage worker pool 控制台：看并发容量、运行中任务、stage 状态和 WorkPool 配置；资源数据放在资源池菜单。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="总并发" value={totals.totalConcurrency} hint="所有 WorkPool capacity" tone="primary" />
        <StatCard label="运行中" value={totals.totalInflight} hint="当前 inflight jobs" tone={totals.totalInflight ? 'warning' : 'default'} />
        {JOB_STATUS_KEYS.map((key) => (
          <StatCard key={key} label={key} value={num(queue.counts?.[key])} tone={key === 'failed' ? 'danger' : key === 'running' ? 'info' : 'default'} />
        ))}
      </SummaryGrid>

      <ActionCard
        title="WorkPool stages"
        description="每个 stage 对应一个独立 worker pool；配置入口跟随卡片，不放到全局 settings。"
        actions={<CardToolbar><Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新状态</Button></CardToolbar>}
      />

      <div className="entity-grid">
        {stageRows.map((stage) => {
          const concurrency = num(queue.concurrency?.[stage.name] ?? stage.default_concurrency)
          const inflight = num(queue.inflight?.[stage.name])
          const percent = concurrency ? Math.min(100, Math.round((inflight / concurrency) * 100)) : 0
          const counts = jobCountsByStage[stage.name] || {}
          return (
            <EntityCard
              key={stage.name}
              tone={stageTone(stage)}
              title={stageLabel(stage.name)}
              subtitle={<Text code>{stage.name}</Text>}
              status={<Tag color={stage.implemented ? 'green' : 'default'}>{stage.implemented ? 'implemented' : 'stub'}</Tag>}
              badges={(
                <Space size={4} wrap>
                  <Tag>queued {counts.queued || 0}</Tag>
                  <Tag color="processing">running {counts.running || 0}</Tag>
                  <Tag color="red">failed {counts.failed || 0}</Tag>
                </Space>
              )}
              actions={<Button size="small" icon={<SettingOutlined />} onClick={() => openConfig(WORKPOOL_SETTING_GROUPS[stage.name])}>配置</Button>}
              footer={stage.description || 'stage worker pool'}
            >
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <KeyValueGrid>
                  <KeyValue label="容量" value={`${inflight}/${concurrency}`} />
                  <KeyValue label="Schema" value={<Space direction="vertical" size={0}><Text code>{stage.input_schema || '-'}</Text><Text code>{stage.output_schema || '-'}</Text></Space>} />
                  <KeyValue label="必需资源" value={resourceTags(stage.requires_resources || [])} />
                  <KeyValue label="可选资源" value={resourceTags(stage.optional_resources || [])} />
                </KeyValueGrid>
                <Progress percent={percent} size="small" showInfo={false} status={inflight >= concurrency && concurrency ? 'exception' : 'active'} />
              </Space>
            </EntityCard>
          )
        })}
      </div>

      <ActionCard title="ResourcePools" description="资源池卡片展示资源余量与状态；资源数据仍在邮箱、代理、卡、PayPal 号码、短信项目等资源页面管理。" />

      <div className="entity-grid">
        <ResourceCard title="email_pool" tone="info" values={{ total: poolValue(emailPool, 'total'), available: poolValue(emailPool, 'available'), claimed: poolValue(emailPool, 'claimed'), consumed: poolValue(emailPool, 'consumed'), blacklist: poolValue(emailPool, 'blacklist') }} onConfig={() => openConfig(RESOURCEPOOL_SETTING_GROUPS.email_pool)} />
        <ResourceCard title="card_pool" tone="warning" values={{ total: poolValue(cardPool, 'total'), available: poolValue(cardPool, 'available'), in_use: poolValue(cardPool, 'in_use'), used: poolValue(cardPool, 'used'), failed: poolValue(cardPool, 'failed'), banned: poolValue(cardPool, 'banned') }} onConfig={() => openConfig(RESOURCEPOOL_SETTING_GROUPS.card_pool)} />
        <ResourceCard title="paypal_number_pool" tone="primary" values={{ total: poolValue(paypalNumberPool, 'total'), available: poolValue(paypalNumberPool, 'available'), in_use: poolValue(paypalNumberPool, 'in_use'), cooling: poolValue(paypalNumberPool, 'cooling'), banned: poolValue(paypalNumberPool, 'banned') }} onConfig={() => openConfig(RESOURCEPOOL_SETTING_GROUPS.paypal_number_pool)} />
        <ResourceCard title="proxy_pool" tone="success" values={{ total: poolValue(proxyPool, 'total'), enabled: poolValue(proxyPool, 'enabled'), disabled: poolValue(proxyPool, 'disabled') }} onConfig={() => openConfig(RESOURCEPOOL_SETTING_GROUPS.proxy_pool)} />
        <EntityCard
          title="sms_pool"
          subtitle="multi-project sms provider routing"
          tone="primary"
          actions={<Button size="small" icon={<SettingOutlined />} onClick={() => openConfig(RESOURCEPOOL_SETTING_GROUPS.sms_pool)}>配置</Button>}
          footer={`${smsPool?.projects?.length || 0} projects`}
        >
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <KeyValueGrid>
              <KeyValue label="total" value={poolValue(smsPool, 'total')} />
              <KeyValue label="enabled" value={poolValue(smsPool, 'enabled')} />
            </KeyValueGrid>
            <div className="entity-list">
              {(smsPool?.projects || []).map((project) => (
                <div className="kv-row" key={project.id}>
                  <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text strong>{project.name}</Text>
                    <Tag color={project.enabled ? 'green' : 'default'}>{project.enabled ? 'on' : 'off'}</Tag>
                  </Space>
                  <Text type="secondary">{project.provider}</Text>
                </div>
              ))}
              {!(smsPool?.projects || []).length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无短信项目" />}
            </div>
          </Space>
        </EntityCard>
        {extraPools.map(([name, value]) => (
          <EntityCard key={name} title={name} subtitle="extra resource pool" tone="default">
            <CodeSurface>{JSON.stringify(value, null, 2)}</CodeSurface>
          </EntityCard>
        ))}
      </div>

      <ActionCard
        title="Proxy provider templates"
        description="这里新增/编辑动态代理模板；workpool 里的 proxy_provider 下拉框会自动读取这些模板名。"
        actions={<Button type="primary" onClick={() => openTemplateEditor()}>新增模板</Button>}
      />
      <div className="entity-grid">
        {templateRows.map((row) => (
          <EntityCard
            key={row.name}
            title={row.name}
            subtitle="proxy_provider template"
            tone={row.enabled ? 'success' : 'warning'}
            actions={<Button size="small" icon={<SettingOutlined />} onClick={() => openTemplateEditor(row.name)}>编辑</Button>}
            footer={`${row.scheme}://${row.host || '-'}:${row.port || '-'} · region=${row.default_region || '-'} · t=${row.default_ttl || '-'}`}
          >
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <KeyValueGrid>
                <KeyValue label="enabled" value={row.enabled ? 'yes' : 'no'} />
                <KeyValue label="sid_length" value={row.sid_length || '-'} />
                <KeyValue label="username_template" value={<Text code>{row.username_template || '-'}</Text>} />
              </KeyValueGrid>
              <Divider style={{ margin: '8px 0' }} />
              <Text type="secondary">password: {row.password ? '已配置' : '未配置'}</Text>
            </Space>
          </EntityCard>
        ))}
        {!templateRows.length && <Empty description="暂无代理模板，点击右上角新增模板" />}
      </div>

      <PopupCard
        open={!!configGroup}
        onCancel={() => setConfigGroup(null)}
        width={760}
        title={configGroup?.title || ''}
        footer={configGroup && configGroup.fields.length > 0 ? (
          <Button type="primary" icon={<SaveOutlined />} loading={savingSettings} onClick={saveConfig}>保存</Button>
        ) : null}
      >
        {configGroup && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <ActionCard title="配置边界" description={configGroup.description} />
            {configGroup.fields.length ? (
              <Form form={configForm} layout="vertical">
                <Row gutter={16}>
                  {configGroup.fields.map((field) => (
                    <Col key={field.key} xs={24} md={12}>
                      <Form.Item label={field.label} name={field.key} valuePropName={field.type === 'switch' ? 'checked' : undefined}>
                        {renderField(field)}
                      </Form.Item>
                    </Col>
                  ))}
                </Row>
              </Form>
            ) : (
              <Empty description={configGroup.emptyText || '暂无配置项'} />
            )}
          </Space>
        )}
      </PopupCard>

      <PopupCard
        open={templateGroupOpen}
        onCancel={() => setTemplateGroupOpen(false)}
        width={820}
        title="新增 / 编辑代理模板"
        footer={<Button type="primary" loading={templateSaving} onClick={saveTemplate}>保存模板</Button>}
      >
        <Form form={templateForm} layout="vertical">
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item label="模板名 / provider" name="provider" rules={[{ required: true, message: '请输入模板名' }]}>
                <Input placeholder="例如 arxlabs / 1024proxy / myproxy" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="启用" name="enabled" valuePropName="checked">
                <Switch checkedChildren="开" unCheckedChildren="关" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="scheme" name="scheme" rules={[{ required: true, message: '请输入 scheme' }]}>
                <Input placeholder="http / socks5 / socks5h" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="host" name="host" rules={[{ required: true, message: '请输入 host' }]}>
                <Input placeholder="us.arxlabs.io" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="port" name="port" rules={[{ required: true, message: '请输入 port' }]}>
                <Input placeholder="3010" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="默认 region" name="default_region">
                <Input placeholder="US / JP / BR" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="默认 t" name="default_ttl">
                <InputNumber style={{ width: '100%' }} placeholder="5" min={0} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="sid 长度" name="sid_length">
                <InputNumber style={{ width: '100%' }} placeholder="8" min={4} max={64} />
              </Form.Item>
            </Col>
            <Col xs={24}>
              <Form.Item label="username_template" name="username_template" rules={[{ required: true, message: '请输入用户名模板' }]}>
                <Input.TextArea placeholder="jnej1150915-region-{region}-sid-{sid}-t-{ttl}" autoSize={{ minRows: 2, maxRows: 4 }} />
              </Form.Item>
            </Col>
            <Col xs={24}>
              <Form.Item label="password" name="password">
                <Input.Password placeholder="代理密码" autoComplete="new-password" />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </PopupCard>
    </PageScaffold>
  )
}

function ResourceCard({ title, values, onConfig, tone }: { title: string; values: Record<string, number>; onConfig?: () => void; tone?: 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info' }) {
  return (
    <EntityCard title={title} subtitle="resource pool" tone={tone} actions={onConfig ? <Button size="small" icon={<SettingOutlined />} onClick={onConfig}>配置</Button> : null}>
      <KeyValueGrid>
        {Object.entries(values).map(([key, value]) => (
          <KeyValue label={key} value={value} key={key} />
        ))}
      </KeyValueGrid>
    </EntityCard>
  )
}
