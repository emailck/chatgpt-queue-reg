import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Drawer, Dropdown, Input, Modal, Popconfirm, Space, Table, Tooltip, Typography, message } from 'antd'
import { BugOutlined, DeleteOutlined, MailOutlined, ReloadOutlined } from '@ant-design/icons'

import { CopyButton } from '@/components/CopyButton'
import { JobLogPanel } from '@/components/JobLogPanel'
import { StatusTag } from '@/components/StatusTag'
import { apiFetch } from '@/lib/api'

const { Text } = Typography

interface Account {
  id: number
  email: string
  password: string
  status: string
  account_id: string
  workspace_id: string
  proxy_url: string
  last_error: string
  last_payment_link_id: number | null
  last_payment_link_url: string
  user_agent: string
  has_access_token: boolean
  has_session_token: boolean
  created_at: string | null
  registered_at: string | null
  updated_at: string | null
}

export default function Accounts() {
  const [rows, setRows] = useState<Account[]>([])
  const [loading, setLoading] = useState(false)
  const [logJobId, setLogJobId] = useState<number | null>(null)
  const [emailModalAccount, setEmailModalAccount] = useState<Account | null>(null)
  const [emailKeyword, setEmailKeyword] = useState('')
  const [selected, setSelected] = useState<React.Key[]>([])

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<Account[]>('/accounts?limit=300')
      setRows(data)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 5000)
    return () => clearInterval(t)
  }, [reload])

  const triggerReadEmail = async (account: Account, keyword: string) => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/accounts/${account.id}/read-email`, {
        method: 'POST',
        body: JSON.stringify({ keyword, timeout_seconds: 120 }),
      })
      message.success(`已派发收邮件 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
      setEmailModalAccount(null)
      setEmailKeyword('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '收邮件失败')
    }
  }

  const triggerDebugBrowser = async (account: Account) => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/accounts/${account.id}/debug-browser`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      message.success(`已派发调试浏览器 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '调起浏览器失败')
    }
  }

  const retryPaymentLink = async (account: Account, plan: 'team' | 'plus') => {
    try {
      const resp = await apiFetch<{ job_id: number }>(`/accounts/${account.id}/payment-link/retry`, {
        method: 'POST',
        body: JSON.stringify({ plan }),
      })
      message.success(`已重试 ${plan} 长链生成 job #${resp.job_id}`)
      setLogJobId(resp.job_id)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重试失败')
    }
  }

  const deleteAccount = async (account: Account) => {
    try {
      await apiFetch(`/accounts/${account.id}`, { method: 'DELETE' })
      message.success(`已删除 ${account.email}`)
      reload()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const batchDelete = async () => {
    if (!selected.length) return
    try {
      const resp = await apiFetch<{ deleted: number }>('/accounts/batch-delete', {
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
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string) => (
        <Space>
          <Text>{value}</Text>
          <CopyButton value={value} />
        </Space>
      ),
    },
    {
      title: '密码',
      dataIndex: 'password',
      render: (value: string) => (
        <Space>
          <Text code style={{ fontSize: 12 }}>{value || '-'}</Text>
          {value && <CopyButton value={value} />}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 140,
      render: (value: string) => <StatusTag status={value} />,
    },
    { title: '代理', dataIndex: 'proxy_url', ellipsis: true },
    { title: 'Account ID', dataIndex: 'account_id', ellipsis: true },
    {
      title: '最近长链',
      dataIndex: 'last_payment_link_url',
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
    {
      title: '错误',
      dataIndex: 'last_error',
      ellipsis: true,
      render: (value: string) => (value ? <Text type="danger">{value}</Text> : <Text type="secondary">-</Text>),
    },
    {
      title: '操作',
      width: 320,
      render: (_v: unknown, row: Account) => (
        <Space size={4} wrap>
          <Button size="small" icon={<MailOutlined />} onClick={() => setEmailModalAccount(row)}>
            收邮件
          </Button>
          <Button size="small" icon={<BugOutlined />} onClick={() => triggerDebugBrowser(row)}>
            调试浏览器
          </Button>
          {row.status !== 'registering' && (
            <Dropdown
              menu={{
                items: [
                  { key: 'team', label: '生成 Team 长链' },
                  { key: 'plus', label: '生成 Plus 长链 (IDR)' },
                ],
                onClick: ({ key }) => retryPaymentLink(row, key as 'team' | 'plus'),
              }}
            >
              <Button size="small" type="dashed">支付长链 ▾</Button>
            </Dropdown>
          )}
          <Popconfirm title="删除该账号?" onConfirm={() => deleteAccount(row)}>
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
            title={`确认删除选中的 ${selected.length} 个账号?`}
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

      <Modal
        open={!!emailModalAccount}
        onCancel={() => { setEmailModalAccount(null); setEmailKeyword('') }}
        onOk={() => emailModalAccount && triggerReadEmail(emailModalAccount, emailKeyword)}
        title={emailModalAccount ? `收 ${emailModalAccount.email} 的邮件` : ''}
        okText="开始读取"
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text>关键字 (subject/body 包含；留空匹配最新一封)</Text>
          <Input
            value={emailKeyword}
            onChange={(e) => setEmailKeyword(e.target.value)}
            placeholder="例如 ChatGPT"
          />
        </Space>
      </Modal>

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
