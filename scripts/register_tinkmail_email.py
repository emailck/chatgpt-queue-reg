#!/usr/bin/env python3
"""Create one TinkMail mailbox and import it into the local email pool."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BASE = os.getenv("QUEUE_API_BASE", "http://127.0.0.1:8000")
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def http_json(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["content-type"] = "application/json"
    req = Request(API_BASE.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc
    return json.loads(raw) if raw.strip() else {}


def main() -> int:
    ap = argparse.ArgumentParser(description="注册一个 TinkMail 邮箱并写入邮箱池")
    ap.add_argument("--account", default="", help="本地名，不填自动生成")
    ap.add_argument("--password", default="", help="TinkMail 密码，不填自动生成")
    ap.add_argument("--secure-email", default="", help="恢复邮箱，不填用配置/自动值")
    ap.add_argument("--proxy-url", default="", help="覆盖代理；不填走配置里的默认代理")
    ap.add_argument("--disabled", action="store_true", help="创建后不放入可用池")
    ap.add_argument("--wait", action="store_true", help="等待结束")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    stage_input = {"enabled": not args.disabled}
    for key in ("account", "password", "secure_email", "proxy_url"):
        value = getattr(args, key if key != "secure_email" else "secure_email", "")
        if value:
            stage_input[key] = value
    resp = http_json("POST", "/api/pipelines", {
        "preset": "tinkmail_email_register",
        "stage_inputs": {"tinkmail_email_register": stage_input},
    })
    pid = int(resp.get("pipeline_ids", [0])[0] or 0)
    if not pid:
        raise RuntimeError(f"未返回 pipeline id: {resp}")
    result = {"pipeline_id": pid}
    if not args.json:
        print(f"已创建 TinkMail 注册流水线 pipeline={pid}")
    if args.wait:
        while True:
            doc = http_json("GET", f"/api/pipelines/{pid}")
            pipe = doc.get("pipeline") or {}
            jobs = doc.get("jobs") or []
            if not args.json:
                print(f"pipeline={pid} status={pipe.get('status')} error={(pipe.get('error') or '')[:160]}")
            if pipe.get("status") in TERMINAL:
                result["pipeline"] = pipe
                result["jobs"] = jobs
                break
            time.sleep(5)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not args.wait or (result.get("pipeline") or {}).get("status") == "succeeded" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        raise SystemExit(130)
