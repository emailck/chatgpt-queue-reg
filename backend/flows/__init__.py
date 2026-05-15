"""Import each flow module so its `register_flow(...)` calls run."""
from __future__ import annotations

from . import browser_debug  # noqa: F401
from . import chatgpt_payment_link  # noqa: F401
from . import chatgpt_register  # noqa: F401
from . import email_read  # noqa: F401
from . import payment_empty  # noqa: F401
