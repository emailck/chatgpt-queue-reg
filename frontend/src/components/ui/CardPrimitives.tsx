import type { ReactNode } from 'react'
import { Button, Card, Checkbox, Empty, Modal, Pagination, Space, Typography } from 'antd'
import type { ModalProps } from 'antd'

const { Text, Title, Paragraph } = Typography

type Tone = 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info'

interface PageScaffoldProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
}

export function PageScaffold({ title, description, actions, children }: PageScaffoldProps) {
  return (
    <div className="page-stack">
      <section className="page-hero">
        <div className="page-hero-main">
          <Title level={2}>{title}</Title>
          {description && <Paragraph type="secondary">{description}</Paragraph>}
        </div>
        {actions && <div className="page-hero-actions">{actions}</div>}
      </section>
      {children}
    </div>
  )
}

export function SummaryGrid({ children }: { children: ReactNode }) {
  return <div className="summary-grid">{children}</div>
}

interface StatCardProps {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
  icon?: ReactNode
}

export function StatCard({ label, value, hint, tone = 'default', icon }: StatCardProps) {
  return (
    <Card className={`stat-card stat-card-${tone}`}>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
          <Text type="secondary">{label}</Text>
          {icon && <span className="stat-card-icon">{icon}</span>}
        </Space>
        <Text strong className="stat-card-value">{value}</Text>
        {hint && <Text type="secondary" className="stat-card-hint">{hint}</Text>}
      </Space>
    </Card>
  )
}

interface ActionCardProps {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children?: ReactNode
}

export function ActionCard({ title, description, actions, children }: ActionCardProps) {
  return (
    <Card className="action-card">
      <div className="action-card-layout">
        <div>
          {title && <Text strong className="action-card-title">{title}</Text>}
          {description && <Paragraph type="secondary" className="action-card-description">{description}</Paragraph>}
          {children}
        </div>
        {actions && <div className="action-card-actions">{actions}</div>}
      </div>
    </Card>
  )
}

interface EntityGridProps<T> {
  items: T[]
  renderItem: (item: T) => ReactNode
  pageSize?: number
  page?: number
  onPageChange?: (page: number) => void
  empty?: ReactNode
}

export function EntityGrid<T>({ items, renderItem, pageSize = 24, page = 1, onPageChange, empty }: EntityGridProps<T>) {
  const start = (page - 1) * pageSize
  const visible = items.slice(start, start + pageSize)
  if (!items.length) return <EmptyCard>{empty || '暂无数据'}</EmptyCard>
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div className="entity-grid">{visible.map((item) => renderItem(item))}</div>
      {items.length > pageSize && (
        <div className="entity-pagination">
          <Pagination current={page} pageSize={pageSize} total={items.length} onChange={onPageChange} showSizeChanger={false} />
        </div>
      )}
    </Space>
  )
}

interface EntityCardProps {
  title: ReactNode
  subtitle?: ReactNode
  status?: ReactNode
  badges?: ReactNode
  selected?: boolean
  onSelect?: (checked: boolean) => void
  actions?: ReactNode
  footer?: ReactNode
  tone?: Tone
  children?: ReactNode
}

export function EntityCard({ title, subtitle, status, badges, selected, onSelect, actions, footer, tone = 'default', children }: EntityCardProps) {
  return (
    <Card className={`entity-card entity-card-${tone} ${selected ? 'entity-card-selected' : ''}`}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <div className="entity-card-header">
          <Space align="start" size={10}>
            {onSelect && <Checkbox checked={!!selected} onChange={(evt) => onSelect(evt.target.checked)} />}
            <div className="entity-card-title-wrap">
              <Text strong className="entity-card-title">{title}</Text>
              {subtitle && <Text type="secondary" className="entity-card-subtitle">{subtitle}</Text>}
            </div>
          </Space>
          {status && <div className="entity-card-status">{status}</div>}
        </div>
        {badges && <div className="entity-card-badges">{badges}</div>}
        {children && <div className="entity-card-body">{children}</div>}
        {(actions || footer) && (
          <div className="entity-card-footer">
            {footer && <div className="entity-card-meta">{footer}</div>}
            {actions && <Space size={6} wrap>{actions}</Space>}
          </div>
        )}
      </Space>
    </Card>
  )
}

export function KeyValueGrid({ children }: { children: ReactNode }) {
  return <div className="metadata-grid">{children}</div>
}

export function KeyValue({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="kv-row">
      <Text type="secondary">{label}</Text>
      <div className="kv-value">{value || <Text type="secondary">-</Text>}</div>
    </div>
  )
}

export function CodeSurface({ children }: { children: ReactNode }) {
  return <pre className="code-surface">{children}</pre>
}

export function EmptyCard({ children }: { children?: ReactNode }) {
  return (
    <Card className="surface-card empty-card">
      <Empty description={children || '暂无数据'} />
    </Card>
  )
}

export function PopupCard({ children, className, width = 760, footer, ...props }: ModalProps) {
  return (
    <Modal
      width={width}
      footer={footer}
      className={`popup-card ${className || ''}`.trim()}
      centered
      destroyOnClose
      {...props}
    >
      {children}
    </Modal>
  )
}

export function CardToolbar({ children }: { children: ReactNode }) {
  return <Space size={8} wrap className="card-toolbar">{children}</Space>
}

export function DangerTextButton({ children, onClick, disabled }: { children: ReactNode; onClick?: () => void; disabled?: boolean }) {
  return <Button size="small" danger type="text" disabled={disabled} onClick={onClick}>{children}</Button>
}
