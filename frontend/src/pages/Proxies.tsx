import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined, UploadOutlined } from '@ant-design/icons'

import { apiFetch } from '@/lib/api'

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
  const [form] = Form.useForm()
  const [bulkForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<Proxy[]>('/proxies')
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    return () => clearTimeout(initial)
  }, [reload])

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
    const lines = String(values.content || '')
      .split('\n')
      .map((s) => s.trim())
      .filter((s) => s && !s.startsWith('#'))
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

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: 'URL', dataIndex: 'url', ellipsis: true },
    { title: '标签', dataIndex: 'label', width: 120 },
    { title: '区域', dataIndex: 'region', width: 90 },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 80,
      render: (value: boolean, row: Proxy) => (
        <Switch checked={value} onChange={() => toggle(row)} />
      ),
    },
    {
      title: '统计',
      width: 160,
      render: (_v: unknown, row: Proxy) => (
        <Text type="secondary">
          ✓{row.success_count} / ✗{row.fail_count}
        </Text>
      ),
    },
    {
      title: '操作',
      width: 100,
      render: (_v: unknown, row: Proxy) => (
        <Popconfirm title="删除该代理?" onConfirm={() => remove(row)}>
          <Button size="small" danger>删除</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <Card>
      <Space style={{ marginBottom: 12 }} wrap>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新增</Button>
        <Button icon={<UploadOutlined />} onClick={() => setBulkOpen(true)}>批量导入</Button>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
        <Button icon={<ThunderboltOutlined />} onClick={checkAll}>检测全部</Button>
        <Popconfirm
          title={`确认删除选中的 ${selected.length} 条?`}
          onConfirm={batchDelete}
          disabled={!selected.length}
        >
          <Button icon={<DeleteOutlined />} danger disabled={!selected.length}>
            批量删除（{selected.length}）
          </Button>
        </Popconfirm>
      </Space>
      <Table
        rowKey="id"
        dataSource={rows}
        columns={columns as never}
        loading={loading}
        pagination={{ pageSize: 20 }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: setSelected,
        }}
      />

      <Modal
        open={createOpen}
        title="新增代理"
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        okText="添加"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="url" label="URL" rules={[{ required: true }]}>
            <Input placeholder="http://user:pass@host:port" />
          </Form.Item>
          <Form.Item name="label" label="标签"><Input /></Form.Item>
          <Form.Item name="region" label="区域"><Input /></Form.Item>
        </Form>
      </Modal>

      <Modal
        open={bulkOpen}
        title="批量导入代理"
        onCancel={() => { if (!bulkLoading) { setBulkOpen(false); setBulkResult(null) } }}
        onOk={submitBulk}
        okText="导入"
        confirmLoading={bulkLoading}
        maskClosable={!bulkLoading}
        closable={!bulkLoading}
        width={640}
      >
        <Typography.Paragraph type="secondary">
          每行一个代理 URL，例如 <Text code>http://user:pass@host:port</Text>、<Text code>socks5://user:pass@host:port</Text>。
          重复 URL 会自动跳过。
        </Typography.Paragraph>
        <Form form={bulkForm} layout="vertical">
          <Form.Item name="content" label="代理列表" rules={[{ required: true }]}>
            <Input.TextArea rows={10} placeholder="http://user:pass@1.2.3.4:1080" />
          </Form.Item>
          <Form.Item name="region" label="区域（可选）"><Input /></Form.Item>
        </Form>
        {bulkResult && (
          <Alert
            type="success"
            message={`新增 ${bulkResult.added}，跳过 ${bulkResult.skipped}`}
            showIcon
            style={{ marginTop: 12 }}
          />
        )}
      </Modal>
    </Card>
  )
}
