"""Always-on runtime helpers. No order-key or live-gateway capabilities."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .domain import now_ms


@contextmanager
def writer_lease(database):
    """OS-released advisory lease: one deployment writer per database."""
    path=Path(str(database)+".writer.lock")
    path.parent.mkdir(parents=True,exist_ok=True)
    # Do not use append mode: an attempted second writer must not grow or
    # otherwise mutate the lock file before obtaining the OS lease.
    handle=os.fdopen(os.open(path,os.O_RDWR|os.O_CREAT,0o600),"r+b")
    handle.seek(0)
    locked=False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        locked=True
        handle.seek(0);handle.write(b"0");handle.truncate(1);handle.flush();handle.seek(0)
        yield
    except OSError as exc:
        if not locked: raise RuntimeError("another recorder already owns this database") from exc
        raise
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()


def backup_database(database,destination):
    source=Path(database).resolve();dest=Path(destination).resolve()
    if source == dest or dest.exists(): raise ValueError("backup destination must be new and isolated")
    dest.parent.mkdir(parents=True,exist_ok=True)
    partial=dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.partial")
    src=sqlite3.connect(f"file:{source.as_posix()}?mode=ro",uri=True)
    target=None
    try:
        target=sqlite3.connect(partial)
        src.backup(target,pages=256,sleep=.01)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise RuntimeError("backup integrity check failed")
    finally:
        if target is not None:target.close()
        src.close()
    # Failed/interrupted copies remain visibly .partial for investigation. A
    # final .sqlite3 name is published only after verification and closure.
    if dest.exists():raise ValueError("backup destination appeared during capture; verified partial retained")
    partial.replace(dest)


def collector_health(engine,ts=None):
    ts=now_ms() if ts is None else ts
    last=engine.health.get("last_rest_ms",0)
    public_fresh=0 <= ts-last <= max(30000,engine.cfg.kalshi_poll_seconds*5000)
    source_ok=engine.cfg.source == "mock" or public_fresh
    return {"updated_ms":ts,"mode":"PAPER","source":engine.cfg.source,"live_enabled":False,
            "ok":source_ok and not engine.health.get("engine_fault"),"last_rest_ms":last,
            "last_ws_ms":engine.health.get("last_ws_ms"),"websocket":engine.health.get("websocket"),
            "market_count":len(engine.markets),"sequence_gaps":engine.books.gaps,
            "clock_offset_ms":engine.clock_offset_ms,"clock_observed_ms":engine.clock_observed_ms,
            "note":"Collector transport health is separate from trading eligibility and clock verification."}


async def monitor_operations(engine,*,health_file=None,backup_interval_s=21600,min_free_mb=1024):
    directory=Path(engine.cfg.database).resolve().parent
    health_path=Path(health_file) if health_file else directory/"collector-health.json"
    next_backup=now_ms()+backup_interval_s*1000
    while True:
        ts=now_ms()
        free=shutil.disk_usage(directory).free//(1024*1024)
        if free<min_free_mb:
            engine.health["engine_fault"]="insufficient_disk_space"
            engine.broker.kill(ts)
            raise RuntimeError("recorder paused before disk exhaustion; expand storage or archive a consistent snapshot")
        report=collector_health(engine,ts)|{"disk_free_mb":free}
        temp=health_path.with_suffix(".tmp")
        temp.write_text(json.dumps(report,indent=2),encoding="utf-8");temp.replace(health_path)
        if backup_interval_s>0 and ts>=next_backup:
            name=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".sqlite3"
            await asyncio.to_thread(backup_database,engine.cfg.database,directory/"backups"/name)
            next_backup=ts+backup_interval_s*1000
        await asyncio.sleep(10)
