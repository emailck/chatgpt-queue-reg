/**
 * Lightweight API client + small helpers shared by all pages.
 *
 * This project never embeds auth (yet) so the client is much simpler than
 * the legacy `lib/utils.ts`.  We keep the same shape (`apiFetch`, `getToken`)
 * so existing patterns translate when we do add login.
 */
export const API_BASE = '/api'

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  const headers = new Headers(init?.headers || {})
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(url, { ...(init || {}), headers })
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!response.ok) {
    const detail =
      (data && typeof data === 'object' && 'detail' in data && (data as Record<string, unknown>).detail) ||
      response.statusText ||
      'Request failed'
    throw new ApiError(String(detail), response.status, data)
  }
  return data as T
}

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function clearToken(): void {
  localStorage.removeItem('token')
}

export function formatDateTime(value: string | number | null | undefined): string {
  if (!value) return '-'
  const d = typeof value === 'number' ? new Date(value) : new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return d.toLocaleString()
}

export function formatDuration(start?: string | number | null, end?: string | number | null): string {
  if (!start) return '-'
  const startMs = typeof start === 'number' ? start : Date.parse(start)
  const endMs = end ? (typeof end === 'number' ? end : Date.parse(end)) : Date.now()
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return '-'
  const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}
