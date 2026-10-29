"""Headless, restartable PAPER recorder; no browser or live-order module."""
import argparse
import asyncio
import signal
from pathlib import Path

from .domain import Settings
from .engine import Engine
from .operations import monitor_operations,writer_lease


async def record(cfg,*,run_seconds=None,backup_interval_s=21600,min_free_mb=1024):
    engine=Engine(cfg)
    tasks=[asyncio.create_task(engine.run()),asyncio.create_task(monitor_operations(engine,backup_interval_s=backup_interval_s,min_free_mb=min_free_mb))]
    if run_seconds is not None:tasks.append(asyncio.create_task(asyncio.sleep(run_seconds)))
    try:
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception():raise task.exception()
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        engine.store.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",choices=["kalshi","mock"],default="kalshi")
    p.add_argument("--database",default="data/kalshi.sqlite3")
    p.add_argument("--config",default="config/default.json")
    p.add_argument("--run-seconds",type=float,help="bounded smoke check")
    p.add_argument("--backup-interval-seconds",type=int,default=21600)
    p.add_argument("--min-free-mb",type=int,default=1024)
    args=p.parse_args()
    if args.run_seconds is not None and args.run_seconds<=0:p.error("run seconds must be positive")
    if args.backup_interval_seconds<0 or args.min_free_mb<1:p.error("invalid operations limits")
    cfg=Settings.model_validate_json(Path(args.config).read_text())
    cfg=Settings.model_validate(cfg.model_dump()|{"source":args.source,"database":args.database,"mode":"paper"})
    # asyncio.run handles SIGINT. Convert SIGTERM into clean cancellation too.
    def stop(*_):raise KeyboardInterrupt()
    if hasattr(signal,"SIGTERM"):signal.signal(signal.SIGTERM,stop)
    with writer_lease(cfg.database):
        try:asyncio.run(record(cfg,run_seconds=args.run_seconds,backup_interval_s=args.backup_interval_seconds,min_free_mb=args.min_free_mb))
        except KeyboardInterrupt:pass


if __name__ == "__main__":main()
