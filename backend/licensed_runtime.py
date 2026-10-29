"""Opt-in licensed collectors share the market recorder's journal and clocks.

No unknown vendor event name is guessed and no packet timestamp is promoted
to a verified physical occurrence time. Exact event mappings need the operator's
contracted schema; cumulative position snapshots remain separate observations.
"""
import json
import os
from pathlib import Path

from .latency import StudyEvent
from .sportradar import DDEConfig,SportradarDDERecorder


def configured_recorders(engine):
    if os.getenv("UFC_DDE_ENABLED")!="1":return []
    if engine.cfg.source!="kalshi":raise ValueError("licensed feeds require the isolated real-market database")
    values=[v.strip() for v in os.getenv("UFC_DDE_FIGHT_IDS","").split(',') if v.strip()]
    if not values or len(values)>50 or any(not v.isdigit() or int(v)<1 for v in values):
        raise ValueError("UFC_DDE_FIGHT_IDS must identify 1–50 licensed provider fights")
    if len(set(values))!=len(values):raise ValueError("duplicate licensed fight IDs")
    mapping_file=os.getenv("UFC_DDE_EVENT_MAP_FILE")
    event_map=json.loads(Path(mapping_file).read_text()) if mapping_file else {}
    if not isinstance(event_map,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in event_map.items()):
        raise ValueError("licensed event map must contain explicit raw-name to event-kind strings")
    allowed={"start","round_start","round_end","pause","resume","knockdown","takedown","position_change","finish"}
    if set(event_map.values())-allowed:raise ValueError("unsupported normalized licensed event kind")
    streams=[v.strip() for v in os.getenv("UFC_DDE_STREAMS","details,livestats").split(',')]
    if not streams or len(set(streams))!=len(streams) or set(streams)-{"details","livestats"}:raise ValueError("invalid licensed streams")
    result=[]
    engine.health["licensed_feeds"]={}
    for fight_id in values:
        async def observe(observation,provider_id=fight_id):
            kind=event_map.get(observation.event_element_type_raw)
            if kind is None:return
            # Mapping convention deliberately binds provider identity, not names.
            for ticker,mapping in sorted(engine.mappings.items()):
                if mapping["bout_id"]!=f"sportradar:{provider_id}":continue
                event=StudyEvent(id=f"dde:{provider_id}:{observation.stream}:{observation.seq}:{ticker}",
                    bout_id=mapping["bout_id"],card_id=mapping["card_id"],ticker=ticker,event_kind=kind,
                    source="sportradar_dde_details",received_ms=observation.received_utc_ns//1_000_000,
                    mapping_confirmed=bool(mapping["confirmed"]),direction="either")
                # Details timestamp semantics are unverified; only availability
                # lead is measurable until a licensed timing contract establishes more.
                key="dde_study_event:"+event.id
                if not engine.store.get(key):
                    with engine.store.transaction():
                        engine.store.append("sportradar_dde","latency_annotation",event,recv_utc_ns=observation.received_utc_ns,recv_mono_ns=observation.received_mono_ns)
                        engine.store.put(key,True)
                break  # One studied YES mapping per physical bout event, not mirror-contract duplicates.
        for stream in streams:
            recorder=SportradarDDERecorder(engine.store,DDEConfig.from_env(int(fight_id),stream),on_observation=observe)
            engine.health["licensed_feeds"][f"{fight_id}:{stream}"]=recorder.health
            result.append(recorder)
    return result
