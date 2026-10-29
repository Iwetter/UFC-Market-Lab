"""Optional licensed UFC DDE capture, with cumulative position-time snapshots.

Official contracts reviewed 2026-10-29:
https://docs.sportradar.com/ufc/stream-endpoints-websocket/fight-stats
https://docs.sportradar.com/ufc/stream-endpoints-websocket/fight-details

No vendor credential or licensed live observation is bundled. The public docs
say to send a JSON packet containing a token, but do not document the token
property name. An operator must supply their contracted auth packet verbatim
as a private JSON file. This module never invents an authentication schema.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

import websockets
from pydantic import BaseModel, ConfigDict, Field

from .domain import canonical, digest

D = Decimal
BASE_WS = "wss://dde-streams.data.srarena.io/mma/fights"
_PRIVATE_WS_LOGGER=logging.getLogger("octagon.sportradar.private_websocket")
_PRIVATE_WS_LOGGER.disabled=True  # Client debug frame logs must never expose auth JSON.
TIP_FIELDS = {
    "backControlTime":"back_control", "clinchTime":"clinch", "controlTime":"control",
    "distanceTime":"distance", "groundControlTime":"ground_control", "groundTime":"ground",
    "guardControlTime":"guard_control", "halfGuardControlTime":"half_guard_control",
    "miscGroundControlTime":"misc_ground_control", "mountControlTime":"mount_control",
    "neutralTime":"neutral", "sideControlTime":"side_control", "standingTime":"standing",
}


def duration_seconds(value: str) -> Decimal:
    """MM:SS[.fraction] or HH:MM:SS[.fraction], with no unit guessing."""
    if not isinstance(value,str) or not re.fullmatch(r"\d{1,4}:\d{2}(?::\d{2})?(?:\.\d{1,9})?",value):
        raise ValueError("duration must be a documented clock string, not an assumed numeric unit")
    parts=value.split(":")
    seconds=D(parts[-1])
    minutes=int(parts[-2])
    if seconds>=60 or (len(parts)==3 and minutes>=60):
        raise ValueError("invalid duration clock component")
    return seconds+D(minutes*60)+(D(int(parts[0])*3600) if len(parts)==3 else D(0))


def iso_timestamp_ms(value: str) -> int:
    if not isinstance(value,str):raise ValueError("provider ISO8601 timestamp required")
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None:raise ValueError("provider timestamp must include a UTC offset")
    delta=parsed.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return delta.days*86_400_000+delta.seconds*1000+delta.microseconds//1000


def _identifier(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or not str(value).isdigit() or int(value)<=0:
        raise ValueError("positive provider ID required")
    return str(value)


def _seq(value):
    if type(value) is not int or value<1:raise ValueError("positive integer provider sequence required")
    return value


class PositionTimes(BaseModel):
    """Cumulative seconds, not current position. Missing fields remain unknown."""
    model_config=ConfigDict(extra="forbid")
    back_control: Decimal | None=None
    clinch: Decimal | None=None
    control: Decimal | None=None
    distance: Decimal | None=None
    ground_control: Decimal | None=None
    ground: Decimal | None=None
    guard_control: Decimal | None=None
    half_guard_control: Decimal | None=None
    misc_ground_control: Decimal | None=None
    mount_control: Decimal | None=None
    neutral: Decimal | None=None
    side_control: Decimal | None=None
    standing: Decimal | None=None


class FighterPositionTimes(BaseModel):
    model_config=ConfigDict(extra="forbid")
    fighter_id: str | None=None
    ufc_fighter_id: str | None=None
    seconds: PositionTimes


class PositionTimeSnapshot(BaseModel):
    model_config=ConfigDict(extra="forbid")
    schema_version: Literal[1]=1
    source: Literal["sportradar_dde"]="sportradar_dde"
    stream: Literal["livestats"]="livestats"
    license_reference: str
    fight_id: str
    fight_card_id: str | None=None
    seq: int=Field(ge=1)
    round: int=Field(ge=1,le=5)
    max_rounds: Literal[3,5]
    clock_remaining_seconds: Decimal=Field(ge=0,le=300)
    provider_generated_ts_ms: int
    timestamp_basis: Literal["provider_packet_generated"]="provider_packet_generated"
    original_timestamp: str
    received_utc_ns: int=Field(ge=0)
    received_mono_ns: int=Field(ge=0)
    cumulative: Literal[True]=True
    instantaneous_position_known: Literal[False]=False
    red: FighterPositionTimes
    blue: FighterPositionTimes
    round_positions: dict[int,dict[str,FighterPositionTimes]]=Field(default_factory=dict)


def _position_times(corner: dict, fallback: dict, max_seconds: int):
    if not isinstance(corner,dict) or not isinstance(fallback,dict):raise ValueError("fighter metadata object required")
    tip=corner.get("TIP",{})
    if not isinstance(tip,dict):raise ValueError("TIP object required")
    values={}
    for original,canonical_name in TIP_FIELDS.items():
        if original in tip and tip[original] is not None:
            seconds=duration_seconds(tip[original])
            if seconds>max_seconds:raise ValueError("position duration exceeds scheduled fight duration")
            values[canonical_name]=seconds
    fid=corner.get("fighterId",fallback.get("fighterId"))
    ufc_id=corner.get("ufcFighterId",fallback.get("ufcFighterId"))
    return FighterPositionTimes(fighter_id=_identifier(fid) if fid is not None else None,
                                ufc_fighter_id=str(ufc_id) if ufc_id is not None else None,
                                seconds=PositionTimes(**values))


def normalize_position_packet(packet: dict, *, received_utc_ns: int, received_mono_ns: int,
                              license_reference: str) -> PositionTimeSnapshot:
    if not license_reference.strip():raise ValueError("declared data-license reference required")
    sequence=_seq(packet["seqNum"])
    maximum=packet["maxRounds"]
    current=packet["currentRound"]
    if type(maximum) is not int or maximum not in (3,5) or type(current) is not int or not 1<=current<=maximum:
        raise ValueError("invalid current or scheduled round count")
    fight_stats=packet["fightStats"]
    fighters=packet.get("fighters",{})
    if not isinstance(fight_stats,dict) or not isinstance(fighters,dict):raise ValueError("cumulative fightStats object required")
    red=_position_times(fight_stats.get("red",{}),fighters.get("red",{}),maximum*300)
    blue=_position_times(fight_stats.get("blue",{}),fighters.get("blue",{}),maximum*300)
    rounds={}
    round_stats=packet.get("roundStats",{})
    if not isinstance(round_stats,dict):raise ValueError("roundStats must be an object when supplied")
    for round_label,stats in round_stats.items():
        if not str(round_label).isdigit() or not 1<=int(round_label)<=maximum or not isinstance(stats,dict):
            raise ValueError("invalid roundStats entry")
        rounds[int(round_label)]={corner:_position_times(stats.get(corner,{}),fighters.get(corner,{}),300)
                                  for corner in ("red","blue")}
    return PositionTimeSnapshot(license_reference=license_reference,fight_id=_identifier(packet["fightId"]),
                fight_card_id=_identifier(packet["fightCardId"]) if packet.get("fightCardId") is not None else None,
                seq=sequence,round=current,max_rounds=maximum,
                clock_remaining_seconds=duration_seconds(packet["currentRoundTime"]),
                provider_generated_ts_ms=iso_timestamp_ms(packet["timestamp"]),original_timestamp=packet["timestamp"],
                received_utc_ns=received_utc_ns,received_mono_ns=received_mono_ns,red=red,blue=blue,
                round_positions=rounds)


class DDEConfig(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled: bool=False
    license_reference: str=""
    fight_id: int=Field(gt=0)
    stream: Literal["livestats","details"]="livestats"
    auth_packet_file: Path | None=Field(default=None,repr=False)
    start_position: int=Field(default=1,ge=1)
    initial_reconnect_seconds: float=Field(default=1,ge=.1,le=30)
    max_reconnect_seconds: float=Field(default=30,ge=1,le=60)

    @classmethod
    def from_env(cls, fight_id: int, stream="livestats"):
        path=os.getenv("UFC_DDE_AUTH_PACKET_FILE")
        return cls(enabled=os.getenv("UFC_DDE_ENABLED")=="1",license_reference=os.getenv("UFC_DATA_LICENSE_ID",""),
                   fight_id=fight_id,stream=stream,auth_packet_file=Path(path) if path else None)


@dataclass(frozen=True)
class DDEObservation:
    """Details timestamps are kept unverified, never relabeled occurrence time."""
    stream: str
    seq: int
    packet: dict
    received_utc_ns: int
    received_mono_ns: int
    source_timestamp_ms: int | None
    timestamp_basis: str
    position_snapshot: PositionTimeSnapshot | None=None
    event_element_type_raw: str | None=None


class DDESequenceGap(ValueError):
    def __init__(self,expected,received):
        self.expected,self.received=expected,received
        super().__init__("licensed feed sequence gap; reconnect at last contiguous sequence + 1")


def _string_leaves(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from _string_leaves(item)
    elif isinstance(value,list):
        for item in value:yield from _string_leaves(item)


class SportradarDDERecorder:
    """At-least-once callback delivery, idempotent by stream/fight/seq.

    Resume cursors survive restarts. A gap does not promote a future snapshot;
    reconnect requests the missing contiguous sequence. All received server
    frames are journaled before parsing, except authentication-secret echoes,
    which are dropped without persisting their bytes.
    """
    def __init__(self,store,config:DDEConfig,on_observation:Callable|None=None,*,connect=None):
        if not config.enabled:raise ValueError("licensed DDE adapter is disabled")
        if not config.license_reference.strip():raise ValueError("UFC_DATA_LICENSE_ID is required")
        if config.auth_packet_file is None:raise ValueError("private contracted auth-packet JSON file is required")
        raw=config.auth_packet_file.read_bytes()
        if len(raw)>65536:raise ValueError("auth-packet file too large")
        auth=json.loads(raw)
        if not isinstance(auth,dict) or not auth:raise ValueError("auth-packet must be a nonempty JSON object")
        secrets=[value for value in _string_leaves(auth) if value.strip()]
        if not secrets:raise ValueError("auth-packet has no configured credential string")
        self._auth_packet=canonical(auth)
        self._secret_fragments=[json.dumps(v,ensure_ascii=False).encode()[1:-1] for v in secrets if v]
        self.store,self.config,self.on_observation=store,config,on_observation
        self._connect=connect or websockets.connect
        self._state_key=f"sportradar_dde:{config.fight_id}:{config.stream}:cursor"
        saved=store.get(self._state_key,{"seq":config.start_position-1,"payload_hash":None})
        self.last_seq=int(saved["seq"])
        self.last_hash=saved.get("payload_hash")
        self.health={"status":"configured","stale":True,"sequence_gaps":0,"last_seq":self.last_seq,
                     "accepted_packets":0,"error":None}

    @property
    def url(self):
        return f"{BASE_WS}/{self.config.fight_id}/{self.config.stream}?startPosition={self.last_seq+1}"

    async def accept_frame(self,raw:bytes|str,*,received_utc_ns=None,received_mono_ns=None):
        recv=time.time_ns() if received_utc_ns is None else received_utc_ns
        mono=time.monotonic_ns() if received_mono_ns is None else received_mono_ns
        frame=raw.encode() if isinstance(raw,str) else raw
        if any(secret in frame for secret in self._secret_fragments):
            self.health.update(status="credential_echo_blocked",stale=True,error="credential_echo")
            self.store.append("sportradar_dde","credential_echo_blocked",{"fight_id":str(self.config.fight_id),"stream":self.config.stream},raw=b"",recv_utc_ns=recv,recv_mono_ns=mono)
            raise ValueError("server echoed configured credential; content was not retained")
        self.store.append("sportradar_dde","frame",{"fight_id":str(self.config.fight_id),"stream":self.config.stream,
                          "license_reference":self.config.license_reference},raw=frame,recv_utc_ns=recv,recv_mono_ns=mono)
        self.health["last_packet_ms"]=recv//1_000_000
        packet=json.loads(frame,parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite JSON value")))
        if not isinstance(packet,dict):raise ValueError("provider packet must be an object")
        if "seqNum" not in packet:
            # The public pages do not specify auth-response/heartbeat schemas.
            # Retain these control frames without guessing their meaning.
            return None
        seq=_seq(packet["seqNum"])
        payload_hash=digest(packet)
        if seq<=self.last_seq:
            if seq==self.last_seq and self.last_hash and payload_hash!=self.last_hash:
                self.health.update(stale=True,error="conflicting_duplicate")
                raise ValueError("conflicting payload at an already accepted sequence")
            return None
        if seq!=self.last_seq+1:
            self.health.update(stale=True,error="sequence_gap",sequence_gaps=self.health["sequence_gaps"]+1)
            raise DDESequenceGap(self.last_seq+1,seq)
        if packet.get("fightId") is not None and _identifier(packet["fightId"])!=str(self.config.fight_id):
            raise ValueError("packet belongs to a different provider fight")
        snapshot=None
        if self.config.stream=="livestats":
            snapshot=normalize_position_packet(packet,received_utc_ns=recv,received_mono_ns=mono,license_reference=self.config.license_reference)
            source_ts=snapshot.provider_generated_ts_ms
            timestamp_basis="provider_packet_generated"
        else:
            source_ts=iso_timestamp_ms(packet["timestamp"]) if packet.get("timestamp") is not None else None
            timestamp_basis="provider_timestamp_semantics_unverified"
        element=packet.get("eventElementType")
        if element is not None and not isinstance(element,str):raise ValueError("eventElementType must be a source string")
        observation=DDEObservation(self.config.stream,seq,packet,recv,mono,source_ts,timestamp_basis,snapshot,element)
        if self.on_observation:
            returned=self.on_observation(observation)
            if inspect.isawaitable(returned):await returned
        body=snapshot.model_dump(mode="json") if snapshot else {"fight_id":str(self.config.fight_id),"seq":seq,
                  "event_element_type_raw":element,"round_num_raw":packet.get("roundNum"),"round_time_raw":packet.get("roundTime"),
                  "source_timestamp_ms":source_ts,"timestamp_basis":timestamp_basis,"event_occurrence_time_verified":False}
        with self.store.transaction():
            self.store.append("sportradar_dde","position_times" if snapshot else "fight_details",body,
                              source_ts_ms=source_ts,seq=seq,recv_utc_ns=recv,recv_mono_ns=mono)
            self.store.put(self._state_key,{"seq":seq,"payload_hash":payload_hash})
        self.last_seq,self.last_hash=seq,payload_hash
        self.health.update(status="receiving",stale=False,error=None,last_seq=seq,last_data_ms=recv//1_000_000,
                           accepted_packets=self.health["accepted_packets"]+1)
        return observation

    async def run(self):
        retry=0
        while True:
            try:
                async with self._connect(self.url,ping_interval=10,ping_timeout=10,open_timeout=10,close_timeout=5,max_size=8_000_000,max_queue=256,logger=_PRIVATE_WS_LOGGER) as ws:
                    self.health.update(status="awaiting_provider_data",stale=True)
                    await ws.send(self._auth_packet)
                    async for raw in ws:
                        await self.accept_frame(raw)
                        retry=0
            except asyncio.CancelledError:raise
            except Exception as exc:
                # Never log repr(exc), server responses or the authentication packet.
                self.health.update(status="reconnecting",stale=True,error=type(exc).__name__)
            finally:
                self.health["stale"]=True
            await asyncio.sleep(min(self.config.max_reconnect_seconds,self.config.initial_reconnect_seconds*2**min(retry,8)))
            retry+=1


def main():
    """Capture licensed packets without enabling or importing order execution."""
    import argparse
    from .store import Store
    parser=argparse.ArgumentParser(description="Optional licensed UFC DDE recorder; credentials are read only from a private contracted auth-packet file.")
    parser.add_argument("--fight-id",type=int,required=True)
    parser.add_argument("--stream",choices=["livestats","details"],default="livestats")
    parser.add_argument("--database",default="data/sportradar-dde.sqlite3")
    args=parser.parse_args()
    config=DDEConfig.from_env(args.fight_id,args.stream)
    store=Store(args.database)
    try:
        recorder=SportradarDDERecorder(store,config)
        print(f"Licensed DDE capture configured for fight {config.fight_id}, stream {config.stream}. No order execution is available.")
        asyncio.run(recorder.run())
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":main()
