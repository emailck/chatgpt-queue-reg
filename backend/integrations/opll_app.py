"""Adapter for the uploaded app.py OPLL PayPal long-link generator.

The uploaded app.py is a Tk desktop application, but its OPLL payment-link
functions are pure HTTP helpers guarded by ``if __name__ == "__main__"``.
This adapter imports it with lightweight tkinter stubs so backend workers can
reuse ``generate_opll_paypal_long_link`` without requiring a GUI runtime.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

_APP_MODULE_NAME = "_uploaded_opll_app"


class _Dummy:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __call__(self, *args: Any, **kwargs: Any) -> "_Dummy":
        return self

    def __getattr__(self, _name: str) -> "_Dummy":
        return self

    def __iter__(self):
        return iter(())

    def __bool__(self) -> bool:
        return False


def _install_tkinter_stubs() -> None:
    if "tkinter" in sys.modules:
        return
    tk = types.ModuleType("tkinter")
    for name in (
        "BOTH", "END", "LEFT", "RIGHT", "X",
    ):
        setattr(tk, name, name.lower())
    for name in (
        "BooleanVar", "IntVar", "StringVar", "Tk", "Toplevel", "Label", "Menu",
        "PanedWindow",
    ):
        setattr(tk, name, _Dummy)
    tk.filedialog = _Dummy()
    tk.messagebox = _Dummy()
    tk.simpledialog = _Dummy()

    ttk = types.ModuleType("tkinter.ttk")
    ttk.__getattr__ = lambda _name: _Dummy  # type: ignore[attr-defined]

    font = types.ModuleType("tkinter.font")
    font.nametofont = lambda *a, **k: _Dummy()

    scrolled = types.ModuleType("tkinter.scrolledtext")
    scrolled.ScrolledText = _Dummy

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.font"] = font
    sys.modules["tkinter.scrolledtext"] = scrolled


def load_app_module():
    existing = sys.modules.get(_APP_MODULE_NAME)
    if existing is not None:
        return existing
    _install_tkinter_stubs()
    root = Path(__file__).resolve().parents[2]
    app_path = root / "app.py"
    if not app_path.exists():
        raise RuntimeError(f"uploaded app.py not found: {app_path}")
    spec = importlib.util.spec_from_file_location(_APP_MODULE_NAME, app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load app.py from {app_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_APP_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def generate_paypal_long_link(
    *,
    access_token: str,
    country: str = "US",
    currency: str = "USD",
    create_proxy_url: str = "",
    followup_proxy_url: str = "",
    approve_proxy_url: str = "",
    target_amount: str = "",
) -> dict[str, Any]:
    module = load_app_module()
    fn = getattr(module, "generate_opll_paypal_long_link", None)
    if fn is None:
        raise RuntimeError("app.py missing generate_opll_paypal_long_link")
    return fn(
        access_token=access_token,
        country=country,
        currency=currency,
        create_proxy_url=create_proxy_url,
        followup_proxy_url=followup_proxy_url,
        approve_proxy_url=approve_proxy_url,
        target_amount=target_amount,
    )
