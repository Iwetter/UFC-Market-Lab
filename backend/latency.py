"""Conservative event/market timing research, independent of order execution.

The physical occurrence clock is never inferred from a provider packet timestamp.
Reaction intervals describe price associations; they do not establish causality.
"""
from __future__ import annotations

import json
import math
import sqlite3
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StudyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1,max_length=200)
    bout_id: str = Field(min_length=1,max_length=200)
    card_id: str = Field(min_length=1,max_length=200)
    ticker: str = Field(min_length=1,max_length=200)
    event_kind: str = Field(min_length=1,max_length=100)
    source: str = Field(min_length=1,max_length=100)
    received_ms: int = Field(ge=0)
    provider_generated_ms: int | None = Field(default=None,ge=0)
    physical_start_ms: int | None = Field(default=None,ge=0)
    physical_end_ms: int | None = Field(default=None,ge=0)
    physical_time_source: str | None = Field(default=None,max_length=300)
    physical_clock_verified: bool = False
    broadcast_observed_ms: int | None = Field(default=None,ge=0)
    clock_uncertainty_ms: int = Field(default=0,ge=0)
    mapping_confirmed: bool = False
    direction: Literal["up","down","either"] = "either"
    direction_basis: str | None = Field(default=None,max_length=300)
    is_mock: bool = False

    @model_validator(mode="after")
    def valid_clocks(self):
        if (self.physical_start_ms is None) != (self.physical_end_ms is None):
            raise ValueError("physical occurrence needs both uncertainty bounds")
        if self.physical_start_ms is not None:
            if self.physical_end_ms < self.physical_start_ms:
                raise ValueError("physical interval reversed")
            if not self.physical_time_source:
                raise ValueError("physical timing requires an identified source")
        if self.direction != "either" and not self.direction_basis:
            raise ValueError("direction needs a documented event/YES mapping basis")
        upper=self.received_ms+self.clock_uncertainty_ms
        if any(value is not None and value>upper for value in
               (self.physical_end_ms,self.provider_generated_ms,self.broadcast_observed_ms)):
            raise ValueError("event/source/observation time is ahead of receipt beyond stated clock uncertainty")
        return self

    def reference_ms(self):
        if self.physical_start_ms is not None:
            return (self.physical_start_ms+self.physical_end_ms)//2
        if self.broadcast_observed_ms is not None:
            return self.broadcast_observed_ms
        return self.provider_generated_ms if self.provider_generated_ms is not None else self.received_ms


class QuoteObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    ticker: str
    received_ms: int = Field(ge=0)
    yes_bid: Decimal = Field(ge=0,le=1)
    yes_ask: Decimal = Field(ge=0,le=1)
    bid_depth: Decimal = Field(ge=0)
    ask_depth: Decimal = Field(ge=0)
    stale: bool = False
    market_status: str | None = None
    source: str = "kalshi"
    source_protocol: Literal["rest","websocket","unknown"] = "unknown"
    exchange_ts_ms: int | None = Field(default=None,ge=0)
    sending_ts_ms: int | None = Field(default=None,ge=0)
    clock_uncertainty_ms: int = Field(default=0,ge=0)
    is_mock: bool = False

    @model_validator(mode="after")
    def noncrossed(self):
        if self.yes_bid >= self.yes_ask:
            raise ValueError("locked/crossed book is not a latency observation")
        return self

    @property
    def midpoint(self):
        return float((self.yes_bid+self.yes_ask)/2)


class StudyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_mocks: bool = False
    search_before_ms: int = Field(default=15000,ge=0,le=120000)
    search_after_ms: int = Field(default=30000,gt=0,le=300000)
    baseline_window_ms: int = Field(default=10000,gt=0)
    threshold_probability: float = Field(default=.03,gt=0,lt=1,allow_inf_nan=False)
    baseline_max_range: float = Field(default=.03,ge=0,lt=1,allow_inf_nan=False)
    min_baseline_quotes: int = Field(default=3,ge=1)
    min_persistence_quotes: int = Field(default=2,ge=1)
    persistence_ms: int = Field(default=500,ge=0)
    max_quote_gap_ms: int = Field(default=4000,gt=0)
    max_spread: Decimal = Field(default=Decimal(".15"),gt=0,lt=1)
    min_touch_depth: Decimal = Field(default=Decimal("1"),gt=0)
    exclude_overlaps: bool = True
    min_independent_clusters: int = Field(default=20,ge=2)
    bootstrap_samples: int = Field(default=1000,ge=20,le=10000)
    bootstrap_seed: int = 42
    hypothesis_ms: int = Field(default=12000,ge=0)
    hypothesis_tolerance_ms: int = Field(default=2000,ge=0)


def _eligible_quote(q: QuoteObservation, cfg: StudyConfig):
    return (not q.stale and q.market_status == "active" and (cfg.include_mocks or not q.is_mock)
            and q.yes_ask-q.yes_bid <= cfg.max_spread
            and min(q.bid_depth,q.ask_depth) >= cfg.min_touch_depth)


def _interval(reaction_lower, reaction_upper, event_lower, event_upper, uncertainty=0):
    return [int(reaction_lower-event_upper-uncertainty),int(reaction_upper-event_lower+uncertainty)]


def _detect(event, events, quotes, cfg, event_times, quote_times):
    result={"event_id":event.id,"bout_id":event.bout_id,"card_id":event.card_id,"ticker":event.ticker,
            "kind":event.event_kind,"source":event.source,"is_mock":event.is_mock,
            "status":"excluded","reason":None,"physical_delay_ms":None,"available_lead_ms":None,
            "provider_generation_delay_ms":None,"broadcast_relative_delay_ms":None}
    if event.is_mock and not cfg.include_mocks:
        result["reason"]="mock_excluded";return result
    if not event.mapping_confirmed:
        result["reason"]="mapping_unconfirmed";return result
    ref=event.reference_ms()
    left,right=ref-cfg.search_before_ms,ref+cfg.search_after_ms
    overlap_range=range(bisect_left(event_times,left),bisect_right(event_times,right))
    if cfg.exclude_overlaps and any(events[i].id != event.id for i in overlap_range if cfg.include_mocks or not events[i].is_mock):
        result["reason"]="overlapping_event_window";return result
    all_quotes=quotes[bisect_left(quote_times,left-cfg.baseline_window_ms):bisect_right(quote_times,right)]
    # Losing an unhealthy observation is itself a coverage gap; do not bridge it
    # with an apparently clean before/after pair.
    valid=[q for q in all_quotes if _eligible_quote(q,cfg)]
    baseline=[q for q in valid if q.received_ms < left]
    if len(baseline)<cfg.min_baseline_quotes:
        result["reason"]="insufficient_pre_event_baseline";return result
    if max(q.midpoint for q in baseline)-min(q.midpoint for q in baseline)>cfg.baseline_max_range:
        result["reason"]="unstable_baseline";return result
    baseline_value=median(q.midpoint for q in baseline)
    result["baseline_midpoint"]=baseline_value
    def crossed(q):
        change=q.midpoint-baseline_value
        return change>=cfg.threshold_probability if event.direction == "up" else -change>=cfg.threshold_probability if event.direction == "down" else abs(change)>=cfg.threshold_probability
    last_below=baseline[-1]
    if crossed(last_below):
        result["reason"]="baseline_already_displaced";return result
    observations=[q for q in all_quotes if q.received_ms >= left]
    consecutive=[]
    for q in observations:
        if not _eligible_quote(q,cfg):
            result["reason"]="unhealthy_quote_in_search_window";return result
        previous=consecutive[-1] if consecutive else last_below
        if previous is not None and q.received_ms-previous.received_ms>cfg.max_quote_gap_ms:
            # No single point estimate is manufactured across a blind window.
            result["reason"]="quote_sampling_gap";return result
        if not crossed(q):
            last_below=q;consecutive=[]
            continue
        if last_below is None:
            continue
        if consecutive and (q.midpoint-baseline_value)*(consecutive[0].midpoint-baseline_value)<0:
            result["reason"]="oscillating_displacement";return result
        consecutive.append(q)
        if len(consecutive)<cfg.min_persistence_quotes or q.received_ms-consecutive[0].received_ms<cfg.persistence_ms:
            continue
        first=consecutive[0]
        lower,upper=last_below.received_ms,first.received_ms
        uncertainty=event.clock_uncertainty_ms+max(last_below.clock_uncertainty_ms,first.clock_uncertainty_ms)
        result.update(status="detected",reason=None,reaction_interval_ms=[lower,upper],
                      detection_confirmed_ms=q.received_ms,midpoint_change=first.midpoint-baseline_value,
                      reaction_resolution_ms=upper-lower,physical_timing_verified=event.physical_clock_verified,
                      association_only=True,first_displaced_quote_exchange_ts_ms=first.exchange_ts_ms,
                      first_displaced_quote_sending_ts_ms=first.sending_ts_ms,
                      exchange_to_receive_ms=first.received_ms-first.exchange_ts_ms if first.exchange_ts_ms is not None else None,
                      queue_to_receive_ms=first.received_ms-first.sending_ts_ms if first.sending_ts_ms is not None else None)
        result["available_lead_ms"]=_interval(lower,upper,event.received_ms,event.received_ms,uncertainty)
        if event.provider_generated_ms is not None:
            result["provider_generation_delay_ms"]=_interval(lower,upper,event.provider_generated_ms,event.provider_generated_ms,uncertainty)
        if event.broadcast_observed_ms is not None:
            result["broadcast_relative_delay_ms"]=_interval(lower,upper,event.broadcast_observed_ms,event.broadcast_observed_ms,uncertainty)
        if event.physical_start_ms is not None and event.physical_clock_verified:
            result["physical_delay_ms"]=_interval(lower,upper,event.physical_start_ms,event.physical_end_ms,uncertainty)
        return result
    result["status"]="window_censored" if observations and observations[-1].received_ms >= right-cfg.max_quote_gap_ms else "excluded"
    result["reason"]="no_sustained_reaction_in_window" if result["status"] == "window_censored" else "insufficient_post_event_coverage"
    return result


def _summary_interval(results,field,cfg):
    values=[r for r in results if r["status"] == "detected" and r.get(field) is not None]
    if field=="physical_delay_ms":values=[r for r in values if not r["is_mock"]]
    if not values:return {"n":0,"independent_clusters":0,"median_interval_ms":None,"cluster_bootstrap_95pct_interval_ms":None}
    # Cards are the coarsest available dependence unit; a singleton card_id falls
    # back to the bout only when that identifier is explicitly absent upstream.
    clusters={}
    for r in values:clusters.setdefault(r["card_id"] or r["bout_id"],[]).append(r[field])
    low=median(r[field][0] for r in values);high=median(r[field][1] for r in values)
    keys=sorted(clusters);rng=np.random.default_rng(cfg.bootstrap_seed)
    bounds=[]
    for _ in range(cfg.bootstrap_samples):
        sample=[interval for key in rng.choice(keys,len(keys),replace=True) for interval in clusters[key]]
        bounds.append((median(i[0] for i in sample),median(i[1] for i in sample)))
    ci=[float(np.quantile([v[0] for v in bounds],.025)),float(np.quantile([v[1] for v in bounds],.975))]
    return {"n":len(values),"independent_clusters":len(keys),"median_interval_ms":[low,high],
            "cluster_bootstrap_95pct_interval_ms":ci,"cluster_unit":"card",
            "population":"detected episodes passing known-confound and coverage checks only; censoring/selection limits generalization"}


def _hypothesis(summary,cfg,insufficient,meaning):
    ci=summary["cluster_bootstrap_95pct_interval_ms"]
    lower,upper=cfg.hypothesis_ms-cfg.hypothesis_tolerance_ms,cfg.hypothesis_ms+cfg.hypothesis_tolerance_ms
    status=insufficient
    if summary["independent_clusters"]>=cfg.min_independent_clusters:
        status="supported_within_tolerance" if ci[0]>=lower and ci[1]<=upper else "inconsistent_with_tolerance" if ci[1]<lower or ci[0]>upper else "inconclusive"
    return {"target_ms":cfg.hypothesis_ms,"tolerance_ms":cfg.hypothesis_tolerance_ms,"status":status,
            "required_independent_cards":cfg.min_independent_clusters,"observed_independent_cards":summary["independent_clusters"],
            "detected_real_episodes":summary["n"],"confidence_interval_ms":ci,"meaning":meaning}


def analyze_latency(events,quotes,config=None):
    """Return auditable observations plus a predeclared 12-second hypothesis test.

    Inputs may be typed objects or dictionaries. Empty inputs are valid and
    return insufficient evidence, never a zero-delay estimate.
    """
    cfg=config if isinstance(config,StudyConfig) else StudyConfig.model_validate(config or {})
    events=[e if isinstance(e,StudyEvent) else StudyEvent.model_validate(e) for e in events]
    quotes=[q if isinstance(q,QuoteObservation) else QuoteObservation.model_validate(q) for q in quotes]
    if len({e.id for e in events}) != len(events):raise ValueError("duplicate study event IDs")
    # Index once rather than scanning an entire card's capture for every packet.
    # Mock events cannot alter overlap detection for real observations.
    by_bout=defaultdict(list);by_market=defaultdict(list)
    for e in events:by_bout[e.bout_id].append(e)
    for q in quotes:by_market[(q.ticker,q.is_mock)].append(q)
    for group in by_bout.values():group.sort(key=lambda e:(e.reference_ms(),e.id))
    for group in by_market.values():group.sort(key=lambda q:(q.received_ms,q.id))
    event_times={key:[e.reference_ms() for e in group] for key,group in by_bout.items()}
    quote_times={key:[q.received_ms for q in group] for key,group in by_market.items()}
    rows=[_detect(e,by_bout[e.bout_id],by_market.get((e.ticker,e.is_mock),[]),cfg,
                  event_times[e.bout_id],quote_times.get((e.ticker,e.is_mock),[])) for e in events]
    summaries={name:_summary_interval(rows,name,cfg) for name in ("physical_delay_ms","available_lead_ms","provider_generation_delay_ms","broadcast_relative_delay_ms")}
    physical=summaries["physical_delay_ms"]
    # Even a demonstration that explicitly includes mock observations can never
    # substantiate either real-world latency hypothesis.
    real_broadcast=_summary_interval([r for r in rows if not r["is_mock"]],"broadcast_relative_delay_ms",cfg)
    counts=Counter(r["status"] for r in rows)
    return {"schema_version":1,"config":cfg.model_dump(mode="json"),"events_supplied":len(events),
            "quotes_supplied":len(quotes),"status_counts":dict(counts),"reason_counts":dict(Counter(r["reason"] for r in rows if r["reason"])),
            "summaries":summaries,
            "hypothesis":_hypothesis(physical,cfg,"insufficient_physical_timing","Physical occurrence to locally observed first sustained price displacement; not proof of a causal reaction or tradable edge."),
            "broadcast_hypothesis":_hypothesis(real_broadcast,cfg,"insufficient_broadcast_observations","Broadcast-observed event to locally observed first sustained executable-quote displacement. This tests an observation-relative price association, not physical fight-to-market delay or tradable edge."),
            "observations":rows,"warnings":["A provider packet timestamp is generation time, not physical event time.",
                "A broadcast-relative delay includes the broadcast path and must not be treated as physical market latency.",
                "Quote arrival intervals include polling/network uncertainty; source-time diagnostics are separate.",
                "No price reaction is attributed causally by this detector. Overlapping events and sparse coverage are excluded.",
                "No displacement in the observed window can mean the market moved before that window; it does not imply delay exceeds the positive search horizon.",
                "Positive available lead means the observed displacement followed feed availability; negative lead means the market was already moving."]}


def _capture_snapshot(database,session,max_records,start_ms=None,end_ms=None):
    """Bound input rows and copy one consistent WAL read snapshot, then release it."""
    if max_records is not None and (isinstance(max_records,bool) or not isinstance(max_records,int) or max_records<1):
        raise ValueError("max_records must be a positive integer or None")
    for name,value in (("start_ms",start_ms),("end_ms",end_ms)):
        if value is not None and (isinstance(value,bool) or not isinstance(value,int) or not 0<=value<(2**63-1)//1_000_000):
            raise ValueError(f"{name} must be a nonnegative supported UTC millisecond integer or None")
    if start_ms is not None and end_ms is not None and start_ms>end_ms:
        raise ValueError("receive-time window is reversed")
    own=not isinstance(database,sqlite3.Connection)
    db=sqlite3.connect(f"file:{Path(database).resolve().as_posix()}?mode=ro",uri=True,isolation_level=None) if own else database
    opened_transaction=False
    try:
        if not db.in_transaction:
            db.execute("BEGIN");opened_transaction=True
        if not db.execute("PRAGMA table_info(events)").fetchone():raise ValueError("event journal table missing")
        channels=("mapping_review","market","market_lifecycle_v2","latency_annotation","fight","book")
        where="channel IN ("+",".join("?" for _ in channels)+")"
        params=list(channels)
        if session:
            where+=" AND host_session=?";params.append(session)
        if start_ms is not None:
            where+=" AND recv_utc_ns>=?";params.append(start_ms*1_000_000)
        if end_ms is not None:
            where+=" AND recv_utc_ns<?";params.append((end_ms+1)*1_000_000)
        sessions=db.execute(f"SELECT DISTINCT host_session FROM events WHERE {where} LIMIT 2",params).fetchall()
        if len(sessions)>1:
            raise ValueError("latency study requires an explicit session; multiple recording sessions cannot be merged without verified clock synchronization")
        count=db.execute(f"SELECT count(*) FROM events WHERE {where}",params).fetchone()[0]
        if max_records is not None and count>max_records:
            raise ValueError(f"latency journal exceeds max_records={max_records}; select a smaller recording session or use the offline CLI with a higher limit")
        # Raw frames, raw_b64, full REST responses and execution projections are
        # deliberately not selected or materialized for this event study.
        columns=("id","source","channel","recv_utc_ns","host_session","payload")
        records=[dict(zip(columns,r)) for r in db.execute(f"SELECT {','.join(columns)} FROM events WHERE {where} ORDER BY id",params)]
        metadata=[]
        if records:
            # Carry only the most recent normalized metadata per ticker. Reading
            # every earlier discovery response would grow without bound.
            query="""SELECT channel,payload FROM (
              SELECT channel,payload,row_number() OVER (
                PARTITION BY channel,json_extract(payload,'$.ticker') ORDER BY id DESC) AS version
              FROM events WHERE id<? AND channel IN ('mapping_review','market') AND json_valid(payload))
              WHERE version=1"""
            values=[records[0]["id"]]
            if max_records is not None:
                query+=" LIMIT ?";values.append(max_records-count+1)
            metadata=list(db.execute(query,values))
            if max_records is not None and count+len(metadata)>max_records:
                raise ValueError(f"latency session plus required prior metadata exceeds max_records={max_records}; use the offline CLI with a higher limit")
        return records,metadata
    finally:
        if opened_transaction and db.in_transaction:db.execute("ROLLBACK")
        if own:db.close()


def read_journal(database,session=None,max_records=None,start_ms=None,end_ms=None):
    """Read existing WAL-safe journal, preferring explicit timing annotations.

    The existing canonical fight stream lacks a physical occurrence clock. It is
    eligible for generation/availability diagnostics only. No guessed 12s shift.
    """
    records,prior_metadata=_capture_snapshot(database,session,max_records,start_ms,end_ms)
    events=[];quotes=[];warnings=[];mappings={};canonical=[];market_states={}
    # Prior session mappings and market metadata are state, not episode data.
    # Carry these forward without merging any prior quote/event clocks.
    if records:
        for metadata in prior_metadata:
            try:
                value=json.loads(metadata[1])
                if metadata[0]=="mapping_review":mappings[value["ticker"]]=value
                else:market_states[value["ticker"]]=value.get("status")
            except (ValueError,KeyError,TypeError):pass
    for row in records:
        try:payload=json.loads(row["payload"])
        except (ValueError,TypeError):warnings.append(f"invalid JSON at journal ID {row['id']}");continue
        source=row["source"];channel=row["channel"];ts=row["recv_utc_ns"]//1_000_000
        if channel=="mapping_review":mappings[payload["ticker"]]=payload
        elif channel=="market":market_states[payload["ticker"]]=payload.get("status")
        elif channel=="market_lifecycle_v2":
            message=payload.get("msg",payload)
            statuses={"activated":"active","deactivated":"inactive","determined":"determined","settled":"finalized"}
            if message.get("event_type") in statuses and message.get("market_ticker"):
                market_states[message["market_ticker"]]=statuses[message["event_type"]]
        elif channel=="latency_annotation":
            try:events.append(StudyEvent.model_validate(payload))
            except ValueError:warnings.append(f"invalid timing annotation at journal ID {row['id']}")
        elif channel=="fight":
            # Packet-level generation/receive association is explicitly distinct
            # from a tagged knockdown or other physical event.
            for mapping in mappings.values():
                if mapping.get("bout_id") != payload.get("bout_id"):continue
                try:
                    canonical.append(StudyEvent(id=f"journal:{row['id']}:{mapping['ticker']}",bout_id=payload["bout_id"],card_id=mapping.get("card_id") or payload["bout_id"],ticker=mapping["ticker"],event_kind=f"packet:{payload.get('kind','unknown')}",source=source,received_ms=ts,
                        provider_generated_ms=payload.get("source_ts_ms"),mapping_confirmed=bool(mapping.get("confirmed")),is_mock=source=="mock"))
                except ValueError:warnings.append(f"invalid canonical event clock at journal ID {row['id']}")
        elif channel=="book":
            try:
                yes=payload.get("yes",[]);no=payload.get("no",[])
                bid=payload.get("yes_bid");ask=payload.get("yes_ask")
                if bid is None or ask is None:continue
                bid_depth=max((Decimal(q) for p,q in yes if Decimal(p)==Decimal(bid)),default=Decimal(0))
                no_bid=Decimal(1)-Decimal(ask)
                ask_depth=max((Decimal(q) for p,q in no if Decimal(p)==no_bid),default=Decimal(0))
                status=market_states.get(payload["ticker"])
                # Either source saying inactive/unknown is enough to disqualify.
                # A fresh normalized final status must not be overwritten by an
                # older active discovery record retained at the window boundary.
                if status is None or status=="active":status=payload.get("market_status",status)
                quotes.append(QuoteObservation(id=str(row["id"]),ticker=payload["ticker"],received_ms=ts,yes_bid=bid,yes_ask=ask,bid_depth=bid_depth,ask_depth=ask_depth,stale=payload.get("stale",True),market_status=status,source=source,is_mock=source=="mock",
                    source_protocol=payload.get("source_protocol","unknown"),exchange_ts_ms=payload.get("exchange_ts_ms"),sending_ts_ms=payload.get("sending_ts_ms"),clock_uncertainty_ms=payload.get("clock_uncertainty_ms",0)))
            except (ValueError,TypeError,ArithmeticError):warnings.append(f"invalid/empty book at journal ID {row['id']}")
    # Annotations replace dense stats/heartbeat packet associations for a bout,
    # while discrete finishes/pauses/round boundaries remain overlap evidence.
    annotated={(e.bout_id,e.ticker) for e in events}
    dense={"packet:stats","packet:heartbeat"}
    events.extend(e for e in canonical if (e.bout_id,e.ticker) not in annotated or e.event_kind not in dense)
    if canonical:warnings.append("Canonical fight source_ts_ms is provider-generation time; physical delay remains unmeasured without a verified annotation.")
    return events,quotes,warnings


def latency_stress_scenarios():
    return [{"name":f"fight-feed-plus-{s}s","additional_feed_delay_ms":s*1000,
             "measured":False,"purpose":"Sensitivity assumption, not an observed latency"} for s in (0,5,12,20)]
