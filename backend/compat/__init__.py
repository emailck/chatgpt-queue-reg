"""Compatibility shims registered before the queue starts.

Importing this package installs lightweight modules under names the legacy
ChatGPT integration expects (`smstome_tool`, ...).
"""
from __future__ import annotations

import sys

from . import smstome_tool as _smstome_tool

sys.modules.setdefault("smstome_tool", _smstome_tool)
