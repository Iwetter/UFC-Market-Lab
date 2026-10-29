"""Contract-only synthetic packets; no licensed feed is contacted by these tests."""
import asyncio
import base64
import copy
import json
from decimal import Decimal as D

import pytest

from backend.sportradar import (DDEConfig,DDESequenceGap,SportradarDDERecorder,duration_seconds,
                               iso_timestamp_ms,normalize_position_packet)
from backend.store import Store


def packet(seq=1):
    return {"seqNum":seq,"timestamp":"2026-10-29T01:02:03.123456Z","fightId":123,"fightCardId":45,
            "currentRound":2,"maxRounds":3,"currentRoundTime":"04:12",
            "fighters":{"red":{"fighterId":1,"ufcFighterId":"red-id"},"blue":{"fighterId":2,"ufcFighterId":"blue-id"}},
            "fightStats":{"red":{"TIP":{"backControlTime":"00:05","mountControlTime":"00:12.500","groundTime":"01:10"}},
                          "blue":{"TIP":{"groundTime":"01:10","sideControlTime":"00:03"}}},
            "roundStats":{"1":{"red":{"TIP":{"backControlTime":"00:05"}},"blue":{"TIP":{}}}}}


def configured(tmp_path,store=None,stream="livestats",callback=None,connect=None):
    auth=tmp_path/"contracted-auth.json"
    auth.write_text(json.dumps({"contract-specific-name":"secret-test-token-only"}))
    cfg=DDEConfig(enabled=True,license_reference="unit-test-license",fight_id=123,stream=stream,auth_packet_file=auth)
    return SportradarDDERecorder(store or Store(":memory:"),cfg,callback,connect=connect)


def test_clock_and_duration_units_are_explicit():
    assert duration_seconds("25:00")==1500
    assert duration_seconds("01:02:03.125")==D("3723.125")
    assert duration_seconds("00:00.000000001")==D(".000000001")
    assert iso_timestamp_ms("2026-10-29T01:02:03.123456Z")==iso_timestamp_ms("2026-10-29T03:02:03.123999+02:00")
    for value in ("5","00:60","01:60:00",5,"-1:00"):
        with pytest.raises(ValueError):duration_seconds(value)
    with pytest.raises(ValueError):iso_timestamp_ms("2026-10-29T01:02:03")


def test_tip_is_cumulative_and_does_not_guess_current_position():
    normalized=normalize_position_packet(packet(),received_utc_ns=1000000000,received_mono_ns=999,license_reference="test-license")
    assert normalized.red.seconds.back_control==5
    assert normalized.red.seconds.mount_control==D("12.500")
    assert normalized.red.seconds.guard_control is None
    assert normalized.clock_remaining_seconds==252
    assert normalized.timestamp_basis=="provider_packet_generated"
    assert normalized.cumulative and not normalized.instantaneous_position_known
    assert normalized.round_positions[1]["red"].seconds.back_control==5
    assert "current_position" not in normalized.model_dump()
    with pytest.raises(ValueError):normalize_position_packet(packet()|{"currentRoundTime":"06:00"},received_utc_ns=1,received_mono_ns=1,license_reference="test")


async def test_raw_before_parse_cumulative_duplicates_and_resume(tmp_path):
    store=Store(":memory:");accepted=[]
    recorder=configured(tmp_path,store,callback=accepted.append)
    with pytest.raises(ValueError):await recorder.accept_frame(b'{ invalid-json',received_utc_ns=1,received_mono_ns=2)
    raw=store.db.execute("SELECT raw_b64 FROM events ORDER BY id LIMIT 1").fetchone()[0]
    assert base64.b64decode(raw)==b'{ invalid-json'
    await recorder.accept_frame(json.dumps(packet()),received_utc_ns=3,received_mono_ns=4)
    assert await recorder.accept_frame(json.dumps(packet()),received_utc_ns=5,received_mono_ns=6) is None
    assert len(accepted)==1 and recorder.last_seq==1
    next_packet=packet(2)
    await recorder.accept_frame(json.dumps(next_packet),received_utc_ns=7,received_mono_ns=8)
    assert accepted[-1].position_snapshot.red.seconds.back_control==5  # not summed to 10
    restored=configured(tmp_path,store)
    assert restored.url.endswith('/123/livestats?startPosition=3')


async def test_gap_blocks_callback_and_requests_missing_contiguous_sequence(tmp_path):
    seen=[];recorder=configured(tmp_path,callback=seen.append)
    await recorder.accept_frame(json.dumps(packet()))
    with pytest.raises(DDESequenceGap) as error:await recorder.accept_frame(json.dumps(packet(3)))
    assert error.value.expected==2 and error.value.received==3
    assert recorder.last_seq==1 and recorder.health['stale'] and len(seen)==1
    assert recorder.url.endswith('startPosition=2')
    await recorder.accept_frame(json.dumps(packet(2)))
    assert not recorder.health['stale']


async def test_details_keep_unverified_source_semantics(tmp_path):
    recorder=configured(tmp_path,stream='details')
    raw={"seqNum":1,"timestamp":"2026-10-29T01:02:03Z","eventElementType":"provider-contract-example",
         "roundNum":1,"roundTime":"02:15","stats":{"Fight":{"red":{"knockdown":1}}}}
    observed=await recorder.accept_frame(json.dumps(raw))
    assert observed.event_element_type_raw=='provider-contract-example'
    assert observed.position_snapshot is None
    assert observed.timestamp_basis=='provider_timestamp_semantics_unverified'
    saved=json.loads(recorder.store.db.execute("SELECT payload FROM events WHERE channel='fight_details'").fetchone()[0])
    assert not saved['event_occurrence_time_verified']


def test_disabled_and_missing_license_or_auth_never_connect(tmp_path):
    with pytest.raises(ValueError,match='disabled'):SportradarDDERecorder(Store(':memory:'),DDEConfig(fight_id=123))
    with pytest.raises(ValueError,match='LICENSE'):SportradarDDERecorder(Store(':memory:'),DDEConfig(fight_id=123,enabled=True))
    with pytest.raises(ValueError,match='auth-packet'):SportradarDDERecorder(Store(':memory:'),DDEConfig(fight_id=123,enabled=True,license_reference='declared-license'))


async def test_auth_not_journaled_and_echo_secret_blocked(tmp_path):
    recorder=configured(tmp_path)
    with pytest.raises(ValueError,match='credential'):
        await recorder.accept_frame('{"echo":"secret-test-token-only"}')
    for row in recorder.store.db.execute('SELECT payload,raw_b64 FROM events'):
        assert 'secret-test-token-only' not in row[0]
        assert b'secret-test-token-only' not in base64.b64decode(row[1])


async def test_reconnection_uses_durable_cursor_and_opaque_auth_packet(tmp_path,monkeypatch):
    urls=[];sent=[];connections=0
    class Socket:
        def __init__(self,which):self.which,self.done=which,False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):sent.append(json.loads(value))
        def __aiter__(self):return self
        async def __anext__(self):
            if self.which==2:raise asyncio.CancelledError()
            if not self.done:self.done=True;return json.dumps(packet())
            raise OSError('synthetic disconnect')
    def connect(url,**kwargs):
        nonlocal connections
        urls.append(url);connections+=1
        assert kwargs['logger'].disabled
        return Socket(connections)
    async def immediate(*args):pass
    monkeypatch.setattr(asyncio,'sleep',immediate)
    recorder=configured(tmp_path,connect=connect)
    with pytest.raises(asyncio.CancelledError):await recorder.run()
    assert urls[0].endswith('startPosition=1') and urls[1].endswith('startPosition=2')
    assert sent==[{"contract-specific-name":"secret-test-token-only"}]*2
    assert recorder.health['stale']
