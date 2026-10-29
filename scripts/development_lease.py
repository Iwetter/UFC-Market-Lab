"""Acquire/release the cooperating-agent lease; its token is local coordination only."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.development import DevelopmentLease


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['acquire','release','status'])
    p.add_argument('--owner',default='codex-ufc-development')
    p.add_argument('--token')
    args=p.parse_args();lease=DevelopmentLease(ROOT/'work')
    if args.action=='acquire':result=lease.acquire(args.owner)
    elif args.action=='release':
        if not args.token:p.error('release requires the token returned by acquire')
        result={'released':lease.release(args.token)}
    else:result=lease.status()
    print(json.dumps(result))
    if args.action=='acquire' and not result['acquired']:raise SystemExit(3)


if __name__=='__main__':main()
