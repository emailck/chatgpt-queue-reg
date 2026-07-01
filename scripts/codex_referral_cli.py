#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex referral command-line workflow.

利用当前项目已有 stage/queue 实现一个类似 Codex_team_auto 的命令行流程：

  多母号全部邀请完成 -> 再统一创建子号 sso_oauth -> active 激活 pipeline

示例：
  # 3 个母号，每个最多邀请 5 个；邀请全部结束后自动激活
  .venv/bin/python scripts/codex_referral_cli.py \
    --inviters jr3q7pganb@aicoco.xyz,foo@aicoco.xyz,bar@aicoco.xyz \
    --per-inviter 5 --wait

  # 从文件读取母号（一行一个），只邀请不激活
  .venv/bin/python scripts/codex_referral_cli.py \
    --inviters-file inviters.txt --no-activate --wait

  # dry-run：只查额度/生成邮箱，不发邀请、不激活
  .venv/bin/python scripts/codex_referral_cli.py \
    --inviters-file inviters.txt --dry-run --wait
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 允许从 repo root 或任意目录执行脚本。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlmodel import Session, select  # noqa: E402

from backend.core.constants import JOB_TERMINAL_STATUSES  # noqa: E402
from backend.core.db import engine, init_db  # noqa: E402
from backend.core.json_utils import json_loads  # noqa: E402
from backend.core.pipeline import create_pipeline  # noqa: E402
from backend.core.queue import get_pool, recover_orphan_jobs  # noqa: E402
from backend.models.job import Job  # noqa: E402
from backend.models.pipeline import Pipeline  # noqa: E402

# 触发 stage 注册。
import backend.stages  # noqa: E402,F401
import backend.core.pools  # noqa: E402,F401


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Codex 多母号邀请 -> 统一激活 命令行工作流",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--inviters", help="母号列表，逗号/换行/分号分隔；可以是邮箱或账号/资源 ID")
    src.add_argument("--inviters-file", help="母号列表文件，一行一个；可以是邮箱或账号/资源 ID")

    p.add_argument("--per-inviter", type=int, default=5, help="每个母号最多邀请数量，最大 5")
    p.add_argument("--invite-concurrency", type=int, default=0, help="邀请阶段并发母号数；0 表示默认 min(5, 母号数)")
    p.add_argument("--prefix-len", type=int, default=20, help="随机子号邮箱前缀长度")
    p.add_argument("--domain", default="", help="指定子号邮箱域名；留空则使用母号同域名")
    p.add_argument("--source-type", default="auto", help="母号解析来源：auto/chatgpt_account/access_token_account_email/email_account_email 等")
    p.add_argument("--proxy-url", default="", help="本次流程使用的代理 URL")
    p.add_argument("--dry-run", action="store_true", help="只生成/预检，不实际发送邀请，也不激活")
    p.add_argument("--no-eligibility", action="store_true", help="不预先查询剩余额度，直接尝试邀请")
    p.add_argument("--no-activate", action="store_true", help="只邀请，不创建后续激活 pipeline")
    p.add_argument("--wait", action="store_true", help="等待批量邀请以及后续激活 pipeline 全部结束")
    p.add_argument("--timeout", type=int, default=3600, help="--wait 最大等待秒数")
    p.add_argument("--poll", type=float, default=2.0, help="--wait 轮询间隔秒数")
    p.add_argument("--local-worker", action="store_true", help="在当前 CLI 进程启动本地 worker；如果 systemd 服务已运行，一般不需要")
    p.add_argument("--json-out", default="", help="把最终摘要写入 JSON 文件")
    return p.parse_args()


def split_many(value: str) -> list[str]:
    text = str(value or "")
    for sep in ["\r\n", "\r", "\n", ";", "，"]:
        text = text.replace(sep, ",")
    out: list[str] = []
    seen: set[str] = set()
    for part in text.split(","):
        item = part.strip()
        if not item or item.startswith("#"):
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def read_inviters(args: argparse.Namespace) -> list[str]:
    if args.inviters_file:
        raw = Path(args.inviters_file).read_text(encoding="utf-8")
    else:
        raw = args.inviters or ""
    inviters = split_many(raw)
    if not inviters:
        raise SystemExit("[!] 母号列表为空")
    return inviters


def compact_pipeline(p: Pipeline | None) -> dict[str, Any]:
    if p is None:
        return {}
    result = json_loads(p.result_json, fallback={}) or {}
    return {
        "id": int(p.id or 0),
        "preset": p.preset,
        "status": p.status,
        "current_stage": p.current_stage,
        "completed_steps": p.completed_steps,
        "total_steps": p.total_steps,
        "error": p.error,
        "result": result,
    }


def latest_job_for_pipeline(session: Session, pipeline_id: int, job_type: str = "") -> Job | None:
    stmt = select(Job).where(Job.pipeline_id == pipeline_id).order_by(Job.id.desc())
    if job_type:
        stmt = stmt.where(Job.type == job_type)
    return session.exec(stmt).first()


def collect_child_pipeline_ids(batch_pipeline_id: int) -> list[int]:
    with Session(engine) as s:
        p = s.get(Pipeline, batch_pipeline_id)
        result = json_loads(p.result_json, fallback={}) if p else {}
        last = result.get("last_job_result") if isinstance(result, dict) else {}
        ids = []
        if isinstance(last, dict):
            ids = last.get("activation_pipeline_ids") or []
        if not ids:
            j = latest_job_for_pipeline(s, batch_pipeline_id, "codex_batch_invite")
            jr = json_loads(j.result_json, fallback={}) if j else {}
            ids = jr.get("activation_pipeline_ids") or [] if isinstance(jr, dict) else []
        return [int(x) for x in ids if str(x).isdigit() or isinstance(x, int)]


def wait_pipeline(pipeline_id: int, *, timeout: int, poll: float, label: str) -> dict[str, Any]:
    start = time.time()
    last_status = ""
    while True:
        with Session(engine) as s:
            p = s.get(Pipeline, pipeline_id)
            data = compact_pipeline(p)
        status = data.get("status") or "missing"
        if status != last_status:
            print(f"[*] {label} pipeline #{pipeline_id}: {status} stage={data.get('current_stage') or ''}", flush=True)
            last_status = str(status)
        if status in JOB_TERMINAL_STATUSES or status == "missing":
            return data
        if time.time() - start > timeout:
            raise TimeoutError(f"等待 {label} pipeline #{pipeline_id} 超时")
        time.sleep(max(0.2, poll))


def wait_children(batch_pipeline_id: int, *, timeout: int, poll: float) -> list[dict[str, Any]]:
    start = time.time()
    child_ids: list[int] = []
    while time.time() - start <= timeout:
        child_ids = collect_child_pipeline_ids(batch_pipeline_id)
        if child_ids:
            break
        # no activation may be expected if no-activate/dry-run/no invited emails; caller handles final result too.
        time.sleep(max(0.2, poll))
    if not child_ids:
        return []

    print(f"[*] activation child pipelines: {child_ids}", flush=True)
    done: set[int] = set()
    last: dict[int, str] = {}
    while True:
        rows: list[dict[str, Any]] = []
        with Session(engine) as s:
            for pid in child_ids:
                data = compact_pipeline(s.get(Pipeline, pid))
                rows.append(data)
        for row in rows:
            pid = int(row.get("id") or 0)
            status = str(row.get("status") or "")
            if last.get(pid) != status:
                print(f"[*] active child #{pid}: {status} stage={row.get('current_stage') or ''}", flush=True)
                last[pid] = status
            if status in JOB_TERMINAL_STATUSES:
                done.add(pid)
        if len(done) == len(child_ids):
            return rows
        if time.time() - start > timeout:
            raise TimeoutError("等待 activation child pipelines 超时")
        time.sleep(max(0.2, poll))


def summarize(batch: dict[str, Any], children: list[dict[str, Any]]) -> dict[str, Any]:
    last = (batch.get("result") or {}).get("last_job_result") or {}
    invited = last.get("invited_emails") or [] if isinstance(last, dict) else []
    failed = last.get("failed") or [] if isinstance(last, dict) else []
    child_summary = [
        {
            "pipeline_id": c.get("id"),
            "status": c.get("status"),
            "email": (((c.get("result") or {}).get("last_job_result") or {}).get("email") or ((c.get("result") or {}).get("last_job_result") or {}).get("sso_email")),
            "error": c.get("error"),
        }
        for c in children
    ]
    return {
        "batch_pipeline_id": batch.get("id"),
        "batch_status": batch.get("status"),
        "invited_count": len(invited),
        "invited_emails": invited,
        "failed_inviters": failed,
        "activation_pipeline_ids": [c.get("id") for c in children],
        "activation": child_summary,
        "batch_error": batch.get("error"),
    }


def main() -> int:
    args = parse_args()
    inviters = read_inviters(args)
    per_inviter = max(1, min(5, int(args.per_inviter or 5)))
    prefix_len = max(3, min(64, int(args.prefix_len or 20)))
    invite_concurrency = max(0, min(50, int(args.invite_concurrency or 0)))

    init_db()
    interrupted = recover_orphan_jobs() if args.local_worker else 0
    pool = None
    if args.local_worker:
        pool = get_pool()
        pool.start()
        if interrupted:
            print(f"[*] recovered {interrupted} orphan running job(s)", flush=True)

    payload: dict[str, Any] = {
        "inviter_list": "\n".join(inviters),
        "source_type": args.source_type,
        "invite_count_per_inviter": per_inviter,
        "prefix_len": prefix_len,
        "dry_run": bool(args.dry_run),
        "activate_after_invite": (not args.no_activate),
        "check_eligibility": (not args.no_eligibility),
    }
    if invite_concurrency:
        payload["invite_concurrency"] = invite_concurrency
    if args.domain:
        payload["domain"] = args.domain.strip().lstrip("@")
    if args.proxy_url:
        payload["proxy_url"] = args.proxy_url.strip()

    print("=" * 72)
    print("Codex referral CLI")
    print(f"母号数: {len(inviters)}")
    print(f"每母号邀请: {per_inviter}")
    print(f"邀请并发: {invite_concurrency or '默认'}")
    print(f"激活: {not args.no_activate and not args.dry_run}")
    print(f"dry-run: {args.dry_run}")
    print(f"local-worker: {args.local_worker}")
    print("=" * 72)

    pipeline_id = create_pipeline(
        stages=["codex_batch_invite"],
        preset="codex_cli_referral_flow",
        stage_inputs={"codex_batch_invite": payload},
        resource_bindings={},
        proxy_url=args.proxy_url or "",
        request_payload=payload,
    )
    print(f"[+] created batch pipeline #{pipeline_id}")

    children: list[dict[str, Any]] = []
    if args.wait:
        batch = wait_pipeline(pipeline_id, timeout=args.timeout, poll=args.poll, label="batch invite")
        if batch.get("status") == "succeeded" and not args.no_activate and not args.dry_run:
            children = wait_children(pipeline_id, timeout=args.timeout, poll=args.poll)
        summary = summarize(batch, children)
        print("\n" + "=" * 72)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[+] summary written: {args.json_out}")
        return 0 if summary.get("batch_status") == "succeeded" else 1

    print("[*] 未指定 --wait；任务已入队。可以在服务/UI 或数据库里查看进度。")
    print("    如果 systemd 服务未运行，请加 --local-worker --wait 让 CLI 自己执行。")
    if pool is not None:
        pool.stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[!] interrupted", file=sys.stderr)
        raise SystemExit(130)
