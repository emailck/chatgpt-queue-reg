import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import { CopyButton } from '@/components/CopyButton'
import { API_BASE, apiFetch, formatDateTime } from '@/lib/api'

const { Text, Paragraph } = Typography

interface AccessTokenAccount {
  id: number
  pipeline_id: number | null
  chatgpt_account_id: number | null
  email: string
  password: string
  account_id: string
  workspace_id: string
  access_token: string
  refresh_token: string
  id_token: string
  session_token: string
  has_access_token: boolean
  has_refresh_token: boolean
  has_session_token: boolean
  user_agent: string
  proxy_url: string
  note: string
  metadata: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

const TXT_FIELD_OPTIONS = [
  'email',
  'password',
  'account_id',
  'workspace_id',
  'access_token',
  'refresh_token',
  'id_token',
  'session_token',
  'proxy_url',
  'user_agent',
] as const

export default function AccessTokens() {
  const [rows, setRows] = useState<AccessTokenAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [showSecrets, setShowSecrets] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [exportOpen, setExportOpen] = useState(false)
  const [detail, setDetail] = useState<AccessTokenAccount | null>(null)
  const [exportForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<AccessTokenAccount[]>(`/access-tokens?include_secrets=${showSecrets}`)
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [showSecrets])

  useEffect(() => { reload() }, [reload])

  const openDetail = useCallback(async (row: AccessTokenAccount) => {
    try {
      const full = await apiFetch<AccessTokenAccount>(
        `/access-tokens/${row.id}?include_secrets=true`,
      )
      setDetail(full)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载详情失败')
    }
  }, [])

  const deleteOne = async (row: AccessTokenAccount) => {
    try {
      await apiFetch(`/access-tokens/${row.id}`, { method: 'DELETE' })
      message.success('已删除')
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/access-tokens/batch-delete', {
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

  const submitExport = async () => {
    const values = await exportForm.validateFields()
    const idsArg = selected.length ? selected.join(',') : ''
    const params = new URLSearchParams()
    params.set('fmt', values.fmt)
    if (idsArg) params.set('ids', idsArg)
    if (values.fmt === 'txt') {
      params.set('separator', values.separator || '----')
      const f = (values.fields || []) as string[]
      params.set('fields', f.length ? f.join(',') : 'email,password,access_token,refresh_token')
    }
    const url = `${API_BASE}/access-tokens/export?${params.toString()}`
    window.open(url, '_blank')
    setExportOpen(false)
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string) => (
        <Space size={2}>
          <Text>{value}</Text>
          <CopyButton value={value} />
        </Space>
      ),
    },
    {
      title: 'access_token',
      dataIndex: 'access_token',
      render: (value: string) => (
        <Space size={2}>
          <Text code style={{ fontSize: 11 }}>{value || '-'}</Text>
          {value && <CopyButton value={value} />}
        </Space>
      ),
    },
    {
      title: 'refresh_token',
      dataIndex: 'refresh_token',
      render: (value: string) => (
        <Space size={2}>
          <Text code style={{ fontSize: 11 }}>{value || '-'}</Text>
          {value && <CopyButton value={value} />}
        </Space>
      ),
    },
    { title: '代理', dataIndex: 'proxy_url', ellipsis: true, width: 180 },
    { title: 'pipeline', dataIndex: 'pipeline_id', width: 90, render: (v: number | null) => (v ? `#${v}` : '-') },
    { title: '创建时间', width: 170, render: (_v: unknown, row: AccessTokenAccount) => formatDateTime(row.created_at) },
    {
      title: '操作',
      width: 200,
      render: (_v: unknown, row: AccessTokenAccount) => (
        <Space size={4}>
          <Button size="small" onClick={() => openDetail(row)}>详情</Button>
          <Popconfirm title="删除该 AT?" onConfirm={() => deleteOne(row)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Card>
        <Space style={{ marginBottom: 12 }} wrap>
          <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
          <Button icon={<DownloadOutlined />} type="primary" onClick={() => setExportOpen(true)}>
            导出{selected.length ? `（${selected.length}）` : '全部'}
          </Button>
          <Popconfirm
            title={`确认删除选中的 ${selected.length} 条?`}
            onConfirm={batchDelete}
            disabled={!selected.length}
          >
            <Button icon={<DeleteOutlined />} danger disabled={!selected.length}>
              批量删除（{selected.length}）
            </Button>
          </Popconfirm>
          <Space size={6}>
            <Switch checked={showSecrets} onChange={setShowSecrets} />
            <Text>显示完整 token</Text>
          </Space>
          <Tag>共 {rows.length} 条</Tag>
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

      <Modal
        open={exportOpen}
        title="导出 AT 号池"
        onCancel={() => setExportOpen(false)}
        onOk={submitExport}
        okText="下载"
        width={560}
      >
        <Paragraph type="secondary">
          {selected.length
            ? `当前选中 ${selected.length} 条，仅导出选中。`
            : '未选中任何条目，将导出全部。'}
        </Paragraph>
        <Form
          form={exportForm}
          layout="vertical"
          initialValues={{
            fmt: 'txt',
            separator: '----',
            fields: ['email', 'password', 'access_token', 'refresh_token'],
          }}
        >
          <Form.Item label="格式" name="fmt">
            <Select
              options={[
                { value: 'txt', label: 'TXT（行式，可指定字段顺序与分隔符）' },
                { value: 'csv', label: 'CSV' },
                { value: 'json', label: 'JSON（含完整 cookies / fingerprint）' },
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, curr) => prev.fmt !== curr.fmt}
          >
            {({ getFieldValue }) => (getFieldValue('fmt') === 'txt' ? (
              <>
                <Form.Item label="分隔符" name="separator">
                  <Input />
                </Form.Item>
                <Form.Item label="字段顺序" name="fields">
                  <Select
                    mode="multiple"
                    options={TXT_FIELD_OPTIONS.map((f) => ({ value: f, label: f }))}
                    placeholder="按需勾选并拖动排序"
                  />
                </Form.Item>
              </>
            ) : null)}
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        open={!!detail}
        onClose={() => setDetail(null)}
        width={720}
        title={detail ? `AT #${detail.id} — ${detail.email}` : ''}
      >
        {detail && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            {[
              ['email', detail.email],
              ['password', detail.password],
              ['account_id', detail.account_id],
              ['workspace_id', detail.workspace_id],
              ['access_token', detail.access_token],
              ['refresh_token', detail.refresh_token],
              ['id_token', detail.id_token],
              ['session_token', detail.session_token],
              ['user_agent', detail.user_agent],
              ['proxy_url', detail.proxy_url],
            ].map(([label, value]) => (
              <div key={label}>
                <Text type="secondary" style={{ fontSize: 12 }}>{label}</Text>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Text code style={{ wordBreak: 'break-all', flex: 1 }}>{value || '-'}</Text>
                  {value && <CopyButton value={String(value)} />}
                </div>
              </div>
            ))}
          </Space>
        )}
      </Drawer>
    </>
  )
}
