import { useEffect, useRef, useState } from 'react'
import { Empty, Space, Tag, Typography } from 'antd'

import { API_BASE, apiFetch, formatDateTime } from '@/lib/api'

interface JobEvent {
  id: number
  job_id: number
  pipeline_id: number | null
  level: string
  event_type: string
  message: string
  payload: Record<string, unknown>
  created_at: string | null
}

const LEVEL_COLOR: Record<string, string> = {
  info: 'blue',
  warning: 'gold',
  error: 'red',
  debug: 'default',
}

interface JobLogPanelProps {
  jobId: number
  /** Stop the live stream after a terminal status is observed. */
  onTerminal?: (status: string) => void
}

export function JobLogPanel({ jobId, onTerminal }: JobLogPanelProps) {
  const [events, setEvents] = useState<JobEvent[]>([])
  const [error, setError] = useState<string>('')
  const lastIdRef = useRef<number>(0)
  const onTerminalRef = useRef(onTerminal)

  useEffect(() => {
    onTerminalRef.current = onTerminal
  }, [onTerminal])

  useEffect(() => {
    let cancelled = false
    setEvents([])
    setError('')
    lastIdRef.current = 0

    const replay = async () => {
      try {
        const initial = await apiFetch<JobEvent[]>(`/jobs/${jobId}/events?since_id=0&limit=500`)
        if (cancelled) return
        if (initial.length) {
          setEvents(initial)
          lastIdRef.current = initial[initial.length - 1].id
        }
      } catch (err) {
        const detail = err instanceof Error ? err.message : 'failed to load events'
        if (!cancelled) setError(detail)
      }
    }

    replay()

    const url = `${API_BASE}/jobs/${jobId}/events/stream?since_id=${lastIdRef.current}`
    const stream = new EventSource(url)

    stream.onmessage = (msg) => {
      if (cancelled) return
      try {
        const data = JSON.parse(msg.data)
        if (data?.kind === 'status' && (data.status === 'succeeded' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'interrupted')) {
          onTerminalRef.current?.(String(data.status))
          stream.close()
          return
        }
        if (typeof data?.id === 'number') {
          // Replayed event from list-then-stream race window.
          if (data.id <= lastIdRef.current) return
          lastIdRef.current = data.id
          setEvents((prev) => [...prev, data])
          return
        }
        if (data?.kind === 'event') {
          // live event published by JobContext.log()
          const synthetic: JobEvent = {
            id: lastIdRef.current + 1,
            job_id: jobId,
            pipeline_id: null,
            level: String(data.level || 'info'),
            event_type: String(data.event_type || 'log'),
            message: String(data.message || ''),
            payload: data.payload || {},
            created_at: new Date().toISOString(),
          }
          lastIdRef.current = synthetic.id
          setEvents((prev) => [...prev, synthetic])
        }
      } catch {
        // ignore malformed frames
      }
    }

    stream.onerror = () => {
      if (cancelled) return
      // EventSource auto-reconnects; surface a soft warning.
      setError('日志流暂时中断，正在尝试重连')
    }

    return () => {
      cancelled = true
      stream.close()
    }
  }, [jobId])

  if (!events.length) {
    return error ? (
      <Typography.Text type="danger">{error}</Typography.Text>
    ) : (
      <Empty description="暂无事件" />
    )
  }

  return (
    <div style={{ maxHeight: 480, overflowY: 'auto', fontFamily: 'monospace', fontSize: 12 }}>
      {events.map((evt) => (
        <div key={evt.id} style={{ padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
          <Space size={6}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {formatDateTime(evt.created_at)}
            </Typography.Text>
            <Tag color={LEVEL_COLOR[evt.level] || 'default'} style={{ margin: 0 }}>
              {evt.level || 'info'}
            </Tag>
            <Tag style={{ margin: 0 }}>{evt.event_type || 'log'}</Tag>
          </Space>
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: 2 }}>
            {evt.message}
          </div>
        </div>
      ))}
    </div>
  )
}
