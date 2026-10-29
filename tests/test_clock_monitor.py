"""Deterministic synthetic NTP packets, not captured public-time measurements."""
import asyncio
import struct
from types import SimpleNamespace

import pytest

from backend import clock_monitor as clock
from backend.store import Store

BASE = 1_793_232_000_000_000_000


def synthetic_packet(offset_ns=10_000_000, path_ns=10_000_000, processing_ns=1_000_000):
    request = bytearray(48)
    request[0] = 0x23
    request[40:48] = clock._stamp(BASE)
    response = bytearray(48)
    response[0] = 0x24
    response[1] = 2
    response[3] = 236  # Signed precision -20.
    response[8:12] = struct.pack("!I", 65)  # About 1ms root dispersion.
    response[16:24] = clock._stamp(BASE-1_000_000_000+offset_ns)
    response[24:32] = request[40:48]
    response[32:40] = clock._stamp(BASE+path_ns+offset_ns)
    response[40:48] = clock._stamp(BASE+path_ns+offset_ns+processing_ns)
    elapsed = path_ns*2+processing_ns
    return bytes(request), bytes(response), BASE, BASE+elapsed, elapsed


def test_synthetic_four_timestamp_offset_delay_and_uncertainty():
    result = clock.parse_ntp_response(*synthetic_packet())
    assert result["offset_ms"] == pytest.approx(10, abs=.000002)
    assert result["roundtrip_ms"] == pytest.approx(20, abs=.000002)
    assert 10.9 < result["uncertainty_ms"] < 11.1
    assert result["authenticated"] is False and result["paper_only"] is True


def test_actual_large_offset_is_reported_without_clamping():
    result = clock.parse_ntp_response(*synthetic_packet(offset_ns=100_000_000))
    assert result["offset_ms"] == pytest.approx(100, abs=.000002)


@pytest.mark.parametrize("field,value", [(0, 0xE4), (0, 0x23), (0, 0x14), (1, 0), (1, 16), (24, 1)])
def test_invalid_server_flags_stratum_and_originate_rejected(field, value):
    parts = list(synthetic_packet())
    response = bytearray(parts[1]); response[field] = value; parts[1] = bytes(response)
    with pytest.raises(ValueError): clock.parse_ntp_response(*parts)


@pytest.mark.parametrize("mutation", ["short", "zero", "reversed", "negative", "slow", "wall_step", "dispersion"])
def test_invalid_timestamps_delay_and_dispersion_rejected(mutation):
    parts = list(synthetic_packet())
    response = bytearray(parts[1])
    if mutation == "short": response = response[:-1]
    elif mutation == "zero": response[32:40] = bytes(8)
    elif mutation == "reversed": response[40:48] = clock._stamp(BASE-1_000_000_000)
    elif mutation == "negative": response[40:48] = clock._stamp(BASE+100_000_000)
    elif mutation == "slow": parts[3] += 100_000_000; parts[4] += 100_000_000
    elif mutation == "wall_step": parts[3] += 5_000_000
    elif mutation == "dispersion": response[8:12] = struct.pack("!I", 65536)
    parts[1] = bytes(response)
    with pytest.raises(ValueError): clock.parse_ntp_response(*parts)


def engine():
    return SimpleNamespace(store=Store(":memory:"), health={}, clock_offset_ms=7., clock_observed_ms=123)


@pytest.mark.asyncio
async def test_off_environment_performs_no_network_or_measurement(monkeypatch):
    monkeypatch.setenv("UFC_NTP_HOST", "off")
    monkeypatch.setattr(clock, "sample_ntp", lambda _: pytest.fail("network forbidden"))
    subject = engine()
    await clock.monitor_clock(subject)
    assert subject.clock_observed_ms == 123
    assert subject.health["clock_monitor"]["status"] == "disabled"
    assert subject.store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    subject.store.close()


@pytest.mark.asyncio
async def test_success_persists_and_failure_does_not_refresh(monkeypatch):
    subject = engine()
    measurement = {**clock.parse_ntp_response(*synthetic_packet()), "source": "sntp:unit.invalid"}
    calls = 0
    def sample(_):
        nonlocal calls
        calls += 1
        if calls == 1: return measurement
        raise TimeoutError("sensitive network exception text")
    async def sleep(_):
        if calls == 2: raise asyncio.CancelledError
    monkeypatch.setattr(clock, "sample_ntp", sample)
    monkeypatch.setattr(clock.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError): await clock.monitor_clock(subject, "unit.invalid")
    assert subject.clock_observed_ms == measurement["measured_ms"]
    assert subject.clock_offset_ms == measurement["offset_ms"]
    assert subject.health["clock_monitor"]["error_type"] == "TimeoutError"
    assert "sensitive" not in str(subject.health)
    event = subject.store.db.execute("SELECT channel,payload FROM events").fetchone()
    assert event[0] == "clock_offset" and '"source":"sntp:unit.invalid"' in event[1]
    assert subject.store.verify()
    subject.store.close()
