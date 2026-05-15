"""Cooperative cancellation primitives used by JobContext and flows."""
from __future__ import annotations


class JobCancelled(Exception):
    """Raised inside flows when the user requested cancellation."""


class JobInterrupted(Exception):
    """Raised when a process restart wiped the running marker."""
