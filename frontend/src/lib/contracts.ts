export const STAGE_LABELS: Record<string, string> = {
  register: '注册',
  payment_link: '生成长链',
  payment: '付款',
  oauth_codex: '获取 Codex RT',
  rt_keepalive: 'RT 保活',
}

export function stageLabel(stage?: string | null): string {
  const key = String(stage || '')
  return STAGE_LABELS[key] || key || '-'
}

export interface StageMeta {
  name: string
  implemented: boolean
  requires_resources: string[]
  optional_resources: string[]
  default_concurrency: number
  rate_limit_per_min: number | null
  input_schema: string
  output_schema: string
  description: string
}

export type StageMap = Record<string, StageMeta>

export interface QueueStats {
  concurrency: Record<string, number>
  inflight: Record<string, number>
  counts: Record<string, number>
}

export interface EmailPoolStats {
  available?: number
  claimed?: number
  consumed?: number
  blacklist?: number
  total?: number
}

export interface CardPoolStats {
  total?: number
  available?: number
  in_use?: number
  used?: number
  failed?: number
  banned?: number
}

export interface PayPalNumberPoolStats {
  total?: number
  available?: number
  in_use?: number
  used?: number
  failed?: number
  banned?: number
}

export interface ProxyPoolStats {
  total?: number
  enabled?: number
  disabled?: number
}

export interface SmsProjectSummary {
  id: number
  name: string
  provider: string
  enabled: boolean
}

export interface SmsPoolStats {
  projects?: SmsProjectSummary[]
  total?: number
  enabled?: number
}

export type PoolStats = EmailPoolStats | CardPoolStats | PayPalNumberPoolStats | ProxyPoolStats | SmsPoolStats | Record<string, unknown>

export type PoolsResponse = Record<string, PoolStats>

export interface Pipeline {
  id: number
  preset: string
  stages: string[]
  stop_after: string
  stage_inputs?: Record<string, unknown>
  resource_bindings?: Record<string, unknown>
  status: string
  current_stage: string
  total_steps: number
  completed_steps: number
  account_id: number | null
  payment_link_id: number | null
  proxy_id?: number | null
  proxy_url: string
  input?: Record<string, unknown>
  result?: Record<string, unknown>
  error: string
  cancel_requested?: boolean
  created_at: string | null
  started_at?: string | null
  finished_at: string | null
  updated_at: string | null
}

export interface Job {
  id: number
  pipeline_id: number | null
  type: string
  status: string
  priority?: number
  attempt: number
  max_attempts: number
  input?: Record<string, unknown>
  result?: Record<string, unknown>
  error: string
  account_id: number | null
  payment_link_id: number | null
  email_address: string
  proxy_id: number | null
  proxy_url: string
  cancel_requested?: boolean
  created_at: string | null
  queued_at?: string | null
  started_at: string | null
  finished_at: string | null
  updated_at?: string | null
}

export interface PipelineDetail {
  pipeline: Pipeline
  jobs: Job[]
}

export interface JobRetryResponse {
  job_id: number
  retried_job_id: number
  pipeline_id: number
  stage: string
}

export interface JobEvent {
  id: number
  job_id: number
  pipeline_id: number | null
  level: string
  event_type: string
  message: string
  payload: Record<string, unknown>
  created_at: string | null
}

export type JobStreamFrame =
  | JobEvent
  | { kind: 'event'; level?: string; event_type?: string; message?: string; payload?: Record<string, unknown> }
  | { kind: 'status'; status?: string; error?: string }
  | { kind: 'result'; result?: Record<string, unknown> }
