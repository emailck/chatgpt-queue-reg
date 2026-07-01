#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 1: create/login mother accounts via sso_oauth only."""
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
    p=argparse.ArgumentParser(description='Step1 母号 SSO OAuth，只生成/登录母号并保存 RT/AT/account_id')
    src=p.add_mutually_exclusive_group(required=False)
    src.add_argument('--mothers')
    src.add_argument('--mothers-file')
    p.add_argument('--mother-count',type=int,default=0)
    p.add_argument('--mother-domain',default='')
    p.add_argument('--mother-prefix-len',type=int,default=10)
    p.add_argument('--general-concurrency',type=int,default=3)
    p.add_argument('--proxy-url',default='')
    p.add_argument('--proxy-pool',default='')
    p.add_argument('--proxy-pool-file',default='')
    p.add_argument('--proxy-scheme',default='http',choices=['http','https','socks5'])
    p.add_argument('--sso-invite-code',default='')
    p.add_argument('--sso-connection-id',default='')
    p.add_argument('--sso-provider',type=int,default=2)
    p.add_argument('--wait-timeout',type=int,default=3600)
    p.add_argument('--poll',type=float,default=2.0)
    p.add_argument('--local-worker',action='store_true')
    p.add_argument('--out',default='mother_sso_result.json')
    p.add_argument('--usable-out',default='usable_mothers.txt')
    return p.parse_args()


def main():
    args=parse_args(); init_db()
    pool=None
    if args.local_worker:
        recover_orphan_jobs(); pool=get_pool(); pool.start()
    flow.apply_concurrency(args,pool)
    mothers=flow.read_mothers(args)
    proxies=flow.load_proxies(args)
    usable, results=flow.run_sso_batch(mothers,args,proxies)
    final={'mothers':mothers,'usable_mothers':usable,'mother_sso':results,'summary':{'mother_total':len(mothers),'mother_sso_ok':len(usable),'mother_sso_failed':len(mothers)-len(usable),'general_concurrency':args.general_concurrency,'proxy_count':len(proxies)}}
    Path(args.out).write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
    Path(args.usable_out).write_text('\n'.join(usable)+'\n',encoding='utf-8')
    print(json.dumps(final['summary'],ensure_ascii=False,indent=2))
    print(f'[+] JSON: {args.out}')
    print(f'[+] usable mothers: {args.usable_out}')
    if pool: pool.stop()
    return 0 if usable else 1

if __name__=='__main__': raise SystemExit(main())
