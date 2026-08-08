#!/usr/bin/env python3
"""Try team Codex token first; fallback to OAuth RT; upload result to sub2api.

Flow:
  1. Resolve account by --account-id or --email.
  2. Run codex_token for target team workspace(s).
  3. If codex_token succeeds, upload its workspace_sessions with sub2api_sync.
  4. If codex_token fails, run openai_oauth to obtain RT, then sync RT to sub2api.

This script only orchestrates the local backend API; it does not contain the
browser/OAuth implementation itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_BASE = os.getenv("QUEUE_API_BASE", "http://127.0.0.1:8000")
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="优先创建 team Codex 令牌；失败则获取 OAuth RT；最后上传 sub2api",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  # 指定 account_id 和一个 team workspace
  scripts/team_token_or_rt_upload_sub.py --account-id 335 \\
    -w 7dc92548-255c-4e45-a570-ef25d793ab23

  # 按邮箱查找账号
  scripts/team_token_or_rt_upload_sub.py --email 3853122365@qq.com \\
    -w 7dc92548-255c-4e45-a570-ef25d793ab23

  # 多个 workspace：Codex token 会逐个创建并上传多个；RT fallback 选择第一个匹配 workspace
  scripts/team_token_or_rt_upload_sub.py --account-id 335 -w id1 -w id2 --json
""",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help=f"后台地址，默认 {DEFAULT_API_BASE}")
    parser.add_argument("--account-id", type=int, default=0, help="本地账号 ID")
    parser.add_argument("--email", default="", help="按邮箱查找本地账号 ID")
    parser.add_argument("--latest", action="store_true", help="未指定账号时使用最新 registered 账号")
    parser.add_argument("-w", "--workspace-id", action="append", default=[], help="team workspace UUID，可重复")
    parser.add_argument("--workspace-ids", default="", help="多个 workspace，支持逗号/空格/换行分隔")
    parser.add_argument("--workspace-file", default="", help="从文件读取 workspace id")
    parser.add_argument("--token-name", default="codex", help="Codex 令牌名前缀")
    parser.add_argument("--ttl", type=int, default=7_776_000, help="Codex 令牌 TTL 秒，默认 90 天")
    parser.add_argument("--scope", default="", help="额外/覆盖 Codex scope；不传使用 stage 默认")
    parser.add_argument("--interval-ms", type=int, default=1500)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff-ms", type=int, default=5000)
    parser.add_argument("--skip-codex", action="store_true", help="跳过 Codex token，直接 OAuth RT")
    parser.add_argument("--no-fallback-rt", action="store_true", help="Codex 失败时不 fallback 到 RT")
    parser.add_argument("--use-partial-codex", action="store_true", help="Codex 部分成功时也先上传成功部分，而不是 fallback RT")
    parser.add_argument("--reset-remote-status", action="store_true", default=True, help="同步后重置远端状态，默认开启")
    parser.add_argument("--no-reset-remote-status", dest="reset_remote_status", action="store_false")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="轮询间隔秒")
    parser.add_argument("--timeout", type=float, default=0, help="单个 job 等待超时秒；0 不限制")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    return parser.parse_args()


def http_json(api_base: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = api_base.rstrip("/") + path
    headers = {"accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} returned non-json: {raw[:1200]}") from exc


def split_items(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\s,;|]+", text or "") if x.strip()]


def collect_workspace_ids(args: argparse.Namespace) -> list[str]:
    items: list[str] = []
    items.extend(args.workspace_id or [])
    items.extend(split_items(args.workspace_ids))
    if args.workspace_file:
        items.extend(split_items(Path(args.workspace_file).read_text(encoding="utf-8")))
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()
        if "__" in value:
            value = value.split("__", 1)[0].strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    if not out:
        raise SystemExit("必须指定至少一个 workspace id：-w/--workspace-ids/--workspace-file")
    return out


def resolve_account_id(api_base: str, args: argparse.Namespace) -> int:
    if args.account_id:
        return int(args.account_id)
    accounts = http_json(api_base, "GET", "/api/accounts?limit=500")
    if not isinstance(accounts, list):
        raise RuntimeError(f"/api/accounts 返回异常: {accounts}")
    if args.email:
        target = args.email.strip().lower()
        for account in accounts:
            if str(account.get("email") or "").strip().lower() == target:
                return int(account.get("id") or 0)
        raise RuntimeError(f"未找到邮箱对应账号: {args.email}")
    if args.latest:
        for account in accounts:
            if str(account.get("status") or "").lower() == "registered":
                return int(account.get("id") or 0)
        raise RuntimeError("未找到 latest registered 账号")
    raise SystemExit("必须指定 --account-id 或 --email；或使用 --latest")


def enqueue_job(api_base: str, job_type: str, account_id: int, payload: dict[str, Any]) -> int:
    body = {"type": job_type, "account_id": account_id, "input": {"account_id": account_id, **payload}}
    resp = http_json(api_base, "POST", "/api/jobs", body)
    job_id = int(resp.get("job_id") or 0)
    if not job_id:
        raise RuntimeError(f"创建 job 失败: {resp}")
    return job_id


def wait_job(api_base: str, job_id: int, *, poll_interval: float, timeout: float, quiet_json: bool) -> dict[str, Any]:
    started = time.time()
    last = ""
    while True:
        job = http_json(api_base, "GET", f"/api/jobs/{job_id}")
        status = str(job.get("status") or "")
        line = (
            f"job={job_id} type={job.get('type')} status={status} "
            f"attempt={job.get('attempt')}/{job.get('max_attempts')} account={job.get('account_id') or '-'}"
        )
        if not quiet_json and line != last:
            print(line, flush=True)
            last = line
        if status in TERMINAL:
            return job
        if timeout and time.time() - started > timeout:
            raise TimeoutError(f"等待 job {job_id} 超时: {line}")
        time.sleep(max(1.0, poll_interval))


def print_job_tail(api_base: str, job_id: int, *, quiet_json: bool, limit: int = 10) -> None:
    if quiet_json:
        return
    try:
        events = http_json(api_base, "GET", f"/api/jobs/{job_id}/events?limit={limit}")
    except Exception:
        return
    if isinstance(events, list) and events:
        print(f"job {job_id} events tail:")
        for event in events[-limit:]:
            print(f"  [{event.get('level')}] {event.get('message')}")


def run_codex(api_base: str, account_id: int, workspace_ids: list[str], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload: dict[str, Any] = {
        "workspace_ids": workspace_ids,
        "token_name": args.token_name,
        "ttl": args.ttl,
        "interval_ms": args.interval_ms,
        "max_retries": args.max_retries,
        "retry_backoff_ms": args.retry_backoff_ms,
        "upload_multiple": True,
    }
    if args.scope:
        payload["scope"] = args.scope
    job_id = enqueue_job(api_base, "codex_token", account_id, payload)
    if not args.json:
        print(f"已启动 codex_token job={job_id}")
    job = wait_job(api_base, job_id, poll_interval=args.poll_interval, timeout=args.timeout, quiet_json=args.json)
    print_job_tail(api_base, job_id, quiet_json=args.json)
    result = job.get("result") or {}
    sessions = result.get("workspace_sessions") if isinstance(result.get("workspace_sessions"), list) else []
    if job.get("status") == "succeeded" and sessions:
        sync_payload = {
            "workspace_sessions": sessions,
            "upload_multiple": True,
            "force_new": len(sessions) > 1,
            "reset_remote_status": bool(args.reset_remote_status),
        }
        sync_id = enqueue_job(api_base, "sub2api_sync", account_id, sync_payload)
        if not args.json:
            print(f"Codex 成功，已启动 sub2api_sync job={sync_id}")
        sync_job = wait_job(api_base, sync_id, poll_interval=args.poll_interval, timeout=args.timeout, quiet_json=args.json)
        print_job_tail(api_base, sync_id, quiet_json=args.json)
        return job, sync_job
    if args.use_partial_codex and sessions:
        sync_id = enqueue_job(api_base, "sub2api_sync", account_id, {
            "workspace_sessions": sessions,
            "upload_multiple": True,
            "force_new": len(sessions) > 1,
            "reset_remote_status": bool(args.reset_remote_status),
        })
        if not args.json:
            print(f"Codex 部分成功，已上传成功部分 sub2api_sync job={sync_id}")
        sync_job = wait_job(api_base, sync_id, poll_interval=args.poll_interval, timeout=args.timeout, quiet_json=args.json)
        return job, sync_job
    return job, None


def run_oauth_rt(api_base: str, account_id: int, workspace_ids: list[str], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        # openai_oauth 会把 extra_config 合并进 OAuth 协议；当前代码会优先选择这里的 workspace。
        "extra_config": {
            "workspace_ids": workspace_ids,
            "workspace_id": workspace_ids[0] if workspace_ids else "",
        }
    }
    job_id = enqueue_job(api_base, "openai_oauth", account_id, payload)
    if not args.json:
        print(f"已启动 openai_oauth job={job_id}")
    job = wait_job(api_base, job_id, poll_interval=args.poll_interval, timeout=args.timeout, quiet_json=args.json)
    print_job_tail(api_base, job_id, quiet_json=args.json)
    if job.get("status") != "succeeded":
        raise RuntimeError(f"openai_oauth 失败: {job.get('error') or job.get('result')}")
    result = job.get("result") or {}
    refresh_token_id = int(result.get("refresh_token_id") or 0)
    if not refresh_token_id:
        raise RuntimeError(f"openai_oauth 成功但没有 refresh_token_id: {result}")
    resp = http_json(api_base, "POST", f"/api/refresh-tokens/{refresh_token_id}/sync")
    sync_id = int(resp.get("job_id") or 0)
    if not sync_id:
        raise RuntimeError(f"创建 RT sub2api sync 失败: {resp}")
    if not args.json:
        print(f"RT 获取成功 refresh_token_id={refresh_token_id}，已启动 sub2api_sync job={sync_id}")
    sync_job = wait_job(api_base, sync_id, poll_interval=args.poll_interval, timeout=args.timeout, quiet_json=args.json)
    print_job_tail(api_base, sync_id, quiet_json=args.json)
    return job, sync_job


def main() -> int:
    args = parse_args()
    workspace_ids = collect_workspace_ids(args)
    account_id = resolve_account_id(args.api_base, args)
    if not args.json:
        print(f"account_id={account_id} workspace_ids={workspace_ids}")

    output: dict[str, Any] = {
        "account_id": account_id,
        "workspace_ids": workspace_ids,
        "path": "",
        "codex_job": None,
        "oauth_job": None,
        "sub2api_job": None,
    }

    codex_job = None
    codex_sync = None
    if not args.skip_codex:
        codex_job, codex_sync = run_codex(args.api_base, account_id, workspace_ids, args)
        output["codex_job"] = compact_job(codex_job)
        if codex_sync and codex_sync.get("status") == "succeeded":
            output["path"] = "codex_token"
            output["sub2api_job"] = compact_job(codex_sync)
            print_output(output, args.json)
            return 0

    if args.no_fallback_rt:
        output["path"] = "codex_token_failed_no_fallback"
        if codex_sync:
            output["sub2api_job"] = compact_job(codex_sync)
        print_output(output, args.json)
        return 1

    if not args.json:
        print("Codex token 未成功上传，fallback 到 OAuth RT...")
    oauth_job, rt_sync = run_oauth_rt(args.api_base, account_id, workspace_ids, args)
    output["path"] = "oauth_rt"
    output["oauth_job"] = compact_job(oauth_job)
    output["sub2api_job"] = compact_job(rt_sync)
    print_output(output, args.json)
    return 0 if rt_sync.get("status") == "succeeded" else 1


def compact_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    result = job.get("result") or {}
    return {
        "id": job.get("id"),
        "type": job.get("type"),
        "status": job.get("status"),
        "account_id": job.get("account_id"),
        "error": job.get("error") or "",
        "result": result,
    }


def print_output(output: dict[str, Any], quiet_json: bool) -> None:
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        raise SystemExit(130)
