"""Project-wide constants.

After v2 the queue/pipeline contract is fully data-driven (stage names live in
`backend/core/stages.py`, pipelines carry their own stage list in `input_json`).
Only **status enums for domain rows** survive in this file.
"""
from __future__ import annotations


# Job lifecycle ----------------------------------------------------------------
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_INTERRUPTED = "interrupted"

JOB_TERMINAL_STATUSES = frozenset({
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_INTERRUPTED,
})


# Account / payment-link / browser-session simple state strings ----------------
ACCOUNT_STATUS_CREATED = "created"
ACCOUNT_STATUS_REGISTERING = "registering"
ACCOUNT_STATUS_REGISTERED = "registered"
ACCOUNT_STATUS_PAYMENT_LINK_READY = "payment_link_ready"
ACCOUNT_STATUS_FAILED = "failed"

PAYMENT_LINK_STATUS_CREATED = "created"
PAYMENT_LINK_STATUS_EMPTY_PAYMENT_PENDING = "empty_payment_pending"
PAYMENT_LINK_STATUS_PAID_UNKNOWN = "paid_unknown"
PAYMENT_LINK_STATUS_FAILED = "failed"

BROWSER_SESSION_STATUS_OPENING = "opening"
BROWSER_SESSION_STATUS_OPEN = "open"
BROWSER_SESSION_STATUS_CLOSED = "closed"
BROWSER_SESSION_STATUS_FAILED = "failed"

# ChatGPT Team promo (kept compatible with legacy project) ---------------------
TEAM_PROMO_CODE = "STRIPEATLASGPT4BIZ050126"
TEAM_PROMO_URL = f"https://chatgpt.com/?promoCode={TEAM_PROMO_CODE}"

# Defaults ---------------------------------------------------------------------
DEFAULT_WORKER_CONCURRENCY = 3   # default per-stage concurrency
DEFAULT_MAX_ATTEMPTS = 1
