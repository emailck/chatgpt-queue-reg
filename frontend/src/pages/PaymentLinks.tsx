import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Drawer, Popconfirm, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { BugOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'

import { CopyButton } from '@/components/CopyButton'
import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text } = Typography

interface PaymentLink {
  id: number
  account_id: number
  pipeline_id: number | null
  job_id: number | null
  plan: string
  promo_code: string
  checkout_url: string
  checkout_session_id: string
  status: string
  error: string
  created_at: string | null
  updated_at: string | null
}

export default function PaymentLinks() {
  const [rows, setRows] = useState<PaymentLink[]>([])
  const [loading, setLoading] = useState(false)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [selected, setSelected] = useState<React.Key[]>([])

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<PaymentLink[]>('/payment-links?limit=300')
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 6000)
    return () => clearInterval(t)
  }, [reload])

  const debugBrowser = async (row: PaymentLink) => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/payment-links/${row.id}/debug-browser`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      message.success(`已派发 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '调起失败')
    }
  }

  const triggerEmpty = async (row: PaymentLink) => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/payment-links/${row.id}/payment`, {
        method: 'POST',
      })
      message.success(`已派发占位支付 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '请求失败')
    }
  }

  const deleteOne = async (row: PaymentLink) => {
    try {
      await apiFetch(`/payment-links/${row.id}`, { method: 'DELETE' })
      message.success('已删除')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/payment-links/batch-delete', {
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

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '账号 ID', dataIndex: 'account_id', width: 80 },
    {
      title: '长链',
      dataIndex: 'checkout_url',
      render: (value: string) =>
        value ? (
          <Space>
            <Tooltip title={value}>
              <a href={value} target="_blank" rel="noopener noreferrer">打开</a>
            </Tooltip>
            <CopyButton value={value} />
          </Space>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    { title: 'cs_id', dataIndex: 'checkout_session_id', ellipsis: true },
    {
      title: '套餐',
      dataIndex: 'plan',
      width: 90,
      render: (value: string) => (
        <Tag color={value === 'plus' ? 'magenta' : 'blue'}>{(value || '-').toUpperCase()}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 160,
      render: (value: string) => <StatusTag status={value} />,
    },
    { title: '创建时间', width: 180, render: (_v: unknown, row: PaymentLink) => formatDateTime(row.created_at) },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (value: string) => (value ? <Text type="danger">{value}</Text> : <Text type="secondary">-</Text>),
    },
    {
      title: '操作',
      width: 280,
      render: (_v: unknown, row: PaymentLink) => (
        <Space size={4} wrap>
          <Button size="small" icon={<BugOutlined />} onClick={() => debugBrowser(row)}>
            抓 HAR
          </Button>
          <Button size="small" type="dashed" onClick={() => triggerEmpty(row)}>
            占位支付
          </Button>
          <Popconfirm title="删除该长链记录?" onConfirm={() => deleteOne(row)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ] as const

  return (
    <>
      <Card>
        <Space style={{ marginBottom: 12 }} wrap>
          <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
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
      </Card>

      <Drawer
        open={logJobId !== null}
        onClose={() => setLogJobId(null)}
        width={720}
        title={logJobId ? `Job #${logJobId}` : ''}
      >
        {logJobId !== null && <JobLogPanel jobId={logJobId} />}
      </Drawer>
    </>
  )
}
