import { Button, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'

interface CopyButtonProps {
  value: string
  label?: string
  size?: 'small' | 'middle' | 'large'
  type?: 'default' | 'link' | 'text'
  compact?: boolean
  className?: string
}

export function CopyButton({ value, label, size = 'small', type = 'text', compact = false, className }: CopyButtonProps) {
  if (!value) return null
  const handle = async () => {
    try {
      await navigator.clipboard.writeText(value)
      message.success(label ? `已复制 ${label}` : '已复制')
    } catch {
      message.error('复制失败')
    }
  }
  return (
    <Button size={size} type={type} className={className} icon={<CopyOutlined />} onClick={handle}>
      {compact ? '' : label || ''}
    </Button>
  )
}
