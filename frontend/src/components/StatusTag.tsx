import { Tag } from 'antd'

import { getStatusMeta } from './statusMeta'

export function StatusTag({ status }: { status?: string | null }) {
  const config = getStatusMeta(status)
  return <Tag color={config.color}>{config.label}</Tag>
}
