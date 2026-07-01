#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 3: activate invited emails by running sso_oauth -> active."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from backend.core.db import init_db
from backend.core.queue import get_pool, recover_orphan_jobs
import backend.stages  # noqa
import backend.core.pools  # noqa
from scripts import codex_sso_invite_activate as flow


def parse_args():
    p=argparse.ArgumentParser(description='Step3 被邀请邮箱 SSO OAuth + active 激活')
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument('--emails')
    src.add_argument('--emails-file')
    src.add_argument('--from-json',help='读取 step2 输出 JSON 的 invited_emails')
    p.add_argument('--general-concurrency',type=int,default=3)
    p.add_argument('--proxy-url',default='')
    p.add_argument('--proxy-pool',default='')
    p.add_argument('--proxy-pool-file',default='')
    p.add_argument('--proxy-scheme',default='http',choices=['http','https','socks5'])
    p.add_argument('--sso-invite-code',default='')
    p.add_argument('--sso-connection-id',default='')
    p.add_argument('--sso-provider',type=int,default=2)
    p.add_argument('--insecure',action='store_true')
    p.add_argument('--wait-timeout',type=int,default=3600)
    p.add_argument('--poll',type=float,default=2.0)
    p.add_argument('--local-worker',action='store_true')
    p.add_argument('--out',default='activation_result.json')
    return p.parse_args()


def read_emails(args):
    if args.from_json:
        d=json.loads(Path(args.from_json).read_text(encoding='utf-8'))
        return [str(x).strip() for x in d.get('invited_emails',[]) if str(x).strip()]
    raw=Path(args.emails_file).read_text(encoding='utf-8') if args.emails_file else args.emails
    return [x for x in flow.split_many(raw) if '@' in x]


def main():
    args=parse_args(); init_db()
    pool=None
    if args.local_worker:
        recover_orphan_jobs(); pool=get_pool(); pool.start()
    flow.apply_concurrency(args,pool)
    emails=read_emails(args)
    if not emails: raise SystemExit('[!] 没有待激活邮箱')
    proxies=flow.load_proxies(args)
    results=flow.run_activation_batch(emails,args,proxies)
    summary={'activation_total':len(results),'activation_ok':sum(1 for r in results if r.get('status')=='succeeded'),'activation_failed':sum(1 for r in results if r.get('status')!='succeeded'),'general_concurrency':args.general_concurrency,'proxy_count':len(proxies)}
    final={'emails':emails,'activation':results,'summary':summary}
    Path(args.out).write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(f'[+] JSON: {args.out}')
    if pool: pool.stop()
    return 0 if summary['activation_ok'] else 1

if __name__=='__main__': raise SystemExit(main())
