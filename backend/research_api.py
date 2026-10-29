"""Read-only research findings and authenticated observation capture.

Nothing here changes a trading model, approves a model, or calls a broker.
"""
import asyncio
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import Field

from .domain import StrictModel,digest,now_ms
from .latency import StudyConfig,StudyEvent,analyze_latency,latency_stress_scenarios,read_journal
from .position_evidence import BoutOutcome,PositionObservation,PositionSamplingProtocol,PositionSource,position_evidence


class PositionStudy(StrictModel):
    observations:list[PositionObservation]=Field(default_factory=list,max_length=10000)
    outcomes:list[BoutOutcome]=Field(default_factory=list,max_length=10000)
    sources:list[PositionSource]=Field(default_factory=list,max_length=100)
    protocol:PositionSamplingProtocol
    as_of_ms:int=Field(ge=0)


class AnnotationInput(StudyEvent):
    received_ms:int=Field(default_factory=now_ms,ge=0)


class ResearchFeatures(StrictModel):
    features:dict[str,float]


def _analyze_file(database,session,start_ms,end_ms):
    events,quotes,warnings=read_journal(database,session=session,max_records=25000,start_ms=start_ms,end_ms=end_ms)
    return analyze_latency(events,quotes)|{"capture_warnings":warnings}


def _evaluate_position_study(study):
    return position_evidence(study["observations"],study["outcomes"],study["sources"],study["protocol"],as_of_ms=study["as_of_ms"])


def research_router(engine,root:Path,operator):
    router=APIRouter(prefix="/api/research",tags=["research"])
    pool=ProcessPoolExecutor(max_workers=1)
    limiter=asyncio.Semaphore(1)
    router.shutdown_research=lambda:pool.shutdown(wait=True,cancel_futures=True)

    @router.get("/data")
    async def data_summary():
        path=root/"docs/data-training-report.json"
        specs={}
        for target in ("prefight","round_scoring"):
            model_path=root/"artifacts/research"/f"{target}.json"
            if model_path.exists():
                a=json.loads(model_path.read_text())
                specs[target]={k:a[k] for k in ("features","target","spec_sha256","research_only","approved_for_trading")}
        return {"historical":json.loads(path.read_text(encoding="utf-8")) if path.exists() else None,
                "research_model_specs":specs,
                "execution_approved":False,"reason":"Historical snapshot research lacks observed market and publication timestamps."}

    @router.post("/predict/{target}",dependencies=[Depends(operator)])
    async def research_predict(target:str,request:ResearchFeatures):
        if target not in {"prefight","round_scoring"}:raise HTTPException(404,"unknown research model")
        path=root/"artifacts/research"/f"{target}.json"
        if not path.exists():raise HTTPException(503,"fit or acquire the research artifact first")
        from .historical_models import predict_research
        artifact=json.loads(path.read_text())
        probability=predict_research(artifact,request.features)
        return {"probability_a":probability,"target":artifact["target"],"model_sha256":artifact["spec_sha256"],
                "research_only":True,"approved_for_trading":False,
                "warning":"Historical baseline with reconstructed availability; not a calibrated executable-market edge."}

    @router.get("/latency")
    async def latency(session:str|None=None,lookback_minutes:int=Query(default=30,ge=1,le=120)):
        sessions=[r[0] for r in engine.store.db.execute("SELECT DISTINCT host_session FROM events ORDER BY id")]
        selected=session or (engine.store.session if len(sessions)>1 else None)
        if selected is not None and selected not in sessions:raise ValueError("unknown recording session")
        if engine.cfg.database == ":memory:":raise ValueError("timing research requires a persistent capture database")
        end_ms=now_ms();start_ms=end_ms-lookback_minutes*60000
        async with limiter:
            try:
                report=await asyncio.get_running_loop().run_in_executor(pool,_analyze_file,str(Path(engine.cfg.database).resolve()),selected,start_ms,end_ms)
            except ValueError as exc:
                if "max_records" not in str(exc):raise
                report={"hypothesis":{"status":"analysis_requires_offline","required_independent_cards":20},
                        "broadcast_hypothesis":{"status":"analysis_requires_offline"},"summaries":{},
                        "capture_warnings":[str(exc)],"analysis_status":"requires_offline_analysis"}
        return report|{"stress_scenarios":latency_stress_scenarios(),
            "source":engine.cfg.source,"sessions":sessions,"selected_session":selected,
            "receive_window_ms":[start_ms,end_ms],
            "claim":"Price association study; 12 seconds is an unverified hypothesis until paired observations meet the protocol."}

    @router.post("/latency-annotations",dependencies=[Depends(operator)])
    async def annotate(event:AnnotationInput):
        ts=now_ms()
        if event.ticker not in engine.markets:raise ValueError("select a discovered market")
        mapping=engine.mappings.get(event.ticker)
        if not mapping or mapping["bout_id"] != event.bout_id or mapping["card_id"] != event.card_id:
            raise ValueError("annotation must match the reviewed bout/card mapping")
        for stamp in (event.provider_generated_ms,event.broadcast_observed_ms,event.physical_end_ms):
            if stamp is not None and stamp>ts+event.clock_uncertainty_ms:raise ValueError("future event annotations are rejected")
        if event.physical_clock_verified and (engine.clock_offset_ms is None or abs(engine.clock_offset_ms)>50 or ts-engine.clock_observed_ms>60000):
            raise ValueError("physical timing requires a fresh measured clock observation")
        # Caller cannot relabel demo inputs as real samples or assert mapping approval.
        normalized=event.model_copy(update={"received_ms":ts,"mapping_confirmed":bool(mapping["confirmed"]),"is_mock":engine.cfg.source=="mock"})
        key="latency_annotation:"+event.id
        content_hash=digest(event.model_dump(exclude={"received_ms","mapping_confirmed","is_mock"}))
        existing=engine.store.get(key)
        if existing:
            if existing["input_hash"]!=content_hash:raise ValueError("annotation ID already used for different content")
            return existing["event"]
        with engine.store.transaction():
            engine.store.append("operator","latency_annotation",normalized,source_ts_ms=event.provider_generated_ms,recv_utc_ns=ts*1_000_000)
            engine.store.put(key,{"input_hash":content_hash,"event":normalized.model_dump(mode="json")})
        return normalized

    @router.get("/positions")
    async def positions():
        saved=engine.store.get("position_study")
        if saved:return saved
        protocol={"protocol_id":"r1-150-controller-v1","description":"One confirmed MMA position per bout at round 1 with 150 seconds remaining; no future frames.","created_at_ms":engine.started_ms,"round":1,"clock_remaining_s":150}
        return position_evidence([],[],[],protocol,as_of_ms=now_ms())

    @router.post("/positions",dependencies=[Depends(operator)])
    async def positional_study(study:PositionStudy):
        if study.as_of_ms>now_ms():raise ValueError("future research cutoffs are rejected")
        async with limiter:
            result=await asyncio.get_running_loop().run_in_executor(pool,_evaluate_position_study,study.model_dump(mode="json"))
        with engine.store.transaction():
            engine.store.append("operator","position_study",study)
            engine.store.put("position_study",result)
        return result

    return router
