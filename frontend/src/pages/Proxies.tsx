import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Form, Input, Popconfirm, Select, Switch, Table, Tag, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined, UploadOutlined } from '@ant-design/icons'

import { ActionCard, CardToolbar, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, SelectionSummary } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

interface Proxy {
  id: number
  url: string
  label: string
  region: string
  enabled: boolean
  success_count: number
  fail_count: number
  last_used_at: string | null
}

export default function Proxies() {
  const [rows, setRows] = useState<Proxy[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkResult, setBulkResult] = useState<{ added: number; skipped: number } | null>(null)
  const [bulkLoading, setBulkLoading] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [regionFilter, setRegionFilter] = useState<string | undefined>()
  const [regions, setRegions] = useState<string[]>([])
  const [form] = Form.useForm()
  const [bulkForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const query = regionFilter ? `?region=${encodeURIComponent(regionFilter)}&limit=2000` : '?limit=2000'
      const [data, regionData] = await Promise.all([
        apiFetch<Proxy[]>(`/proxies${query}`),
        apiFetch<string[]>('/proxies/regions').catch(() => []),
      ])
      setRows(data)
      setRegions(regionData)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [regionFilter])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    return () => clearTimeout(initial)
  }, [reload])

  const summary = useMemo(() => {
    const regions = new Set(rows.map((row) => row.region).filter(Boolean))
    return {
      total: rows.length,
      enabled: rows.filter((row) => row.enabled).length,
      disabled: rows.filter((row) => !row.enabled).length,
      success: rows.reduce((sum, row) => sum + Number(row.success_count || 0), 0),
      failed: rows.reduce((sum, row) => sum + Number(row.fail_count || 0), 0),
      regions: regions.size,
    }
  }, [rows])

  const submitCreate = async () => {
    const values = await form.validateFields()
    try {
      await apiFetch('/proxies', { method: 'POST', body: JSON.stringify(values) })
      message.success('已添加')
      setCreateOpen(false)
      form.resetFields()
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '添加失败')
    }
  }

  const submitBulk = async () => {
    const values = await bulkForm.validateFields()
    const lines = String(values.content || '').split('\n').map((s) => s.trim()).filter((s) => s && !s.startsWith('#'))
    if (!lines.length) {
      message.warning('内容为空')
      return
    }
    setBulkLoading(true)
    try {
      const resp = await apiFetch<{ added: number; skipped: number }>('/proxies/bulk', {
        method: 'POST',
        body: JSON.stringify({ proxies: lines, region: values.region || '' }),
      })
      setBulkResult(resp)
      message.success(`新增 ${resp.added}，跳过 ${resp.skipped}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '导入失败')
    } finally {
      setBulkLoading(false)
    }
  }

  const toggle = async (row: Proxy) => {
    try {
      await apiFetch(`/proxies/${row.id}/toggle`, { method: 'PATCH' })
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新失败')
    }
  }

  const remove = async (row: Proxy) => {
    try {
      await apiFetch(`/proxies/${row.id}`, { method: 'DELETE' })
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/proxies/batch-delete', {
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

  const checkAll = async () => {
    try {
      await apiFetch('/proxies/check', { method: 'POST' })
      message.success('已派发检测任务，结果稍后刷新可见')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '请求失败')
    }
  }

  const columns: TableColumnsType<Proxy> = [
    {
      title: '标签',
      dataIndex: 'label',
      render: (value: string, row) => value || `Proxy #${row.id}`,
    },
    {
      title: 'URL',
      dataIndex: 'url',
      ellipsis: true,
      render: (value: string) => <CopyableText value={value} label="代理" />,
    },
    {
      title: 'Region',
      dataIndex: 'region',
      width: 120,
      render: (value: string) => <Tag color="blue">{value || 'no-region'}</Tag>,
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 120,
      render: (_: boolean, row) => <Switch checked={row.enabled} onChange={() => toggle(row)} checkedChildren="启用" unCheckedChildren="停用" />,
    },
    {
      title: '成功',
      dataIndex: 'success_count',
      width: 90,
      render: (value: number) => <Tag color="green">{value || 0}</Tag>,
    },
    {
      title: '失败',
      dataIndex: 'fail_count',
      width: 90,
      render: (value: number) => <Tag color={value ? 'red' : 'default'}>{value || 0}</Tag>,
    },
    {
      title: '最近使用',
      dataIndex: 'last_used_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 90,
      render: (_, row) => (
        <Popconfirm title="删除该代理?" onConfirm={() => remove(row)}>
          <Button size="small" danger>删除</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <PageScaffold
      title="代理池"
      description="资源池 / 代理：用表格管理区域、启用状态、成功/失败计数和最近使用时间。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="total" value={summary.total} tone="primary" />
        <StatCard label="enabled" value={summary.enabled} tone="success" />
        <StatCard label="disabled" value={summary.disabled} />
        <StatCard label="success total" value={summary.success} tone="success" />
        <StatCard label="failure total" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="regions" value={summary.regions} tone="info" />
      </SummaryGrid>

      <ActionCard
        title="代理池操作"
        description="注册链路锁住账号代理；支付模块按 WorkPool 配置选择不同 region 的代理。这里管理代理资源本身。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Select
              allowClear
              showSearch
              placeholder="Region 筛选"
              value={regionFilter}
              onChange={(value) => { setRegionFilter(value); setSelected([]) }}
              options={regions.map((region) => ({ value: region, label: region }))}
              style={{ width: 180 }}
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新增</Button>
            <Button icon={<UploadOutlined />} onClick={() => setBulkOpen(true)}>批量导入</Button>
            <Button icon={<ThunderboltOutlined />} onClick={checkAll}>检测全部</Button>
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
        scroll={{ x: 980 }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (total) => `共 ${total} 条` }}
        rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
      />

      <PopupCard open={createOpen} title="新增代理" onCancel={() => setCreateOpen(false)} onOk={submitCreate} okText="添加" width={560}>
        <Form form={form} layout="vertical">
          <Form.Item name="url" label="URL" rules={[{ required: true }]}><Input placeholder="http://user:pass@host:port" /></Form.Item>
          <Form.Item name="label" label="标签"><Input /></Form.Item>
          <Form.Item name="region" label="区域"><Input /></Form.Item>
        </Form>
      </PopupCard>

      <PopupCard open={bulkOpen} title="批量导入代理" onCancel={() => { if (!bulkLoading) { setBulkOpen(false); setBulkResult(null) } }} onOk={submitBulk} okText="导入" confirmLoading={bulkLoading} maskClosable={!bulkLoading} closable={!bulkLoading} width={720}>
        <Typography.Paragraph type="secondary">每行一个代理 URL，例如 <Text code>http://user:pass@host:port</Text>、<Text code>socks5://user:pass@host:port</Text>。重复 URL 会自动跳过。</Typography.Paragraph>
        <Form form={bulkForm} layout="vertical">
          <Form.Item name="content" label="代理列表" rules={[{ required: true }]}><Input.TextArea rows={10} placeholder="http://user:pass@1.2.3.4:1080" /></Form.Item>
          <Form.Item name="region" label="区域（可选）"><Input /></Form.Item>
        </Form>
        {bulkResult && <Alert type="success" message={`新增 ${bulkResult.added}，跳过 ${bulkResult.skipped}`} showIcon style={{ marginTop: 12 }} />}
      </PopupCard>
    </PageScaffold>
  )
}
