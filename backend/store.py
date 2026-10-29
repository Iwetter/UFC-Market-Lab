"""SQLite WAL event journal and transactional projections; hashes cover raw bytes.

SQLite is the single-writer operational store. DuckDB export is a separate
research operation, never a second competing writer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import canonical

DDL = """
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, channel TEXT NOT NULL,
 source_ts_ms INTEGER, sending_ts_ms INTEGER, recv_utc_ns INTEGER NOT NULL,
 recv_mono_ns INTEGER NOT NULL, host_session TEXT NOT NULL, seq INTEGER, sid INTEGER,
 payload TEXT NOT NULL, raw_b64 TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT UNIQUE NOT NULL);
CREATE INDEX IF NOT EXISTS events_session_time ON events(host_session,recv_utc_ns);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, intent_hash TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, order_id TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, ts_ms INTEGER NOT NULL, order_id TEXT, amount TEXT NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY, ts_ms INTEGER NOT NULL, ticker TEXT NOT NULL, body TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mappings(ticker TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(DDL)
        self.session = str(time.time_ns())

    @contextmanager
    def transaction(self):
        nested = self.db.in_transaction
        self.db.execute("SAVEPOINT nested_operation" if nested else "BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("RELEASE SAVEPOINT nested_operation" if nested else "COMMIT")
        except BaseException:
            if nested:
                self.db.execute("ROLLBACK TO SAVEPOINT nested_operation")
                self.db.execute("RELEASE SAVEPOINT nested_operation")
            else:self.db.execute("ROLLBACK")
            raise

    def append(self, source, channel, payload, *, raw: bytes | None = None,
               source_ts_ms=None, sending_ts_ms=None, seq=None, sid=None,
               recv_utc_ns=None, recv_mono_ns=None):
        raw = raw if raw is not None else canonical(payload).encode()
        row = dict(source=source, channel=channel, source_ts_ms=source_ts_ms,
                   sending_ts_ms=sending_ts_ms, recv_utc_ns=recv_utc_ns if recv_utc_ns is not None else time.time_ns(),
                   recv_mono_ns=recv_mono_ns if recv_mono_ns is not None else time.monotonic_ns(),
                   host_session=self.session, seq=seq, sid=sid, payload=canonical(payload),
                   raw_b64=base64.b64encode(raw).decode())
        previous = self.db.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        row["previous_hash"] = previous[0] if previous else "0" * 64
        row["hash"] = hashlib.sha256(canonical(row).encode()).hexdigest()
        cols = list(row)
        cur = self.db.execute(f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", list(row.values()))
        return cur.lastrowid

    def verify(self):
        previous = "0" * 64
        for record in self.db.execute("SELECT * FROM events ORDER BY id"):
            row = dict(record); row.pop("id"); claimed = row.pop("hash")
            if row["previous_hash"] != previous or hashlib.sha256(canonical(row).encode()).hexdigest() != claimed:
                return False
            previous = claimed
        return True

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical(value)))

    def bodies(self, table):
        if table not in {"orders", "fills", "mappings", "runs"}:
            raise ValueError("invalid projection")
        return [json.loads(row[0]) for row in self.db.execute(f"SELECT body FROM {table} ORDER BY rowid")]

    def prediction(self, ts, ticker, body):
        prev = self.db.execute("SELECT hash FROM predictions ORDER BY id DESC LIMIT 1").fetchone()
        prev = prev[0] if prev else "0" * 64
        value = canonical(body)
        h = hashlib.sha256(f"{prev}|{ts}|{ticker}|{value}".encode()).hexdigest()
        self.db.execute("INSERT INTO predictions(ts_ms,ticker,body,previous_hash,hash) VALUES (?,?,?,?,?)", (ts,ticker,value,prev,h))

    def close(self):
        self.db.close()
