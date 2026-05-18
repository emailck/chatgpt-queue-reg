import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Drawer,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, LoadingOutlined, MailOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons'

import { JobLogPanel } from '@/components/JobLogPanel'
import { apiFetch, formatDateTime } from '@/lib/api'

const { Text, Paragraph } = Typography

interface EmailAccount {
  id: number
  provider: string
  email: string
  enabled: boolean
  pool_status: string
  has_password: boolean
  has_refresh_token: boolean
  api_base: string
  metadata: Record<string, unknown>
}

interface EmailMessage {
  id: number
  email: string
  provider: string
  subject: string
  sender: string
  body_text: string
  code: string
  received_at: string | null
  created_at: string | null
}

interface ImportResponse {
  type: string
  summary: { total: number; success: number; failed: number }
  errors?: string[]
}

interface PoolStats {
  available?: number
  claimed?: number
  consumed?: number
  blacklist?: number
  total?: number
}

const POOL_STATUS_COLOR: Record<string, string> = {
  available: 'green',
  claimed: 'processing',
  consumed: 'default',
  blacklist: 'red',
}

const POOL_STATUS_LABEL: Record<string, string> = {
  available: '可用',
  claimed: '占用中',
  consumed: '已消费',
  blacklist: '黑名单',
}

export default function Emails() {
  const [accounts, setAccounts] = useState<EmailAccount[]>([])
  const [messages, setMessages] = useState<EmailMessage[]>([])
  const [poolStats, setPoolStats] = useState<PoolStats>({})
  const [loading, setLoading] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importing, setImporting] = useState(false)
  const [readModalEmail, setReadModalEmail] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const [importResult, setImportResult] = useState<ImportResponse | null>(null)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [accountSelected, setAccountSelected] = useState<React.Key[]>([])
  const [messageSelected, setMessageSelected] = useState<React.Key[]>([])
  const [form] = Form.useForm()
  const [readForm] = Form.useForm()

  const reloadAccounts = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<EmailAccount[]>('/email/accounts')
      setAccounts(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const reloadMessages = useCallback(async () => {
    try {
      const data = await apiFetch<EmailMessage[]>('/email/messages?limit=100')
      setMessages(data)
    } catch {
      // ignore
    }
  }, [])

  const reloadPoolStats = useCallback(async () => {
    try {
      const data = await apiFetch<PoolStats>('/email/pool-stats')
      setPoolStats(data)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    const initial = setTimeout(() => {
      reloadAccounts()
      reloadMessages()
      reloadPoolStats()
    }, 0)
    const t = setInterval(() => {
      reloadMessages()
      reloadPoolStats()
    }, 6000)
    return () => {
      clearTimeout(initial)
      clearInterval(t)
    }
  }, [reloadAccounts, reloadMessages, reloadPoolStats])

  const submitImport = async () => {
    const values = await form.validateFields()
    setImporting(true)
    try {
      const resp = await apiFetch<ImportResponse>('/email/import', {
        method: 'POST',
        body: JSON.stringify({
          content: values.content,
          enabled: !!values.enabled,
          alias_split_enabled: !!values.alias_split_enabled,
          alias_split_count: Number(values.alias_split_count || 5),
          alias_include_original: !!values.alias_include_original,
          preview_limit: 100,
        }),
      })
      setImportResult(resp)
      message.success(`导入完成: 成功 ${resp.summary.success}/${resp.summary.total}`)
      reloadAccounts()
      reloadPoolStats()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const submitRead = async () => {
    const values = await readForm.validateFields()
    setReading(true)
    try {
      const resp = await apiFetch<{ job_id: number }>('/email/read', {
        method: 'POST',
        body: JSON.stringify({
          email: readModalEmail,
          timeout_seconds: Number(values.timeout_seconds || 120),
          keyword: values.keyword || '',
          code_regex: values.code_regex || null,
        }),
      })
      message.success(`已派发 job #${resp.job_id}`)
      setReadModalEmail(null)
      readForm.resetFields()
      setLogJobId(resp.job_id)
      reloadMessages()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '派发失败')
    } finally {
      setReading(false)
    }
  }

  const poolAction = async (action: 'requeue' | 'mark-consumed' | 'blacklist', email: string, note = '') => {
    try {
      await apiFetch(`/email/${action}`, {
        method: 'POST',
        body: JSON.stringify({ email, note }),
      })
      message.success(`已 ${action}: ${email}`)
      reloadAccounts()
      reloadPoolStats()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '操作失败')
    }
  }

  const batchDeleteAccounts = async () => {
    if (!accountSelected.length) return
    try {
      const items = accounts
        .filter((row) => accountSelected.includes(row.id))
        .map((row) => ({ email: row.email }))
      const resp = await apiFetch<{ summary?: { success: number; failed: number } }>('/email/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ items }),
      })
      message.success(`已删除 ${resp.summary?.success ?? items.length}`)
      setAccountSelected([])
      reloadAccounts()
      reloadPoolStats()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '批量删除失败')
    }
  }

  const batchDeleteMessages = async () => {
    if (!messageSelected.length) return
    try {
      await apiFetch('/email/messages/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ ids: messageSelected.map((id) => Number(id)) }),
      })
      message.success(`已删除 ${messageSelected.length} 条邮件`)
      setMessageSelected([])
      reloadMessages()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '批量删除失败')
    }
  }

  const accountColumns = [
    { title: '邮箱', dataIndex: 'email' },
    {
      title: '池状态',
      dataIndex: 'pool_status',
      width: 120,
      render: (value: string) => (
        <Tag color={POOL_STATUS_COLOR[value] || 'default'}>{POOL_STATUS_LABEL[value] || value}</Tag>
      ),
    },
    { title: 'OAuth', dataIndex: 'has_refresh_token', width: 70, render: (v: boolean) => (v ? '✓' : '-') },
    { title: '启用', dataIndex: 'enabled', width: 70, render: (v: boolean) => (v ? '✓' : '-') },
    {
      title: '操作',
      width: 320,
      render: (_v: unknown, row: EmailAccount) => (
        <Space size={4}>
          <Button size="small" icon={<MailOutlined />} onClick={() => setReadModalEmail(row.email)}>
            读取
          </Button>
          <Dropdown
            menu={{
              items: [
                { key: 'requeue', label: '退回池（设可用）' },
                { key: 'mark-consumed', label: '标记已消费' },
                { key: 'blacklist', label: '加入黑名单' },
              ],
              onClick: ({ key }) => poolAction(key as 'requeue' | 'mark-consumed' | 'blacklist', row.email),
            }}
          >
            <Button size="small">池操作 ▾</Button>
          </Dropdown>
        </Space>
      ),
    },
  ]

  const messageColumns = [
    { title: '邮箱', dataIndex: 'email' },
    {
      title: '验证码',
      dataIndex: 'code',
      render: (value: string) => (value ? <Text code>{value}</Text> : <Text type="secondary">-</Text>),
    },
    { title: '主题', dataIndex: 'subject', ellipsis: true },
    { title: '发件人', dataIndex: 'sender', ellipsis: true },
    { title: '收件时间', render: (_v: unknown, row: EmailMessage) => formatDateTime(row.received_at || row.created_at) },
  ]

  return (
    <>
      <Card>
        <Tabs
          items={[
            {
              key: 'accounts',
              label: '账号池（仅微软）',
              children: (
                <>
                  <Space style={{ marginBottom: 12 }} wrap>
                    <Button icon={<UploadOutlined />} type="primary" onClick={() => setImportOpen(true)}>
                      批量导入
                    </Button>
                    <Button icon={<ReloadOutlined />} onClick={reloadAccounts}>刷新</Button>
                    <Popconfirm
                      title={`确认删除选中的 ${accountSelected.length} 个邮箱?`}
                      onConfirm={batchDeleteAccounts}
                      disabled={!accountSelected.length}
                    >
                      <Button icon={<DeleteOutlined />} danger disabled={!accountSelected.length}>
                        批量删除（{accountSelected.length}）
                      </Button>
                    </Popconfirm>
                    <Tag color="green">可用 {poolStats.available ?? 0}</Tag>
                    <Tag color="processing">占用 {poolStats.claimed ?? 0}</Tag>
                    <Tag>已消费 {poolStats.consumed ?? 0}</Tag>
                    <Tag color="red">黑名单 {poolStats.blacklist ?? 0}</Tag>
                    <Tag>总计 {poolStats.total ?? 0}</Tag>
                  </Space>
                  <Table
                    rowKey="id"
                    dataSource={accounts}
                    columns={accountColumns as never}
                    loading={loading}
                    pagination={{ pageSize: 20 }}
                    rowSelection={{
                      selectedRowKeys: accountSelected,
                      onChange: setAccountSelected,
                    }}
                  />
                </>
              ),
            },
            {
              key: 'messages',
              label: '已读邮件',
              children: (
                <>
                  <Space style={{ marginBottom: 12 }}>
                    <Button icon={<ReloadOutlined />} onClick={reloadMessages}>刷新</Button>
                    <Popconfirm
                      title={`确认删除选中的 ${messageSelected.length} 条邮件?`}
                      onConfirm={batchDeleteMessages}
                      disabled={!messageSelected.length}
                    >
                      <Button icon={<DeleteOutlined />} danger disabled={!messageSelected.length}>
                        批量删除（{messageSelected.length}）
                      </Button>
                    </Popconfirm>
                  </Space>
                  <Table
                    rowKey="id"
                    dataSource={messages}
                    columns={messageColumns as never}
                    pagination={{ pageSize: 20 }}
                    rowSelection={{
                      selectedRowKeys: messageSelected,
                      onChange: setMessageSelected,
                    }}
                  />
                </>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={importOpen}
        title="批量导入微软邮箱"
        onCancel={() => { if (!importing) { setImportOpen(false); setImportResult(null) } }}
        onOk={submitImport}
        okText="导入"
        confirmLoading={importing}
        maskClosable={!importing}
        closable={!importing}
        width={720}
      >
        <Spin
          spinning={importing}
          tip="正在导入并校验微软邮箱（OAuth 探活并发执行中，可能需几秒到几十秒）..."
          indicator={<LoadingOutlined style={{ fontSize: 36 }} spin />}
        >
          <Paragraph>
            支持每行一条 <Text code>邮箱----密码----client_id----refresh_token</Text> 或 <Text code>邮箱----mailapi_url</Text>。
            可选启用 <Text code>裂变</Text>，按 + 别名扩展邮箱（最多 5 个）。
          </Paragraph>
          {importing && (
            <Alert
              type="info"
              message="正在导入邮箱，请稍候..."
              showIcon
              style={{ marginBottom: 12 }}
            />
          )}
          <Form form={form} layout="vertical" initialValues={{ enabled: true, alias_split_count: 5 }}>
            <Form.Item label="导入内容" name="content" rules={[{ required: true }]}>
              <Input.TextArea rows={8} placeholder="example@outlook.com----password----client_id----refresh_token" />
            </Form.Item>
            <Row gutter={12}>
              <Col span={6}>
                <Form.Item name="enabled" valuePropName="checked"><Checkbox>导入即启用</Checkbox></Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="alias_split_enabled" valuePropName="checked"><Checkbox>启用裂变</Checkbox></Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="alias_split_count" label="裂变数量"><InputNumber min={1} max={5} /></Form.Item>
              </Col>
              <Col span={6}>
                <Form.Item name="alias_include_original" valuePropName="checked"><Checkbox>含原邮箱</Checkbox></Form.Item>
              </Col>
            </Row>
          </Form>
          {importResult && (
            <Alert
              type={importResult.summary.failed > 0 ? 'warning' : 'success'}
              message={`成功 ${importResult.summary.success} / 失败 ${importResult.summary.failed}`}
              description={importResult.errors?.length ? importResult.errors.slice(0, 5).join('\n') : undefined}
              showIcon
              style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}
            />
          )}
        </Spin>
      </Modal>

      <Modal
        open={!!readModalEmail}
        title={readModalEmail ? `读取 ${readModalEmail}` : ''}
        onCancel={() => { if (!reading) { setReadModalEmail(null); readForm.resetFields() } }}
        onOk={submitRead}
        okText="开始"
        confirmLoading={reading}
        maskClosable={!reading}
        closable={!reading}
      >
        <Form form={readForm} layout="vertical" initialValues={{ timeout_seconds: 120 }}>
          <Form.Item name="keyword" label="关键字"><Input placeholder="可留空" /></Form.Item>
          <Form.Item name="timeout_seconds" label="超时时间(秒)"><InputNumber min={10} max={1800} /></Form.Item>
          <Form.Item name="code_regex" label="自定义验证码正则"><Input placeholder="留空使用默认" /></Form.Item>
        </Form>
      </Modal>

      <Drawer
        open={logJobId !== null}
        onClose={() => setLogJobId(null)}
        width={760}
        title={logJobId ? `Job #${logJobId} 原始日志` : ''}
      >
        {logJobId !== null && <JobLogPanel jobId={logJobId} onTerminal={() => reloadMessages()} />}
      </Drawer>
    </>
  )
}
