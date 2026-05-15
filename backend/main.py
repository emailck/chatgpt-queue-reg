"""FastAPI entry point for the ChatGPT queue/register project."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.accounts import router as accounts_router
from backend.api.access_tokens import router as access_tokens_router
from backend.api.auth import router as auth_router
from backend.api.browser_debug import router as browser_debug_router
from backend.api.emails import router as emails_router
from backend.api.jobs import router as jobs_router
from backend.api.payments import router as payments_router
from backend.api.proxies import router as proxies_router
from backend.api.settings import router as settings_router
import backend.compat  # noqa: F401 (installs sys.modules shims)
from backend.core.db import init_db
from backend.core.queue import get_pool, recover_orphan_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Importing the flow modules registers them with the dispatcher.
    import backend.flows  # noqa: F401

    interrupted = recover_orphan_jobs()
    if interrupted:
        logging.getLogger(__name__).warning("recovered %s orphan jobs as interrupted", interrupted)

    pool = get_pool()
    pool.start()
    try:
        yield
    finally:
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
app.include_router(access_tokens_router)
app.include_router(payments_router)
app.include_router(emails_router)
app.include_router(proxies_router)
app.include_router(browser_debug_router)
app.include_router(settings_router)


@app.get("/api/healthz", tags=["meta"])
def healthz():
    return {"ok": True}


_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_static_dir, "assets")),
        name="assets",
    )

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
    uvicorn.run("backend.main:app", host=host, port=port, reload=reload_enabled)
