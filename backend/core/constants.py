"""Project-wide constants and enum-like string literals.

Keeping these in one place makes it obvious which strings are part of the
queue/pipeline contract vs ad-hoc values.
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

# Job kinds --------------------------------------------------------------------
JOB_TYPE_CHATGPT_REGISTER = "chatgpt_register"
JOB_TYPE_CHATGPT_PAYMENT_LINK = "chatgpt_payment_link"
JOB_TYPE_PAYMENT_EMPTY = "payment_empty"
JOB_TYPE_EMAIL_READ = "email_read"
JOB_TYPE_BROWSER_DEBUG = "browser_debug"

ALL_JOB_TYPES = frozenset({
    JOB_TYPE_CHATGPT_REGISTER,
    JOB_TYPE_CHATGPT_PAYMENT_LINK,
    JOB_TYPE_PAYMENT_EMPTY,
    JOB_TYPE_EMAIL_READ,
    JOB_TYPE_BROWSER_DEBUG,
})

# Pipeline ---------------------------------------------------------------------
PIPELINE_TYPE_CHATGPT_ACCOUNT = "chatgpt_account_with_payment_link"
PIPELINE_TYPE_CHATGPT_REGISTER_ONLY = "chatgpt_register_only"

PIPELINE_STEP_REGISTER = "register"
PIPELINE_STEP_PAYMENT_LINK = "payment_link"
PIPELINE_STEP_PAYMENT_EMPTY = "payment_empty"
PIPELINE_STEP_DONE = "done"

# Full pipeline (register -> payment link -> empty placeholder)
PIPELINE_STEPS_ORDERED = (
    PIPELINE_STEP_REGISTER,
    PIPELINE_STEP_PAYMENT_LINK,
    PIPELINE_STEP_PAYMENT_EMPTY,
)

# Register-only pipeline (just stash AT)
PIPELINE_STEPS_REGISTER_ONLY = (PIPELINE_STEP_REGISTER,)

PIPELINE_STEP_TO_JOB_TYPE = {
    PIPELINE_STEP_REGISTER: JOB_TYPE_CHATGPT_REGISTER,
    PIPELINE_STEP_PAYMENT_LINK: JOB_TYPE_CHATGPT_PAYMENT_LINK,
    PIPELINE_STEP_PAYMENT_EMPTY: JOB_TYPE_PAYMENT_EMPTY,
}

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
DEFAULT_WORKER_CONCURRENCY = 3
DEFAULT_MAX_ATTEMPTS = 1
