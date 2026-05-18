import { useEffect, useRef, useState } from 'react'
import { Button, Empty, Space, Typography } from 'antd'

import { API_BASE, apiFetch, formatDateTime } from '@/lib/api'
import type { JobEvent } from '@/lib/contracts'

interface JobLogPanelProps {
  jobId: number
  onTerminal?: (status: string) => void
}

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])

function hasPayload(payload: unknown): payload is Record<string, unknown> {
  return !!payload && typeof payload === 'object' && Object.keys(payload as Record<string, unknown>).length > 0
}

function formatPayload(payload: unknown): string {
  if (!hasPayload(payload)) return ''
  return `\n${JSON.stringify(payload, null, 2)}`
}

function formatEventLine(evt: Partial<JobEvent>): string {
  const time = evt.created_at ? formatDateTime(evt.created_at) : formatDateTime(new Date().toISOString())
  const level = String(evt.level || 'info').toUpperCase()
  const eventType = String(evt.event_type || 'log')
  const message = String(evt.message || '')
  return `[${time}] ${level} ${eventType} ${message}${formatPayload(evt.payload)}`
}

function formatFrame(data: Record<string, unknown>): string {
  if (typeof data.id === 'number') {
    return formatEventLine(data as unknown as JobEvent)
  }
  if (data.kind === 'event') {
    return formatEventLine({
      level: String(data.level || 'info'),
      event_type: String(data.event_type || 'log'),
      message: String(data.message || ''),
      payload: (data.payload as Record<string, unknown>) || {},
      created_at: new Date().toISOString(),
    })
  }
  if (data.kind === 'status') {
    const status = String(data.status || 'unknown')
    const error = data.error ? `\n${String(data.error)}` : ''
    return `--- status: ${status} ---${error}`
  }
  if (data.kind === 'result') {
    return `--- result ---\n${JSON.stringify(data.result || {}, null, 2)}`
  }
  return JSON.stringify(data, null, 2)
}

export function JobLogPanel({ jobId, onTerminal }: JobLogPanelProps) {
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState('')
  const lastIdRef = useRef<number>(0)
  const onTerminalRef = useRef(onTerminal)
  const viewportRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    onTerminalRef.current = onTerminal
  }, [onTerminal])

  useEffect(() => {
    viewportRef.current?.scrollTo({ top: viewportRef.current.scrollHeight })
  }, [transcript])

  useEffect(() => {
    let cancelled = false
    const append = (line: string) => {
      if (!line || cancelled) return
      setTranscript((prev) => (prev ? `${prev}\n${line}` : line))
    }

    const reset = setTimeout(() => {
      if (cancelled) return
      setTranscript('')
      setError('')
      lastIdRef.current = 0
    }, 0)

    const replay = async () => {
      try {
        const initial = await apiFetch<JobEvent[]>(`/jobs/${jobId}/events?since_id=0&limit=500`)
        if (cancelled) return
        if (initial.length) {
          lastIdRef.current = initial[initial.length - 1].id
          setTranscript(initial.map(formatEventLine).join('\n'))
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
        const data = JSON.parse(msg.data) as Record<string, unknown>
        if (typeof data.id === 'number') {
          if (data.id <= lastIdRef.current) return
          lastIdRef.current = data.id
        }
        append(formatFrame(data))
        if (data.kind === 'status') {
          const status = String(data.status || '')
          if (TERMINAL_STATUSES.has(status)) {
            onTerminalRef.current?.(status)
            stream.close()
          }
        }
      } catch {
        append(msg.data)
      }
    }

    stream.onerror = () => {
      if (cancelled) return
      setError('日志流暂时中断，正在尝试重连')
    }

    return () => {
      cancelled = true
      clearTimeout(reset)
      stream.close()
    }
  }, [jobId])

  if (!transcript) {
    return error ? (
      <Typography.Text type="danger">{error}</Typography.Text>
    ) : (
      <Empty description="暂无日志" />
    )
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      {error && <Typography.Text type="warning">{error}</Typography.Text>}
      <Button size="small" onClick={() => navigator.clipboard?.writeText(transcript)}>
        复制日志
      </Button>
      <pre
        ref={viewportRef}
        style={{
          maxHeight: 560,
          overflowY: 'auto',
          margin: 0,
          padding: 12,
          borderRadius: 8,
          background: 'rgba(0,0,0,0.35)',
          border: '1px solid rgba(255,255,255,0.08)',
          color: '#d6e4ff',
          fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
          fontSize: 12,
          lineHeight: 1.55,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {transcript}
      </pre>
    </Space>
  )
}
