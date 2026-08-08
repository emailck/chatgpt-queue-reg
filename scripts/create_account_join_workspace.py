#!/usr/bin/env python3
"""Create ChatGPT account pipeline and request joining workspace(s).

This is a thin CLI wrapper around the local queue backend:

    POST /api/pipelines
      {"preset": "register_workspace_request", "workspace_ids": [...], ...}

Optionally it can import one or more email-pool lines before starting.
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


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
DEFAULT_API_BASE = os.getenv("QUEUE_API_BASE", "http://127.0.0.1:8000")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="触发注册账号并申请加入指定 workspace 的流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  # 使用邮箱池里下一个可用邮箱，注册 1 个账号并申请加入空间
  scripts/create_account_join_workspace.py -w 7dc92548-255c-4e45-a570-ef25d793ab23 --wait

  # 指定一个邮箱
  scripts/create_account_join_workspace.py -e user@example.com -w 7dc92548-255c-4e45-a570-ef25d793ab23 --wait

  # 先导入邮箱行，再启动
  scripts/create_account_join_workspace.py \\
    --email-line 'user@qq.com----http://example/messages?email=user%40qq.com&api_key=xxx' \\
    -w 7dc92548-255c-4e45-a570-ef25d793ab23 --wait

  # 多空间
  scripts/create_account_join_workspace.py -e user@example.com \\
    -w 11111111-1111-1111-1111-111111111111 \\
    -w 22222222-2222-2222-2222-222222222222 --wait
""",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help=f"后台地址，默认 {DEFAULT_API_BASE}")
    parser.add_argument("-w", "--workspace-id", action="append", default=[], help="workspace UUID，可重复")
    parser.add_argument("--workspace-ids", default="", help="多个 workspace，支持逗号/空格/换行分隔")
    parser.add_argument("--workspace-file", help="从文件读取 workspace id")
    parser.add_argument("-e", "--email", action="append", default=[], help="指定邮箱，可重复；不传则使用邮箱池")
    parser.add_argument("--email-line", action="append", default=[], help="导入并使用一行邮箱配置：email----url/密码等，可重复")
    parser.add_argument("--email-file", help="导入并使用文件里的邮箱配置行")
    parser.add_argument("--alias-split-count", type=int, default=0, help="邮箱导入时生成别名数量")
    parser.add_argument("--no-alias-original", action="store_true", help="邮箱别名导入时不包含原邮箱")
    parser.add_argument("--count", type=int, default=1, help="未指定 email 时创建数量，默认 1")
    parser.add_argument("--route", choices=["request", "accept"], default="request", help="加入方式，默认 request")
    parser.add_argument("--interval-ms", type=int, default=None, help="多个 workspace 间隔")
    parser.add_argument("--max-retries", type=int, default=None, help="workspace_join 重试次数")
    parser.add_argument("--retry-backoff-ms", type=int, default=None, help="workspace_join 重试退避")
    parser.add_argument("--switch-after-join", action="store_true", help="加入后切换 workspace；默认不切换")
    parser.add_argument("--wait", action="store_true", help="等待流水线结束并输出结果")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="等待轮询间隔秒，默认 5")
    parser.add_argument("--timeout", type=float, default=0, help="等待超时秒；0 表示不限制")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    return parser.parse_args()


def http_json(api_base: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = api_base.rstrip("/") + path
    data = None
    headers = {"accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} returned non-json: {raw[:1000]}") from exc


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
    for raw in items:
        value = raw.strip()
        # 用户有时会传 "uuid__tag"，加入空间只需要 uuid 部分。
        if "__" in value:
            value = value.split("__", 1)[0].strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    if not out:
        raise SystemExit("必须通过 -w/--workspace-ids/--workspace-file 指定至少一个 workspace id")
    return out


def email_from_line(line: str) -> str:
    first = (line or "").strip().split("----", 1)[0].strip()
    return first if "@" in first else ""


def collect_email_lines(args: argparse.Namespace) -> list[str]:
    lines = [x.strip() for x in (args.email_line or []) if x.strip()]
    if args.email_file:
        for raw in Path(args.email_file).read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#"):
                lines.append(raw)
    return lines


def import_email_lines(api_base: str, args: argparse.Namespace, lines: list[str]) -> list[str]:
    if not lines:
        return []
    body = {
        "content": "\n".join(lines),
        "enabled": True,
        "alias_split_enabled": bool(args.alias_split_count),
        "alias_split_count": int(args.alias_split_count or 5),
        "alias_include_original": not bool(args.no_alias_original),
    }
    resp = http_json(api_base, "POST", "/api/email/import", body)
    emails = [email_from_line(line) for line in lines]
    emails = [e for e in emails if e]
    if not args.json:
        print("邮箱导入完成:", json.dumps(resp, ensure_ascii=False)[:1200])
    return emails


def create_pipeline(api_base: str, *, email: str | None, count: int, workspace_ids: list[str], args: argparse.Namespace) -> list[int]:
    body: dict[str, Any] = {
        "preset": "register_workspace_request",
        "workspace_ids": workspace_ids,
        "route": args.route,
        "switch_after_join": bool(args.switch_after_join),
        "refresh_before_request": False,
    }
    if email:
        body["email"] = email
        body["count"] = 1
    else:
        body["count"] = max(1, int(count or 1))
    for key in ("interval_ms", "max_retries", "retry_backoff_ms"):
        value = getattr(args, key)
        if value is not None:
            body[key] = value
    resp = http_json(api_base, "POST", "/api/pipelines", body)
    pids = [int(x) for x in resp.get("pipeline_ids", [])]
    if not pids:
        raise RuntimeError(f"创建流水线成功但未返回 pipeline_ids: {resp}")
    if not args.json:
        print(f"已创建 pipeline: {pids} email={email or '<pool>'} workspace_count={len(workspace_ids)}")
    return pids


def summarize_pipeline(doc: dict[str, Any]) -> dict[str, Any]:
    pipe = doc.get("pipeline") or {}
    jobs = doc.get("jobs") or []
    return {
        "id": pipe.get("id"),
        "status": pipe.get("status"),
        "current_stage": pipe.get("current_stage"),
        "progress": f"{pipe.get('completed_steps')}/{pipe.get('total_steps')}",
        "account_id": pipe.get("account_id"),
        "error": pipe.get("error") or "",
        "jobs": [
            {
                "id": j.get("id"),
                "type": j.get("type"),
                "status": j.get("status"),
                "account_id": j.get("account_id"),
                "error": j.get("error") or "",
                "result": j.get("result") or {},
            }
            for j in jobs
        ],
    }


def wait_pipeline(api_base: str, pipeline_id: int, *, poll_interval: float, timeout: float, quiet_json: bool) -> dict[str, Any]:
    started = time.time()
    last_line = ""
    while True:
        doc = http_json(api_base, "GET", f"/api/pipelines/{pipeline_id}")
        summary = summarize_pipeline(doc)
        status = str(summary["status"] or "")
        line = (
            f"pipeline={pipeline_id} status={status} stage={summary['current_stage'] or '-'} "
            f"progress={summary['progress']} account={summary['account_id'] or '-'}"
        )
        if not quiet_json and line != last_line:
            print(line)
            last_line = line
        if status in TERMINAL:
            return summary
        if timeout and time.time() - started > timeout:
            raise TimeoutError(f"等待 pipeline {pipeline_id} 超时: {line}")
        time.sleep(max(1.0, poll_interval))


def main() -> int:
    args = parse_args()
    workspace_ids = collect_workspace_ids(args)
    imported_emails = import_email_lines(args.api_base, args, collect_email_lines(args))
    emails = list(dict.fromkeys([*(args.email or []), *imported_emails]))

    all_pids: list[int] = []
    if emails:
        for email in emails:
            all_pids.extend(create_pipeline(args.api_base, email=email, count=1, workspace_ids=workspace_ids, args=args))
    else:
        all_pids.extend(create_pipeline(args.api_base, email=None, count=args.count, workspace_ids=workspace_ids, args=args))

    result: dict[str, Any] = {"pipeline_ids": all_pids}
    exit_code = 0
    if args.wait:
        summaries = []
        for pid in all_pids:
            summary = wait_pipeline(
                args.api_base,
                pid,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                quiet_json=args.json,
            )
            summaries.append(summary)
            if summary.get("status") != "succeeded":
                exit_code = 1
        result["pipelines"] = summaries

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("完成:", json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        raise SystemExit(130)
