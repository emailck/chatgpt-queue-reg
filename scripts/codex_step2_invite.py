#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 2: concurrent invitation only, using mother accounts from step1/db."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from backend.core.db import init_db
import backend.stages  # noqa
from scripts import codex_sso_invite_activate as flow


def parse_args():
    p=argparse.ArgumentParser(description='Step2 并发邀请，只调用 referral invite，不激活')
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument('--mothers')
    src.add_argument('--mothers-file')
    src.add_argument('--from-json',help='读取 step1 输出 JSON 的 usable_mothers')
    p.add_argument('--per-mother',type=int,default=5)
    p.add_argument('--invite-concurrency',type=int,default=10)
    p.add_argument('--domain',default='')
    p.add_argument('--prefix-len',type=int,default=20)
    p.add_argument('--source-type',default='auto')
    p.add_argument('--proxy-url',default='')
    p.add_argument('--proxy-pool',default='')
    p.add_argument('--proxy-pool-file',default='')
    p.add_argument('--proxy-scheme',default='http',choices=['http','https','socks5'])
    p.add_argument('--dry-run-invite',action='store_true')
    p.add_argument('--no-eligibility',action='store_true')
    p.add_argument('--insecure',action='store_true')
    p.add_argument('--barrier-send',action='store_true',help='所有母号准备好后再同时释放 POST 邀请请求')
    p.add_argument('--barrier-timeout',type=float,default=20.0,help='barrier 等待秒数，超时则直接发送')
    p.add_argument('--out',default='invite_result.json')
    p.add_argument('--invited-out',default='invited_emails.txt')
    return p.parse_args()


def read_mothers(args):
    if args.from_json:
        d=json.loads(Path(args.from_json).read_text(encoding='utf-8'))
        return [str(x).strip() for x in d.get('usable_mothers',[]) if str(x).strip()]
    return flow.read_mothers(args)


def main():
    args=parse_args(); init_db()
    mothers=read_mothers(args)
    if not mothers: raise SystemExit('[!] 没有可邀请母号')
    proxies=flow.load_proxies(args)
    result=flow.concurrent_invite(mothers,args,proxies)
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    Path(args.invited_out).write_text('\n'.join(result.get('invited_emails') or [])+'\n',encoding='utf-8')
    print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
    print(f'[+] JSON: {args.out}')
    print(f'[+] invited emails: {args.invited_out}')
    return 0 if result['summary'].get('ok_mothers') else 1

if __name__=='__main__': raise SystemExit(main())
