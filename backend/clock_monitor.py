"""Unauthenticated public SNTP observations for PAPER clock freshness only.

This does not set the operating-system clock or authorize trading/live use.
Packets are checked against the request, but public UDP time is not authenticated.
"""
from __future__ import annotations

import asyncio
import os
import socket
import struct
import time

NTP_EPOCH = 2_208_988_800
FRACTION = 1 << 32
MAX_DELAY_MS = 50.


def _stamp(unix_ns):
    seconds, remainder = divmod(unix_ns, 1_000_000_000)
    return struct.pack("!II", (seconds+NTP_EPOCH) % FRACTION, remainder*FRACTION//1_000_000_000)


def _unpack(raw, reference_unix_ns):
    seconds, fraction = struct.unpack("!II", raw)
    if not seconds and not fraction:
        raise ValueError("zero NTP timestamp")
    reference_seconds = reference_unix_ns//1_000_000_000+NTP_EPOCH
    # Unfold the 2036 era rollover relative to the local request date.
    seconds += round((reference_seconds-seconds)/FRACTION)*FRACTION
    return (seconds-NTP_EPOCH)*1_000_000_000+fraction*1_000_000_000//FRACTION


def parse_ntp_response(request, response, sent_unix_ns, received_unix_ns, elapsed_ns):
    """Pure four-timestamp calculation; tests use explicitly synthetic packets."""
    if len(request) != 48 or len(response) != 48 or response[24:32] != request[40:48]:
        raise ValueError("invalid SNTP response or originate timestamp")
    leap, version, mode = response[0] >> 6, (response[0] >> 3) & 7, response[0] & 7
    if leap == 3 or version not in (3, 4) or mode != 4 or not 1 <= response[1] <= 15:
        raise ValueError("unsynchronized or invalid SNTP server")
    if any(type(v) is not int or v < 0 for v in (sent_unix_ns, received_unix_ns, elapsed_ns)):
        raise ValueError("invalid local sample timestamps")
    t1 = _unpack(request[40:48], sent_unix_ns)
    if abs(t1-sent_unix_ns) > 2:
        raise ValueError("request transmit timestamp mismatch")
    t2 = _unpack(response[32:40], sent_unix_ns)
    t3 = _unpack(response[40:48], sent_unix_ns)
    reference = _unpack(response[16:24], sent_unix_ns)
    wall_elapsed = received_unix_ns-sent_unix_ns
    wall_step = abs(wall_elapsed-elapsed_ns)
    if wall_elapsed < 0 or wall_step > 2_000_000 or t3 < t2 or reference > t3:
        raise ValueError("invalid SNTP timing or local clock step")
    delay_ms = (received_unix_ns-t1-(t3-t2))/1_000_000
    if delay_ms < 0 or delay_ms > MAX_DELAY_MS or elapsed_ns/1_000_000 > MAX_DELAY_MS:
        raise ValueError("negative or slow SNTP sample")
    root_delay_ms = struct.unpack("!i", response[4:8])[0]/65536*1000
    root_dispersion_ms = struct.unpack("!I", response[8:12])[0]/65536*1000
    precision_exponent = struct.unpack("!b", response[3:4])[0]
    precision_ms = 2.**precision_exponent*1000
    uncertainty_ms = delay_ms/2+max(0., root_delay_ms)/2+root_dispersion_ms+precision_ms+wall_step/1_000_000
    if uncertainty_ms > MAX_DELAY_MS:
        raise ValueError("uncertain SNTP sample")
    return {"offset_ms": ((t2-t1)+(t3-received_unix_ns))/2_000_000,
            "roundtrip_ms": delay_ms, "uncertainty_ms": uncertainty_ms,
            "measured_ms": received_unix_ns//1_000_000, "stratum": response[1],
            "authenticated": False, "paper_only": True}


def sample_ntp(host):
    """Blocking UDP exchange; caller moves it off the journal/event-loop thread."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.settimeout(3.)
        channel.connect((host, 123))  # Connected UDP filters packets from other endpoints.
        request = bytearray(48)
        request[0] = 0x23  # Version 4, client mode.
        sent_unix_ns = time.time_ns()
        sent_mono_ns = time.monotonic_ns()
        request[40:48] = _stamp(sent_unix_ns)
        channel.send(request)
        response = channel.recv(512)
        received_unix_ns = time.time_ns()
        elapsed_ns = time.monotonic_ns()-sent_mono_ns
    result = parse_ntp_response(bytes(request), response, sent_unix_ns, received_unix_ns, elapsed_ns)
    result["source"] = "sntp:"+host
    return result


async def monitor_clock(engine, host=None):
    """Observe every 30 seconds; failures never refresh the last good timestamp."""
    host = os.environ.get("UFC_NTP_HOST", "time.cloudflare.com") if host is None else host
    host = host.strip()
    if host.lower() in {"", "off", "disabled", "none"}:
        engine.health["clock_monitor"] = {"status": "disabled", "authenticated": False, "paper_only": True}
        return
    while True:
        try:
            measurement = await asyncio.to_thread(sample_ntp, host)
            # Persist first so a failed write cannot publish unjournaled freshness.
            engine.store.append("clock_monitor", "clock_offset", measurement,
                                recv_utc_ns=measurement["measured_ms"]*1_000_000)
            engine.clock_offset_ms = measurement["offset_ms"]
            engine.clock_observed_ms = measurement["measured_ms"]
            engine.clock_uncertainty_ms = measurement['uncertainty_ms']
            engine.health["clock_monitor"] = {"status": "observed", **measurement}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            engine.health["clock_monitor"] = {"status": "error", "error_type": type(exc).__name__,
                                               "authenticated": False, "paper_only": True}
        await asyncio.sleep(30)
