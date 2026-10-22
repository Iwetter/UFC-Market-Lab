"""Replay normalized journal inputs through the same Engine/PaperBroker."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .book import Book
from .domain import D, FightEvent, Market, OrderIntent, Settings, canonical, digest
from .engine import Engine
from .fees import FeeSchedule
from .ingest import replay_records, ReplayManifest
from .models import FightState


def run_replay(input_path, output_path, cfg:Settings, latency_ms=None, session=None, model_path=None, infight_model_path=None):
    if Path(input_path).resolve() == Path(output_path).resolve(): raise ValueError("replay output must be isolated")
    if Path(output_path).exists(): raise ValueError("replay output must be a new database")
    db = sqlite3.connect(f"file:{Path(input_path).resolve().as_posix()}?mode=ro",uri=True)
    db.row_factory = sqlite3.Row
    raw = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id")]
    db.close()
    if session: raw = [r for r in raw if r["host_session"] == session]
    rows = replay_records(raw,latency_ms)
    source_hash = digest(raw)
    code_hash = digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))})
    manifest = ReplayManifest(code_hash,digest(cfg),source_hash,latency_ms or {},"latency_stress" if latency_ms else "as_recorded")
    engine = Engine(cfg.model_copy(update={"database":str(output_path),"mode":"replay"}),model_path,infight_model_path)
    engine.clock_offset_ms = 0 if cfg.source == "mock" else None
    # UTC labels follow recorded receive time; monotonic controls merge order.
    first = min((r["recv_mono_ns"] for r in rows),default=0)
    first_utc = min((r["recv_utc_ns"] for r in rows),default=0)//1_000_000
    for row in rows:
        payload = json.loads(row["payload"])
        delay = (latency_ms or {}).get(row["source"],0)
        ts = row["recv_utc_ns"]//1_000_000+delay
        channel = row["channel"]
        if channel == "market": engine.add_market(Market.model_validate(payload))
        elif channel == "mapping_review":
            engine.save_mapping(payload)
            engine.fights.setdefault(payload["bout_id"],FightState(payload["bout_id"]))
        elif channel == "prior": engine.fights.setdefault(payload["bout_id"],FightState(payload["bout_id"])).prior = payload["p_a"]
        elif channel == "fight": engine.fight_event(FightEvent.model_validate(payload),ts,row["source"])
        elif channel == "book":
            book = engine.books.books.setdefault(payload["ticker"],Book())
            book.snapshot(payload["yes"],payload["no"],ts,payload.get("clock"))
            book.stale = payload["stale"]
        elif channel == "fees":
            engine.fees[payload["ticker"]] = FeeSchedule(payload["fee_type"],D(payload["multiplier"]),payload["verified_ms"],payload.get("valid_until_ms"))
        elif channel == "clock_offset":
            engine.clock_offset_ms=payload["offset_ms"]; engine.clock_observed_ms=payload["measured_ms"]
        elif channel == "model_approval": engine.store.put("model_approval",payload)
        elif channel == "exchange_status": engine.exchange_active=payload["active"]
        elif channel == "order_intent":
            engine.submit(OrderIntent.model_validate(payload["intent"]),ts)
        elif channel == "execution_tick": engine.tick(ts)
        elif channel == "settlement": engine.broker.settle(payload["ticker"],D(payload["value"]),ts,row["source"])
        elif channel == "kill_switch": engine.broker.kill(ts)
    result = {"orders":engine.broker.orders(),"fills":engine.store.bodies("fills"),"portfolio":engine.broker.summary(engine.books.books)}
    result_hash = digest(result)
    engine.store.db.execute("INSERT INTO runs VALUES (?,?)",(manifest.hash(),canonical({"manifest":vars(manifest),"output_sha256":result_hash})))
    engine.store.close()
    return {"manifest":vars(manifest),"output_sha256":result_hash,"result":result}
