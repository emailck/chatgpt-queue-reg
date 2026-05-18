import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
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

type FreePool = 'at' | 'rt'

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
  codex_token_id: number | null
  codex_token_alive: boolean
  codex_token_has_refresh_token: boolean
  codex_token_last_error: string
  sub2api_external_id: string
  sub2api_status: string
  sub2api_uploaded_at: string | null
  sub2api_status_checked_at: string | null
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

const SUB2API_STATUS_COLORS: Record<string, string> = {
  uploaded: 'blue',
  active: 'green',
  alive: 'green',
  ok: 'green',
  pending_upload: 'orange',
  upload_failed: 'red',
  sync_failed: 'red',
  dead: 'red',
  disabled: 'default',
  invalid: 'red',
  expired: 'red',
}

export default function AccessTokens() {
  const [rows, setRows] = useState<AccessTokenAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [pool, setPool] = useState<FreePool>('at')
  const [showSecrets, setShowSecrets] = useState(false)
  const [selected, setSelected] = useState<React.Key[]>([])
  const [exportOpen, setExportOpen] = useState(false)
  const [detail, setDetail] = useState<AccessTokenAccount | null>(null)
  const [fetchingRtId, setFetchingRtId] = useState<number | null>(null)
  const [exportForm] = Form.useForm()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<AccessTokenAccount[]>(`/access-tokens?pool=${pool}&include_secrets=${showSecrets}`)
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [pool, showSecrets])

  useEffect(() => {
    const initial = setTimeout(reload, 0)
    return () => clearTimeout(initial)
  }, [reload])

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

  const fetchRefreshToken = async (row: AccessTokenAccount) => {
    setFetchingRtId(row.id)
    try {
      const resp = await apiFetch<{
        job_id: number | null
        already_has_refresh_token: boolean
        already_running: boolean
      }>(`/access-tokens/${row.id}/refresh-token`, { method: 'POST' })
      if (resp.already_has_refresh_token) {
        message.success('该账号已有 RT')
      } else if (resp.already_running) {
        message.info(`RT 任务已在运行：#${resp.job_id}`)
      } else {
        message.success(`已提交获取 RT 任务：#${resp.job_id}`)
      }
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '提交获取 RT 失败')
    } finally {
      setFetchingRtId(null)
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
      params.set('fields', f.length ? f.join(',') : 'email,password,access_token,refresh_token,session_token')
    }
    params.set('pool', pool)
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
      title: 'Codex RT',
      render: (_v: unknown, row: AccessTokenAccount) => (
        <Space direction="vertical" size={2}>
          <Space size={2}>
            <Text code style={{ fontSize: 11 }}>{row.refresh_token || '-'}</Text>
            {row.refresh_token && <CopyButton value={row.refresh_token} />}
          </Space>
          {row.codex_token_id && (
            <Space size={4} wrap>
              <Text type="secondary">#{row.codex_token_id}</Text>
              <Tag color={SUB2API_STATUS_COLORS[row.sub2api_status] || 'default'}>{row.sub2api_status || 'unknown'}</Tag>
              {!row.codex_token_alive && <Tag color="red">失效</Tag>}
            </Space>
          )}
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
          {pool === 'at' && !row.codex_token_has_refresh_token && (
            <Button
              size="small"
              type="primary"
              loading={fetchingRtId === row.id}
              onClick={() => fetchRefreshToken(row)}
            >
              获取 RT
            </Button>
          )}
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
          <Radio.Group
            value={pool}
            onChange={(e) => { setPool(e.target.value); setSelected([]) }}
            optionType="button"
            buttonStyle="solid"
            options={[
              { value: 'at', label: 'Free AT 池' },
              { value: 'rt', label: 'Free RT 池' },
            ]}
          />
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
        title={`导出 ${pool === 'rt' ? 'Free RT 池' : 'Free AT 池'}`}
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
            fields: ['email', 'password', 'access_token', 'refresh_token', 'session_token'],
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
              ['codex_refresh_token', detail.refresh_token],
              ['sub2api_status', detail.sub2api_status],
              ['sub2api_external_id', detail.sub2api_external_id],
              ['codex_token_last_error', detail.codex_token_last_error],
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
