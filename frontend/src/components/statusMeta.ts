const STATUS_PALETTE: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: 'Queued' },
  running: { color: 'processing', label: 'Running' },
  succeeded: { color: 'success', label: 'Succeeded' },
  failed: { color: 'error', label: 'Failed' },
  cancelled: { color: 'warning', label: 'Cancelled' },
  interrupted: { color: 'volcano', label: 'Interrupted' },
  created: { color: 'default', label: 'Created' },
  registered: { color: 'success', label: 'Registered' },
  registering: { color: 'processing', label: 'Registering' },
  payment_link_ready: { color: 'cyan', label: 'Payment Link Ready' },
  empty_payment_pending: { color: 'gold', label: 'Empty Payment Pending' },
  paid_unknown: { color: 'gold', label: 'Paid (?)' },
  open: { color: 'processing', label: 'Open' },
  opening: { color: 'default', label: 'Opening' },
  closed: { color: 'default', label: 'Closed' },
}

export function getStatusMeta(status?: string | null) {
  const value = String(status || '').toLowerCase()
  return STATUS_PALETTE[value] || { color: 'default', label: value || '-' }
}
