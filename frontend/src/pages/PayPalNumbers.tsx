import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Form, Input, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'

import { ActionCard, CardToolbar, PageScaffold, PopupCard, StatCard, SummaryGrid } from '@/components/ui/CardPrimitives'
import { CopyableText, ErrorCallout, IdBadge, SelectionSummary, UrlAction } from '@/components/ui/DomainBits'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text, Paragraph } = Typography

const STATUS_OPTIONS = [
  { value: 'available', label: 'available' },
  { value: 'in_use', label: 'in_use' },
  { value: 'used', label: 'used' },
  { value: 'failed', label: 'failed' },
  { value: 'banned', label: 'banned' },
]

const STATUS_COLOR: Record<string, string> = {
  available: 'green',
  in_use: 'processing',
  used: 'default',
  failed: 'red',
  banned: 'volcano',
}

interface PayPalNumber {
  id: number
  phone: string
  smsurl: string
  status: string
  use_count: number
  last_used_at: string | null
  last_error: string
  bound_job_id: number | null
  note: string
  created_at: string | null
  updated_at: string | null
}

interface BulkResult {
  created: number
}

function parseBulkLines(content: string, note: string) {
  return content
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
    .map((line) => {
      const parts = line.split(/\s*(?:----|\|\|\||,)\s*/)
      return {
        phone: parts[0]?.trim() || '',
        smsurl: parts[1]?.trim() || '',
        note: parts[2]?.trim() || note || '',
      }
    })
    .filter((item) => item.phone)
}

function normalizeRows(data: unknown): PayPalNumber[] {
  if (Array.isArray(data)) return data as PayPalNumber[]
  if (!data || typeof data !== 'object') return []
  const record = data as Record<string, unknown>
  if (Array.isArray(record.items)) return record.items as PayPalNumber[]
  if (Array.isArray(record.rows)) return record.rows as PayPalNumber[]
  if (Array.isArray(record.data)) return record.data as PayPalNumber[]
  return []
}

export default function PayPalNumbers() {
  const [rows, setRows] = useState<PayPalNumber[]>([])
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [createOpen, setCreateOpen] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkLoading, setBulkLoading] = useState(false)
  const [bulkResult, setBulkResult] = useState<BulkResult | null>(null)
  const [editing, setEditing] = useState<PayPalNumber | null>(null)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [form] = Form.useForm()
  const [bulkForm] = Form.useForm()
  const [editForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : ''
      const data = await apiFetch<unknown>(`/paypal-numbers${query}`)
      setRows(normalizeRows(data))
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    return () => clearTimeout(initial)
  }, [reload])

  const summary = useMemo(() => ({
    total: rows.length,
    available: rows.filter((row) => row.status === 'available').length,
    inUse: rows.filter((row) => row.status === 'in_use').length,
    used: rows.filter((row) => row.status === 'used').length,
    failed: rows.filter((row) => row.status === 'failed').length,
    banned: rows.filter((row) => row.status === 'banned').length,
  }), [rows])

  const submitCreate = async () => {
    const values = await form.validateFields()
    try {
      await apiFetch('/paypal-numbers', {
        method: 'POST',
        body: JSON.stringify({ phone: values.phone, smsurl: values.smsurl || '', note: values.note || '' }),
      })
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
    const numbers = parseBulkLines(String(values.content || ''), String(values.note || ''))
    if (!numbers.length) {
      message.warning('没有可导入的号码')
      return
    }
    setBulkLoading(true)
    try {
      const resp = await apiFetch<BulkResult>('/paypal-numbers/bulk', {
        method: 'POST',
        body: JSON.stringify({ numbers }),
      })
      setBulkResult(resp)
      message.success(`已导入 ${resp.created}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '导入失败')
    } finally {
      setBulkLoading(false)
    }
  }

  const openEdit = (row: PayPalNumber) => {
    setEditing(row)
    editForm.setFieldsValue({
      phone: row.phone,
      smsurl: row.smsurl,
      status: row.status,
      note: row.note,
      last_error: row.last_error,
    })
  }

  const submitEdit = async () => {
    if (!editing) return
    const values = await editForm.validateFields()
    try {
      await apiFetch(`/paypal-numbers/${editing.id}`, {
        method: 'PATCH',
        body: JSON.stringify(values),
      })
      message.success('已保存')
      setEditing(null)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败')
    }
  }

  const deleteOne = async (row: PayPalNumber) => {
    try {
      await apiFetch(`/paypal-numbers/${row.id}`, { method: 'DELETE' })
      message.success('已删除')
      setSelected((prev) => prev.filter((id) => Number(id) !== row.id))
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/paypal-numbers/batch-delete', {
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

  const columns: TableColumnsType<PayPalNumber> = [
    {
      title: '手机号',
      dataIndex: 'phone',
      render: (value: string) => <CopyableText value={value} label="手机号" />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (value: string) => <Tag color={STATUS_COLOR[value] || 'default'}>{value || '-'}</Tag>,
    },
    {
      title: 'SMS URL',
      dataIndex: 'smsurl',
      render: (value: string) => <UrlAction url={value} label="打开" />,
    },
    {
      title: '使用',
      dataIndex: 'use_count',
      width: 90,
      render: (value: number) => value || 0,
    },
    {
      title: '绑定 Job',
      dataIndex: 'bound_job_id',
      width: 120,
      render: (value: number | null) => <IdBadge label="Job" value={value} color="blue" />,
    },
    {
      title: '备注',
      dataIndex: 'note',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
    {
      title: '错误',
      dataIndex: 'last_error',
      ellipsis: true,
      render: (value: string) => <ErrorCallout error={value} />,
    },
    {
      title: '最近使用',
      dataIndex: 'last_used_at',
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '更新',
      dataIndex: 'updated_at',
      width: 170,
      render: (value: string | null, row) => formatDateTime(value || row.created_at),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 150,
      render: (_, row) => (
        <Space size={6}>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>
          <Popconfirm title="删除该 PayPal 号码?" onConfirm={() => deleteOne(row)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <PageScaffold
      title="PayPal 号码池"
      description="PayPal 手机号和短信 URL 是 payment WorkPool 的一次性资源；支付阶段会从 paypal_number_pool 领取可用号码。"
      actions={<Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>}
    >
      <SummaryGrid>
        <StatCard label="total" value={summary.total} tone="primary" />
        <StatCard label="available" value={summary.available} tone="success" />
        <StatCard label="in_use" value={summary.inUse} tone="info" />
        <StatCard label="used" value={summary.used} />
        <StatCard label="failed" value={summary.failed} tone={summary.failed ? 'danger' : 'default'} />
        <StatCard label="banned" value={summary.banned} tone={summary.banned ? 'danger' : 'default'} />
      </SummaryGrid>

      <ActionCard
        title="号码资源操作"
        description="导入格式支持每行 phone----smsurl----note、phone|||smsurl|||note 或 phone,smsurl,note；号码本身不在 pipeline 创建时传入。"
        actions={(
          <CardToolbar>
            <SelectionSummary count={selected.length} />
            <Select allowClear placeholder="状态筛选" value={statusFilter} onChange={setStatusFilter} options={STATUS_OPTIONS} style={{ width: 150 }} />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新增</Button>
            <Button icon={<UploadOutlined />} onClick={() => setBulkOpen(true)}>批量导入</Button>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={reload}>刷新</Button>
            <Popconfirm title={`确认删除选中的 ${selected.length} 个号码?`} onConfirm={batchDelete} disabled={!selected.length}>
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
        scroll={{ x: 1180 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
      />

      <PopupCard open={createOpen} title="新增 PayPal 号码" onCancel={() => setCreateOpen(false)} onOk={submitCreate} okText="添加" width={620}>
        <Form form={form} layout="vertical">
          <Form.Item name="phone" label="手机号" rules={[{ required: true }]}><Input placeholder="+1..." /></Form.Item>
          <Form.Item name="smsurl" label="SMS URL"><Input placeholder="https://..." /></Form.Item>
          <Form.Item name="note" label="备注"><Input /></Form.Item>
        </Form>
      </PopupCard>

      <PopupCard open={bulkOpen} title="批量导入 PayPal 号码" onCancel={() => { if (!bulkLoading) { setBulkOpen(false); setBulkResult(null) } }} onOk={submitBulk} okText="导入" confirmLoading={bulkLoading} maskClosable={!bulkLoading} closable={!bulkLoading} width={760}>
        <Paragraph type="secondary">每行一个号码，支持 <Text code>phone----smsurl----note</Text>、<Text code>phone|||smsurl|||note</Text> 或 CSV 简单格式。</Paragraph>
        <Form form={bulkForm} layout="vertical">
          <Form.Item name="content" label="号码列表" rules={[{ required: true }]}><Input.TextArea rows={10} placeholder="+15551234567----https://sms.example/item/1" /></Form.Item>
          <Form.Item name="note" label="默认备注"><Input /></Form.Item>
        </Form>
        {bulkResult && <Alert type="success" message={`已导入 ${bulkResult.created}`} showIcon style={{ marginTop: 12 }} />}
      </PopupCard>

      <PopupCard open={!!editing} title={editing ? `编辑 PayPal 号码 #${editing.id}` : ''} onCancel={() => setEditing(null)} onOk={submitEdit} okText="保存" width={620}>
        <Form form={editForm} layout="vertical">
          <Form.Item name="phone" label="手机号" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="smsurl" label="SMS URL"><Input /></Form.Item>
          <Form.Item name="status" label="状态"><Select options={STATUS_OPTIONS} /></Form.Item>
          <Form.Item name="note" label="备注"><Input /></Form.Item>
          <Form.Item name="last_error" label="失败原因"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </PopupCard>
    </PageScaffold>
  )
}
