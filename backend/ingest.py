"""A licensed provider boundary, deterministic mocks, and receive-order replay.

Licensed webhook clients map their contracted feed into FightEvent; raw bytes
are persisted before validation. No unlicensed vendor feed is impersonated.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from .domain import FightEvent, canonical, digest


class LicensedIngest:
    def __init__(self, store, shared_secret: bytes):
        if len(shared_secret) < 32: raise ValueError("licensed ingest secret must be at least 32 bytes")
        self.store,self.secret = store,shared_secret

    def accept(self, raw: bytes, signature: str, signed_ms: int, recv_ms: int, license_id: str):
        if abs(recv_ms-signed_ms) > 60000: raise ValueError("expired ingest signature")
        signed = str(signed_ms).encode()+b"."+raw
        expected = hmac.new(self.secret,signed,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature): raise ValueError("invalid ingest signature")
        self.store.append("licensed","provider_raw",{"license_id":license_id,"signed_ms":signed_ms},raw=raw,recv_utc_ns=recv_ms*1_000_000)
        return FightEvent.model_validate_json(raw)


def mock_event(index: int, start_ms: int, bout_id="MOCK-BOUT-001"):
    # Fixed arithmetic, no randomness or real fighters, deterministic by index.
    round_index = min(index//11,2)
    within = index%11
    clock = max(0,300-within*30)
    return FightEvent(bout_id=bout_id,provider_event_id=f"mock:{index}",kind="round_start" if within == 0 else "round_end" if within == 10 else "stats",
                      seq=index+1,source_ts_ms=start_ms+index*1000,round=round_index+1,clock_remaining=clock,
                      strikes_a=index*3,strikes_b=index*2,knockdowns_a=int(index>=15),control_a=index*4,control_b=index*2,snapshot=True)


def replay_records(records, latency_ms=None):
    """Merge in availability order; tie-break by recorded ID.

    Same-host sessions preserve their recorded monotonic order. Cross-host
    merging requires UTC and a verified synchronization log in the manifest.
    """
    latency_ms = latency_ms or {}
    rows = list(records)
    sessions = {r.get("host_session") for r in rows}
    if len(sessions) > 1:
        raise ValueError("cross-session replay requires an explicit synchronized merge; replay one recording session")
    return sorted(rows,key=lambda r:(r["recv_mono_ns"]+latency_ms.get(r["source"],0)*1_000_000,r["id"]))


@dataclass
class ReplayManifest:
    code_sha256: str
    config_sha256: str
    input_sha256: str
    latency_ms: dict
    mode: str = "as_recorded"

    def hash(self): return digest(vars(self))
