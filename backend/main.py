"""FastAPI entry point for the ChatGPT queue/register project."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import Scope, Receive, Send

from backend.api.accounts import router as accounts_router
from backend.api.accounts_export import router as accounts_export_router
from backend.api.access_tokens import router as access_tokens_router
from backend.api.auth import router as auth_router
from backend.api.browser_debug import router as browser_debug_router
from backend.api.cards import router as cards_router
from backend.api.refresh_tokens import router as refresh_tokens_router
from backend.api.emails import router as emails_router
from backend.api.jobs import router as jobs_router
from backend.api.payments import router as payments_router
from backend.api.paypal_numbers import router as paypal_numbers_router
from backend.api.pipeline_configs import router as pipeline_configs_router
from backend.api.pools import router as pools_router
from backend.api.proxies import router as proxies_router
from backend.api.settings import router as settings_router
from backend.api.sms import router as sms_router
import backend.compat  # noqa: F401 (installs sys.modules shims)
from backend.core.db import init_db
from backend.core.queue import get_pool, recover_orphan_jobs
from backend.core.scheduler import get_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    import backend.stages  # noqa: F401
    import backend.core.pools  # noqa: F401

    interrupted = recover_orphan_jobs()
    if interrupted:
        logging.getLogger(__name__).warning("recovered %s orphan jobs as interrupted", interrupted)

    pool = get_pool()
    scheduler = get_scheduler()
    pool.start()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()
        pool.stop()


app = FastAPI(title="ChatGPT Queue Reg", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(jobs_router)
app.include_router(accounts_router)
app.include_router(accounts_export_router)
app.include_router(access_tokens_router)
app.include_router(payments_router)
app.include_router(emails_router)
app.include_router(proxies_router)
app.include_router(cards_router)
app.include_router(paypal_numbers_router)
app.include_router(pipeline_configs_router)
app.include_router(sms_router)
app.include_router(refresh_tokens_router)
app.include_router(pools_router)
app.include_router(browser_debug_router)
app.include_router(settings_router)


@app.get("/api/healthz", tags=["meta"])
def healthz():
    return {"ok": True}


_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_static_dir):
    _ASSET_MIME_MAP = {
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".json": "application/json",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ico": "image/x-icon",
        ".webp": "image/webp",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".html": "text/html; charset=utf-8",
    }

    _assets_dir = os.path.join(_static_dir, "assets")

    async def _serve_assets(scope: Scope, receive: Receive, send: Send) -> None:
        """Custom ASGI app serving /assets/* with correct MIME types.

        Avoids StaticFiles which may serve .js as text/plain on Windows.
        """
        rel = scope["path"][len("/assets/"):]
        # Basic traversal guard
        safe = os.path.normpath(rel).lstrip(os.sep).replace("\\", "/")
        if ".." in safe or safe.startswith("/"):
            resp = JSONResponse({"detail": "Not Found"}, status_code=404)
            await resp(scope, receive, send)
            return
        file_path = os.path.join(_assets_dir, safe)
        if not os.path.isfile(file_path):
            resp = JSONResponse({"detail": "Not Found"}, status_code=404)
            await resp(scope, receive, send)
            return
        _, ext = os.path.splitext(file_path)
        media_type = _ASSET_MIME_MAP.get(ext.lower(), "application/octet-stream")
        resp = FileResponse(file_path, media_type=media_type)
        await resp(scope, receive, send)

    app.mount("/assets", _serve_assets, name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        index_path = os.path.join(_static_dir, "index.html")
        if not os.path.isfile(index_path):
            return JSONResponse({"detail": "index.html not built yet"}, status_code=404)
        return FileResponse(index_path)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("APP_RELOAD", "0").lower() in {"1", "true", "yes"}
    shutdown_timeout = float(os.getenv("UVICORN_SHUTDOWN_TIMEOUT", "2"))
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        timeout_graceful_shutdown=shutdown_timeout,
    )
