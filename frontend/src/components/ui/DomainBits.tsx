import type { ReactNode } from 'react'
import { Button, Progress, Space, Tag, Tooltip, Typography } from 'antd'
import { ExportOutlined } from '@ant-design/icons'

import { CopyButton } from '@/components/CopyButton'

const { Text } = Typography

export function IdBadge({ label, value, color }: { label: string; value?: number | string | null; color?: string }) {
  if (value === undefined || value === null || value === '') return null
  return <Tag color={color}>{label} #{value}</Tag>
}

export function LinkedIdBadges({ pipelineId, accountId, paymentLinkId, jobId }: { pipelineId?: number | null; accountId?: number | null; paymentLinkId?: number | null; jobId?: number | null }) {
  return (
    <Space size={4} wrap>
      <IdBadge label="Pipeline" value={pipelineId} />
      <IdBadge label="Account" value={accountId} color="cyan" />
      <IdBadge label="Link" value={paymentLinkId} color="purple" />
      <IdBadge label="Job" value={jobId} color="blue" />
    </Space>
  )
}

export function TokenBadges({ accessToken, refreshToken }: { accessToken?: string | null; refreshToken?: string | null }) {
  return (
    <Space size={4} wrap>
      <Tag color={accessToken ? 'green' : 'default'}>AT {accessToken ? 'yes' : 'no'}</Tag>
      <Tag color={refreshToken ? 'green' : 'default'}>RT {refreshToken ? 'yes' : 'no'}</Tag>
    </Space>
  )
}

export function Sub2ApiBadge({ status }: { status?: string | null }) {
  const value = String(status || '').trim().toLowerCase()
  if (!value) return <Tag>未同步</Tag>
  const meta: Record<string, { color: string; label: string }> = {
    active: { color: 'green', label: 'active' },
    error: { color: 'red', label: 'error' },
    disabled: { color: 'orange', label: 'disabled' },
    rate_limited: { color: 'orange', label: 'rate_limited' },
    temp_unschedulable: { color: 'orange', label: 'temp_unschedulable' },
    unschedulable: { color: 'orange', label: 'unschedulable' },
    pending_sync: { color: 'gold', label: '待同步' },
    pending_upload: { color: 'gold', label: '待同步' },
    sync_failed: { color: 'red', label: '同步失败' },
    status_check_failed: { color: 'red', label: '状态检查失败' },
    banned: { color: 'red', label: 'banned' },
  }
  const config = meta[value] || { color: 'default', label: value }
  return <Tag color={config.color}>{config.label}</Tag>
}

export function ProgressLine({ current, total, status }: { current: number; total: number; status?: 'normal' | 'exception' | 'active' | 'success' }) {
  const safeTotal = Math.max(total || 0, 1)
  return <Progress percent={Math.round((current / safeTotal) * 100)} size="small" status={status} format={() => `${current}/${total}`} />
}

export function ErrorCallout({ error }: { error?: string | null }) {
  const value = String(error || '').trim()
  if (!value || value.toLowerCase() === 'active') return null
  return <div className="error-callout"><Text type="danger">{value}</Text></div>
}

export function CopyableText({ value, label, code }: { value?: string | number | null; label?: string; code?: boolean }) {
  if (value === undefined || value === null || value === '') return <Text type="secondary">-</Text>
  const text = String(value)
  return (
    <Space size={4} className="copyable-line">
      <Tooltip title={text}>
        {code ? <Text code ellipsis>{text}</Text> : <Text ellipsis>{text}</Text>}
      </Tooltip>
      <CopyButton value={text} label={label} compact />
    </Space>
  )
}

export function UrlAction({ url, label = '打开' }: { url?: string | null; label?: ReactNode }) {
  if (!url) return <Text type="secondary">-</Text>
  return (
    <Space size={4}>
      <Button size="small" type="link" icon={<ExportOutlined />} href={url} target="_blank" rel="noreferrer">
        {label}
      </Button>
      <CopyButton value={url} label="URL" compact />
    </Space>
  )
}

export function SelectionSummary({ count }: { count: number }) {
  if (!count) return <Text type="secondary">未选择</Text>
  return <Tag color="blue">已选择 {count}</Tag>
}
