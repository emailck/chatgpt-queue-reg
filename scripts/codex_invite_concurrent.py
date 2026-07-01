#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concurrent Codex inviter CLI.

只做“并发邀请”：
- 从当前项目数据库账号池解析母号（邮箱或账号 ID）
- 每个母号一次 POST 数组邮箱到 /backend-api/wham/referrals/invite
- 多个母号之间 ThreadPoolExecutor 并发
- 不创建 pipeline，不做 SSO，不做 active，不依赖前端

示例：
  .venv/bin/python scripts/codex_invite_concurrent.py \
    --inviters 'a@x.com,b@x.com,c@x.com' --per-inviter 5 --concurrency 3 --out invite_results.json

  .venv/bin/python scripts/codex_invite_concurrent.py \
    --inviters-file inviters.txt --per-inviter 5 --dry-run --out dry_run.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend.core.db import init_db  # noqa: E402
from backend.core.settings import settings  # noqa: E402
from backend.stages import codex_invitation as inv  # noqa: E402


def log(msg: str, symbol: str = "*") -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{symbol}] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="并发 Codex 邀请：多母号并发，每个母号一次数组邀请",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--inviters", help="母号列表，逗号/分号/换行分隔；可填邮箱或 chatgpt_accounts.id")
    src.add_argument("--inviters-file", help="母号列表文件，一行一个；可填邮箱或 chatgpt_accounts.id")
    p.add_argument("--per-inviter", type=int, default=5, help="每个母号邀请数量，最大 5")
    p.add_argument("--concurrency", type=int, default=5, help="并发母号数")
    p.add_argument("--domain", default="", help="受邀邮箱域名；留空则使用母号同域名")
    p.add_argument("--prefix-len", type=int, default=20, help="随机邮箱前缀长度")
    p.add_argument("--source-type", default="auto", help="母号来源：auto/chatgpt_account/access_token_account/email_account")
    p.add_argument("--proxy-url", default="", help="HTTP/SOCKS 代理 URL")
    p.add_argument("--timeout", type=int, default=30, help="邀请接口超时秒数")
    p.add_argument("--barrier-send", action="store_true", help="所有线程准备好后再同时释放 POST 邀请请求")
    p.add_argument("--barrier-timeout", type=float, default=20.0, help="等待所有线程 ready 的最长秒数；超时则已就绪线程直接发送")
    p.add_argument("--dry-run", action="store_true", help="只解析母号、查额度、生成邮箱，不实际发送")
    p.add_argument("--no-eligibility", action="store_true", help="不查 eligibility_rules，直接发邀请")
    p.add_argument("--insecure", action="store_true", help="关闭 TLS 校验")
    p.add_argument("--out", default="", help="结果 JSON 输出路径")
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
    raw = Path(args.inviters_file).read_text(encoding="utf-8") if args.inviters_file else str(args.inviters or "")
    items = split_many(raw)
    if not items:
        raise SystemExit("[!] 母号列表为空")
    return items


def workpool_config(prefix: str) -> dict[str, Any]:
    return {key[len(prefix):]: value for key, value in settings.get_all().items() if key.startswith(prefix)}


def inviter_payload(inviter: str, args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_type": args.source_type,
        "invite_count": max(1, min(5, int(args.per_inviter or 5))),
        "prefix_len": max(3, min(64, int(args.prefix_len or 20))),
        "dry_run": bool(args.dry_run),
        "check_eligibility": not bool(args.no_eligibility),
        "insecure": bool(args.insecure),
    }
    if args.domain:
        payload["domain"] = args.domain.strip().lstrip("@")
    if args.proxy_url:
        payload["proxy_url"] = args.proxy_url.strip()
    if "@" in inviter:
        payload["inviter_email"] = inviter.strip()
    else:
        payload["inviter_account_id"] = inviter.strip()
    return payload


def process_inviter(index: int, total: int, inviter: str, args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "index": index,
        "total": total,
        "inviter": inviter,
        "ok": False,
        "sent": False,
        "dry_run": bool(args.dry_run),
        "emails": [],
        "invites": [],
        "sent_count": 0,
        "error": "",
        "elapsed_seconds": 0,
    }
    try:
        payload = inviter_payload(inviter, args)
        config = {**settings.get_all(), **workpool_config("workpool.codex_invitation.")}
        source = inv._resolve_source(payload, config)  # reuse current project account-pool resolver
        domain = str(payload.get("domain") or "").strip().lstrip("@") or inv._domain_from_email(source.email)
        if not domain:
            raise RuntimeError(f"cannot infer domain from source email={source.email!r}")

        count = int(payload["invite_count"])
        prefix_len = int(payload["prefix_len"])
        recipients = inv._random_emails(domain, count, prefix_len)
        proxy_url = str(payload.get("proxy_url") or config.get("proxy_url") or "").strip()
        verify_tls = not bool(args.insecure)
        referral_key = str(config.get("referral_key") or inv.REFERRAL_KEY).strip() or inv.REFERRAL_KEY

        result.update({
            "source_type": source.source_type,
            "source_id": source.source_id,
            "source_email": source.email,
            "chatgpt_account_id": source.chatgpt_account_id,
            "domain": domain,
            "emails": recipients,
        })

        session_type, session = inv._build_session(proxy_url)
        result["session_type"] = session_type

        remaining = None
        if not args.no_eligibility:
            remaining = inv._check_eligibility(session, source.access_token, source.chatgpt_account_id, referral_key, verify_tls=verify_tls)
            result["remaining_invites"] = remaining
            if remaining is not None:
                if remaining <= 0:
                    raise RuntimeError("source account has no remaining Codex referral invite quota")
                if len(recipients) > remaining:
                    recipients = recipients[:remaining]
                    result["emails"] = recipients

        if args.dry_run:
            # In dry-run, still exercise barrier synchronization when requested,
            # but never send the invite POST.
            _barrier_wait_before_send(args, result)
            result.update({"ok": True, "sent": False, "sent_count": len(recipients), "status_code": 0})
            return result

        _barrier_wait_before_send(args, result)

        resp = session.post(
            inv.INVITE_URL,
            headers=inv._headers(source.access_token, source.chatgpt_account_id, is_json=True),
            json={"referral_key": referral_key, "emails": recipients},
            timeout=max(5, int(args.timeout or 30)),
            verify=verify_tls,
        )
        result["status_code"] = resp.status_code
        try:
            response_payload = resp.json()
        except Exception:
            response_payload = {"text": (resp.text or "")[:1000]}
        result["response"] = response_payload
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {(resp.text or '')[:300]}")
        invites = response_payload.get("invites", []) if isinstance(response_payload, dict) else []
        if not isinstance(invites, list):
            invites = []
        result.update({
            "ok": bool(invites),
            "sent": bool(invites),
            "invites": invites,
            "sent_count": len(invites),
            "partial": len(invites) != len(recipients),
        })
        if not invites:
            result["error"] = f"HTTP 200 but invites empty; requested={len(recipients)}"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
    finally:
        result["elapsed_seconds"] = round(time.time() - started, 3)



def _barrier_wait_before_send(args: argparse.Namespace, result: dict[str, Any]) -> None:
    """Optional best-effort synchronized release before the invite POST.

    This intentionally uses an Event rather than threading.Barrier: if some
    inviters fail during token resolution/eligibility, ready workers proceed
    after --barrier-timeout instead of deadlocking.
    """
    if not bool(getattr(args, "barrier_send", False)):
        return
    event = getattr(args, "barrier_event", None)
    lock = getattr(args, "barrier_lock", None)
    state = getattr(args, "barrier_state", None)
    if event is None or lock is None or not isinstance(state, dict):
        return
    with lock:
        state["ready"] = int(state.get("ready") or 0) + 1
        ready = int(state["ready"])
        target = int(state.get("target") or 0)
        result["barrier_ready_index"] = ready
        result["barrier_target"] = target
        if target and ready >= target:
            event.set()
    timeout = float(getattr(args, "barrier_timeout", 20.0) or 20.0)
    released = event.wait(max(0.1, timeout))
    if not released:
        with lock:
            state["timed_out"] = True
        event.set()
    result["barrier_released"] = True
    result["barrier_timeout_used"] = not released

def main() -> int:
    args = parse_args()
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    init_db()
    inviters = read_inviters(args)
    max_workers = max(1, min(int(args.concurrency or 1), len(inviters)))

    print("=" * 72)
    print("Codex concurrent invite")
    print(f"母号数: {len(inviters)}")
    print(f"并发数: {max_workers}")
    print(f"每母号邀请: {max(1, min(5, int(args.per_inviter or 5)))}")
    print(f"dry-run: {bool(args.dry_run)}")
    print(f"查额度: {not bool(args.no_eligibility)}")
    print(f"barrier-send: {bool(args.barrier_send)} timeout={float(args.barrier_timeout or 20.0)}s")
    print("=" * 72)

    if bool(args.barrier_send):
        args.barrier_event = Event()
        args.barrier_lock = Lock()
        args.barrier_state = {"ready": 0, "target": max_workers, "timed_out": False}

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="codex-invite") as pool:
        futures = {
            pool.submit(process_inviter, i, len(inviters), inviter, args): inviter
            for i, inviter in enumerate(inviters, 1)
        }
        for fut in as_completed(futures):
            inviter = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"inviter": inviter, "ok": False, "sent": False, "emails": [], "invites": [], "sent_count": 0, "error": str(exc)}
            results.append(r)
            if r.get("ok"):
                label = "dry-run" if r.get("dry_run") else ("partial" if r.get("partial") else "sent")
                log(f"✓ {r.get('source_email') or inviter}: {r.get('sent_count')}/{len(r.get('emails') or [])} ({label})", "✓")
            else:
                log(f"✗ {r.get('source_email') or inviter}: {r.get('error') or 'failed'}", "!")

    # stable output order by input index
    results.sort(key=lambda x: int(x.get("index") or 0))
    summary = {
        "ok_inviters": sum(1 for r in results if r.get("ok")),
        "failed_inviters": sum(1 for r in results if not r.get("ok")),
        "total_inviters": len(results),
        "sent_emails": sum(int(r.get("sent_count") or 0) for r in results if not r.get("dry_run")),
        "planned_emails": sum(len(r.get("emails") or []) for r in results),
        "dry_run": bool(args.dry_run),
    }
    output = {"summary": summary, "results": results}

    print("\n" + "=" * 72)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"结果已写入: {out}", "+")
    return 0 if summary["ok_inviters"] > 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[!] 用户取消", file=sys.stderr)
        raise SystemExit(130)
