#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex full CLI flow: mother SSO -> concurrent invite -> invited SSO+active.

流程：
  1) 母号 SSO OAuth 注册/登录，支持生成指定数量母号邮箱
  2) 所有可用母号并发邀请，每个母号一次数组邀请 N 个邮箱
  3) 收集被邀请邮箱，再跑 sso_oauth -> active

并发：
  --general-concurrency  控制母号 SSO 和子号 SSO+active
  --invite-concurrency   只控制邀请阶段

代理：
  支持 host:port:user:pass 或 http://user:pass@host:port；每个任务随机分配一个代理。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import secrets
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlmodel import Session  # noqa: E402

from backend.core.constants import JOB_TERMINAL_STATUSES  # noqa: E402
from backend.core.db import engine, init_db  # noqa: E402
from backend.core.json_utils import json_loads  # noqa: E402
from backend.core.pipeline import create_pipeline  # noqa: E402
from backend.core.queue import get_pool, recover_orphan_jobs  # noqa: E402
from backend.core.settings import settings  # noqa: E402
from backend.models.pipeline import Pipeline  # noqa: E402

import backend.stages  # noqa: E402,F401
import backend.core.pools  # noqa: E402,F401

_INVITE_SCRIPT = ROOT / "scripts" / "codex_invite_concurrent.py"
_spec = importlib.util.spec_from_file_location("codex_invite_concurrent", _INVITE_SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import {_INVITE_SCRIPT}")
invite_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(invite_mod)

ALPHABET = string.ascii_lowercase + string.digits


def log(msg: str, symbol: str = "*") -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{symbol}] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="母号 SSO -> 并发邀请 -> 被邀请号 SSO+active",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--mothers", help="母号邮箱列表，逗号/分号/换行分隔")
    src.add_argument("--mothers-file", help="母号邮箱文件，一行一个")
    p.add_argument("--mother-count", type=int, default=0, help="自动生成母号数量；未提供 mothers 时使用")
    p.add_argument("--mother-domain", default="", help="自动生成母号邮箱域名，例如 aicoco.xyz")
    p.add_argument("--mother-prefix-len", type=int, default=10, help="自动生成母号邮箱前缀长度")

    p.add_argument("--per-mother", type=int, default=5, help="每个母号邀请数量，最大 5")
    p.add_argument("--general-concurrency", type=int, default=3, help="通用并发：母号 SSO、子号 SSO+active")
    p.add_argument("--invite-concurrency", type=int, default=10, help="邀请阶段并发母号数")
    p.add_argument("--prefix-len", type=int, default=20, help="随机子号邮箱前缀长度")
    p.add_argument("--domain", default="", help="受邀邮箱域名；留空用母号同域名")

    p.add_argument("--proxy-url", default="", help="固定代理 URL；没有代理池时使用")
    p.add_argument("--proxy-pool", default="", help="代理池字符串，换行/逗号分隔，格式 host:port:user:pass")
    p.add_argument("--proxy-pool-file", default="", help="代理池文件，一行一个 host:port:user:pass")
    p.add_argument("--proxy-scheme", default="http", choices=["http", "https", "socks5"], help="host:port:user:pass 转 URL 时使用的协议")

    p.add_argument("--source-type", default="auto", help="邀请母号解析来源")
    p.add_argument("--sso-invite-code", default="", help="传给 sso_oauth 的 OIDC/SSO invite code")
    p.add_argument("--sso-connection-id", default="", help="传给 sso_oauth 的 connection_id；留空自动检测")
    p.add_argument("--sso-provider", type=int, default=2, help="传给 sso_oauth 的 provider")
    p.add_argument("--skip-mother-sso", action="store_true", help="跳过母号 SSO，直接从账号池解析母号邀请")
    p.add_argument("--mother-sso-only", action="store_true", help="只测试/执行母号 SSO，成功后不邀请、不激活")
    p.add_argument("--skip-activation", action="store_true", help="只做到并发邀请，不激活被邀请邮箱")
    p.add_argument("--dry-run-invite", action="store_true", help="邀请阶段 dry-run；不会真正发送，也不会激活")
    p.add_argument("--no-eligibility", action="store_true", help="邀请阶段不查额度")
    p.add_argument("--insecure", action="store_true", help="关闭 TLS 校验")
    p.add_argument("--barrier-send", action="store_true", help="邀请阶段所有母号 ready 后再同时释放 POST 请求")
    p.add_argument("--barrier-timeout", type=float, default=20.0, help="邀请阶段 barrier 等待秒数")
    p.add_argument("--local-worker", action="store_true", help="当前 CLI 进程启动 worker；systemd 已运行时通常不需要")
    p.add_argument("--wait-timeout", type=int, default=3600, help="单个 pipeline 最大等待秒数")
    p.add_argument("--poll", type=float, default=2.0, help="pipeline 轮询间隔秒数")
    p.add_argument("--out", default="", help="最终结果 JSON 输出路径")
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


def random_email(domain: str, prefix_len: int) -> str:
    prefix = "".join(secrets.choice(ALPHABET) for _ in range(max(3, prefix_len)))
    return f"{prefix}@{domain.strip().lstrip('@')}"


def read_mothers(args: argparse.Namespace) -> list[str]:
    if args.mothers_file:
        raw = Path(args.mothers_file).read_text(encoding="utf-8")
        mothers = split_many(raw)
    elif args.mothers:
        mothers = split_many(str(args.mothers or ""))
    else:
        count = max(0, int(args.mother_count or 0))
        domain = str(args.mother_domain or args.domain or "").strip().lstrip("@")
        if not count or not domain:
            raise SystemExit("[!] 需要 --mothers/--mothers-file，或 --mother-count + --mother-domain")
        mothers = [random_email(domain, int(args.mother_prefix_len or 10)) for _ in range(count)]
    mothers = [x for x in mothers if "@" in x]
    if not mothers:
        raise SystemExit("[!] 母号邮箱列表为空，或格式不是邮箱")
    return list(dict.fromkeys(mothers))


def normalize_proxy(raw: str, scheme: str = "http") -> str:
    raw = str(raw or "").strip()
    if not raw or raw.startswith("#"):
        return ""
    if "://" in raw:
        return raw
    parts = raw.split(":")
    if len(parts) >= 4:
        host, port = parts[0], parts[1]
        user = ":".join(parts[2:-1])
        pwd = parts[-1]
        return f"{scheme}://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        return f"{scheme}://{raw}"
    return raw


def load_proxies(args: argparse.Namespace) -> list[str]:
    raw = ""
    if args.proxy_pool_file:
        raw += Path(args.proxy_pool_file).read_text(encoding="utf-8")
    if args.proxy_pool:
        raw += "\n" + str(args.proxy_pool)
    proxies = [normalize_proxy(x, args.proxy_scheme) for x in split_many(raw)]
    proxies = [p for p in proxies if p]
    if not proxies and args.proxy_url:
        proxies = [args.proxy_url.strip()]
    return proxies


def choose_proxy(proxies: list[str]) -> str:
    return random.choice(proxies) if proxies else ""


def masked_proxy(proxy: str) -> str:
    if not proxy:
        return ""
    if "@" not in proxy:
        return proxy
    left, _, right = proxy.rpartition("@")
    scheme = left.split("://", 1)[0] if "://" in left else "http"
    return f"{scheme}://***@{right}"


def pipeline_snapshot(pipeline_id: int) -> dict[str, Any]:
    with Session(engine) as s:
        p = s.get(Pipeline, pipeline_id)
        if p is None:
            return {"id": pipeline_id, "status": "missing", "error": "missing"}
        return {
            "id": int(p.id or 0),
            "preset": p.preset,
            "status": p.status,
            "current_stage": p.current_stage,
            "completed_steps": p.completed_steps,
            "total_steps": p.total_steps,
            "error": p.error,
            "result": json_loads(p.result_json, fallback={}) or {},
        }


def wait_pipeline(pipeline_id: int, *, label: str, timeout: int, poll: float) -> dict[str, Any]:
    start = time.time()
    last = ""
    while True:
        snap = pipeline_snapshot(pipeline_id)
        status = str(snap.get("status") or "")
        if status != last:
            log(f"{label} pipeline #{pipeline_id}: {status} stage={snap.get('current_stage')}")
            last = status
        if status in JOB_TERMINAL_STATUSES or status == "missing":
            return snap
        if time.time() - start > timeout:
            raise TimeoutError(f"等待 {label} pipeline #{pipeline_id} 超时")
        time.sleep(max(0.2, poll))


def sso_one(email: str, args: argparse.Namespace, *, role: str, proxy_url: str = "") -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if args.sso_invite_code:
        extra["sso_invite_code"] = args.sso_invite_code
    if args.sso_connection_id:
        extra["sso_connection_id"] = args.sso_connection_id
    if args.sso_provider:
        extra["sso_provider"] = args.sso_provider
    stage_input = {"sso_email": email, "email": email, "proxy_url": proxy_url, "extra_config": extra}
    pid = create_pipeline(
        stages=["sso_oauth"],
        preset=f"codex_cli_{role}_sso",
        stage_inputs={"sso_oauth": stage_input},
        resource_bindings={},
        proxy_url=proxy_url,
        request_payload=stage_input,
    )
    snap = wait_pipeline(pid, label=f"{role} SSO {email}", timeout=args.wait_timeout, poll=args.poll)
    last = (snap.get("result") or {}).get("last_job_result") or {}
    return {
        "email": email,
        "pipeline_id": pid,
        "status": snap.get("status"),
        "error": snap.get("error"),
        "account_id": last.get("account_id"),
        "refresh_token_id": last.get("refresh_token_id"),
        "chatgpt_account_id": last.get("chatgpt_account_id"),
        "proxy": masked_proxy(proxy_url),
    }


def activate_one(email: str, args: argparse.Namespace, *, proxy_url: str = "") -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if args.sso_invite_code:
        extra["sso_invite_code"] = args.sso_invite_code
    if args.sso_connection_id:
        extra["sso_connection_id"] = args.sso_connection_id
    if args.sso_provider:
        extra["sso_provider"] = args.sso_provider
    stage_inputs = {
        "sso_oauth": {"sso_email": email, "email": email, "proxy_url": proxy_url, "extra_config": extra},
        "active": {"email": email, "sso_email": email, "proxy_url": proxy_url, "insecure": bool(args.insecure)},
    }
    pid = create_pipeline(
        stages=["sso_oauth", "active"],
        preset="codex_cli_invited_sso_active",
        stage_inputs=stage_inputs,
        resource_bindings={},
        proxy_url=proxy_url,
        request_payload={"email": email, "sso_email": email, "proxy_url": proxy_url},
    )
    snap = wait_pipeline(pid, label=f"activate {email}", timeout=args.wait_timeout, poll=args.poll)
    last = (snap.get("result") or {}).get("last_job_result") or {}
    return {
        "email": email,
        "pipeline_id": pid,
        "status": snap.get("status"),
        "error": snap.get("error"),
        "activated": bool(last.get("activated")),
        "success_count": last.get("success_count"),
        "total_count": last.get("total_count"),
        "refresh_token_id": last.get("refresh_token_id"),
        "proxy": masked_proxy(proxy_url),
    }


def run_sso_batch(mothers: list[str], args: argparse.Namespace, proxies: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    usable: list[str] = []
    results: list[dict[str, Any]] = []
    concurrency = max(1, int(args.general_concurrency or 1))
    log(f"开始母号 SSO：数量={len(mothers)} 通用并发={concurrency}")
    with ThreadPoolExecutor(max_workers=min(concurrency, len(mothers)), thread_name_prefix="mother-sso") as pool:
        futures = {pool.submit(sso_one, email, args, role="mother", proxy_url=choose_proxy(proxies)): email for email in mothers}
        for fut in as_completed(futures):
            email = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"email": email, "status": "failed", "error": str(exc)}
            results.append(r)
            if r.get("status") == "succeeded":
                usable.append(email)
                log(f"母号 SSO 成功: {email}", "✓")
            else:
                log(f"母号 SSO 失败: {email} {r.get('error')}", "!")
    return usable, sorted(results, key=lambda x: mothers.index(x.get("email")) if x.get("email") in mothers else 999999)


def concurrent_invite(mothers: list[str], args: argparse.Namespace, proxies: list[str]) -> dict[str, Any]:
    base = SimpleNamespace(
        per_inviter=max(1, min(5, int(args.per_mother or 5))),
        concurrency=max(1, int(args.invite_concurrency or 1)),
        domain=args.domain or "",
        prefix_len=max(3, min(64, int(args.prefix_len or 20))),
        source_type=args.source_type or "auto",
        proxy_url="",
        timeout=30,
        dry_run=bool(args.dry_run_invite),
        no_eligibility=bool(args.no_eligibility),
        insecure=bool(args.insecure),
        barrier_send=bool(getattr(args, "barrier_send", False)),
        barrier_timeout=float(getattr(args, "barrier_timeout", 20.0) or 20.0),
    )
    max_workers = max(1, min(base.concurrency, len(mothers)))
    log(f"开始并发邀请：母号={len(mothers)} 邀请并发={max_workers} 每母号={base.per_inviter} barrier={base.barrier_send}")
    results: list[dict[str, Any]] = []
    barrier_event = Event() if base.barrier_send else None
    barrier_lock = Lock() if base.barrier_send else None
    barrier_state = {"ready": 0, "target": max_workers, "timed_out": False} if base.barrier_send else None

    def one(i: int, mother: str) -> dict[str, Any]:
        proxy = choose_proxy(proxies)
        a = SimpleNamespace(**vars(base))
        a.proxy_url = proxy
        if base.barrier_send:
            a.barrier_event = barrier_event
            a.barrier_lock = barrier_lock
            a.barrier_state = barrier_state
        r = invite_mod.process_inviter(i, len(mothers), mother, a)
        r["proxy"] = masked_proxy(proxy)
        return r

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="codex-invite") as pool:
        futures = {pool.submit(one, i, mother): mother for i, mother in enumerate(mothers, 1)}
        for fut in as_completed(futures):
            mother = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"inviter": mother, "ok": False, "sent": False, "emails": [], "invites": [], "sent_count": 0, "error": str(exc)}
            results.append(r)
            if r.get("ok"):
                log(f"邀请完成 {r.get('source_email') or mother}: {r.get('sent_count')}/{len(r.get('emails') or [])}", "✓")
            else:
                log(f"邀请失败 {r.get('source_email') or mother}: {r.get('error')}", "!")
    results.sort(key=lambda x: int(x.get("index") or 0))
    invited: list[str] = []
    for r in results:
        if not r.get("ok") or r.get("dry_run"):
            continue
        extracted = []
        for item in r.get("invites") or []:
            if isinstance(item, dict) and item.get("email"):
                extracted.append(str(item.get("email")).strip())
        if not extracted:
            extracted = [str(x).strip() for x in (r.get("emails") or []) if str(x).strip()]
        invited.extend(extracted)
    invited = list(dict.fromkeys([x for x in invited if "@" in x]))
    return {
        "results": results,
        "invited_emails": invited,
        "summary": {
            "total_mothers": len(mothers),
            "ok_mothers": sum(1 for r in results if r.get("ok")),
            "failed_mothers": sum(1 for r in results if not r.get("ok")),
            "invited_count": len(invited),
            "dry_run": bool(args.dry_run_invite),
        },
    }


def run_activation_batch(emails: list[str], args: argparse.Namespace, proxies: list[str]) -> list[dict[str, Any]]:
    concurrency = max(1, int(args.general_concurrency or 1))
    log(f"开始子号 SSO+active：数量={len(emails)} 通用并发={concurrency}")
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(concurrency, len(emails)), thread_name_prefix="child-active") as pool:
        futures = {pool.submit(activate_one, email, args, proxy_url=choose_proxy(proxies)): email for email in emails}
        for fut in as_completed(futures):
            email = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                r = {"email": email, "status": "failed", "error": str(exc), "activated": False}
            results.append(r)
            if r.get("status") == "succeeded":
                log(f"子号激活成功: {email}", "✓")
            else:
                log(f"子号激活失败: {email} {r.get('error')}", "!")
    order = {e: i for i, e in enumerate(emails)}
    return sorted(results, key=lambda x: order.get(x.get("email"), 999999))


def apply_concurrency(args: argparse.Namespace, pool: Any | None) -> None:
    gen = max(1, int(args.general_concurrency or 1))
    settings.set_many({
        "worker_concurrency.sso_oauth": str(gen),
        "worker_concurrency.active": str(gen),
    })
    if pool is not None:
        try:
            pool.set_concurrency("sso_oauth", gen)
            pool.set_concurrency("active", gen)
        except Exception as exc:
            log(f"本地 worker 并发设置失败: {exc}", "!")
        return
    # 如果走 systemd 服务，尝试调用 API 让运行中的 worker 立即 resize。
    try:
        import requests
        requests.put(
            "http://127.0.0.1:8000/api/settings",
            json={"data": {"worker_concurrency.sso_oauth": str(gen), "worker_concurrency.active": str(gen)}},
            timeout=3,
        )
    except Exception:
        log("已写入并发配置；如服务未动态生效，可重启服务或使用 --local-worker", "!")


def main() -> int:
    args = parse_args()
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    init_db()
    pool = None
    if args.local_worker:
        recover_orphan_jobs()
        pool = get_pool()
        pool.start()
    apply_concurrency(args, pool)

    mothers = read_mothers(args)
    proxies = load_proxies(args)
    log("流程开始：母号 SSO -> 并发邀请 -> 子号 SSO+active", "=")
    log(f"母号数={len(mothers)} 通用并发={args.general_concurrency} 邀请并发={args.invite_concurrency} 每母号邀请={max(1, min(5, int(args.per_mother or 5)))}")
    log(f"代理池={len(proxies)} 个，将随机分配" if proxies else "未配置代理池")

    mother_sso_results: list[dict[str, Any]] = []
    usable_mothers = mothers[:]
    if not args.skip_mother_sso:
        usable_mothers, mother_sso_results = run_sso_batch(mothers, args, proxies)
    else:
        log("已跳过母号 SSO，直接使用账号池母号邀请", "!")

    if not usable_mothers:
        raise SystemExit("[!] 没有可用于邀请的母号")

    if args.mother_sso_only:
        final = {
            "mothers": mothers,
            "usable_mothers": usable_mothers,
            "mother_sso": mother_sso_results,
            "invite": {"results": [], "invited_emails": [], "summary": {}},
            "activation": [],
            "summary": {
                "mother_total": len(mothers),
                "mother_sso_ok": len(usable_mothers) if not args.skip_mother_sso else None,
                "mother_sso_only": True,
                "general_concurrency": max(1, int(args.general_concurrency or 1)),
                "proxy_count": len(proxies),
            },
        }
        print("\n" + "=" * 72)
        print(json.dumps(final["summary"], ensure_ascii=False, indent=2))
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"结果已写入: {out}", "+")
        if pool is not None:
            pool.stop()
        return 0

    invite_result = concurrent_invite(usable_mothers, args, proxies)
    invited_emails = invite_result["invited_emails"]
    log(f"并发邀请结束，成功邀请邮箱数: {len(invited_emails)}", "✓" if invited_emails else "!")

    activation_results: list[dict[str, Any]] = []
    if not args.skip_activation and not args.dry_run_invite and invited_emails:
        activation_results = run_activation_batch(invited_emails, args, proxies)
    elif args.skip_activation:
        log("已按参数跳过子号激活")
    elif args.dry_run_invite:
        log("邀请 dry-run，跳过子号激活")

    final = {
        "mothers": mothers,
        "usable_mothers": usable_mothers,
        "mother_sso": mother_sso_results,
        "invite": invite_result,
        "activation": activation_results,
        "summary": {
            "mother_total": len(mothers),
            "mother_sso_ok": len(usable_mothers) if not args.skip_mother_sso else None,
            "invite_ok_mothers": invite_result["summary"]["ok_mothers"],
            "invited_count": len(invited_emails),
            "activation_ok": sum(1 for r in activation_results if r.get("status") == "succeeded"),
            "activation_total": len(activation_results),
            "general_concurrency": max(1, int(args.general_concurrency or 1)),
            "invite_concurrency": max(1, int(args.invite_concurrency or 1)),
            "proxy_count": len(proxies),
        },
    }
    print("\n" + "=" * 72)
    print(json.dumps(final["summary"], ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"结果已写入: {out}", "+")

    if pool is not None:
        pool.stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[!] 用户取消", file=sys.stderr)
        raise SystemExit(130)
