from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .domain import OrderIntent, Settings, StrictModel, canonical, now_ms
from .engine import Engine
from .ingest import LicensedIngest
from .models import PREFIGHT_FEATURES

ROOT = Path(__file__).resolve().parents[1]


class MappingReview(StrictModel):
    ticker: str
    bout_id: str = Field(min_length=1,max_length=200)
    card_id: str = Field(min_length=1,max_length=200)
    fighter_a: str = Field(min_length=1,max_length=100)
    fighter_b: str = Field(min_length=1,max_length=100)
    yes_is_a: bool
    rounds: Literal[3,5] = 3


class ClockObservation(StrictModel):
    offset_ms: float = Field(allow_inf_nan=False)
    measured_ms: int
    source: str = Field(min_length=1,max_length=100)
    uncertainty_ms: float = Field(default=0,ge=0,allow_inf_nan=False)


class PreFightRequest(StrictModel):
    ticker: str
    features: dict[str,float]
    available_ms: int


def create_app(cfg=None, *, background=True, model_path=None, infight_model_path=None, operations=False):
    cfg = cfg or Settings.model_validate_json((ROOT/"config/default.json").read_text())
    engine = Engine(cfg,model_path,infight_model_path)
    token = secrets.token_urlsafe(32)
    from .practice import PracticeController
    practice=PracticeController(ROOT,Path(cfg.database).resolve().parent/'practice')

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(engine.run())] if background else []
        if operations and background:
            from .operations import monitor_operations
            tasks.append(asyncio.create_task(monitor_operations(engine)))
        if tasks:
            def failed(done):
                if not done.cancelled() and done.exception() is not None:
                    engine.health["engine_fault"] = type(done.exception()).__name__
                    engine.books.invalidate()
                    try:engine.broker.kill(now_ms())
                    except Exception:engine.broker.killed=True
                    finally:
                        for other in tasks:
                            if other is not done:other.cancel()
                        callback=getattr(app.state,"on_runtime_failure",None)
                        if callback:callback()
            for task in tasks:task.add_done_callback(failed)
        if background:
            practice_task=asyncio.create_task(practice.run())
            def practice_failed(done):
                if not done.cancelled() and done.exception() is not None:
                    # Practice failure never kills or changes the market-data account.
                    practice.runtime_error=type(done.exception()).__name__
            practice_task.add_done_callback(practice_failed)
            tasks.append(practice_task)
        yield
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        practice.close()
        research.shutdown_research()
        engine.store.close()

    app = FastAPI(title="Octagon / UFC Market Lab",version="0.1.0",lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","testserver"])
    app.state.engine = engine
    app.state.practice = practice
    app.state.on_runtime_failure = None

    @app.middleware("http")
    async def security_headers(request,call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; img-src 'self' data:; frame-ancestors 'none'"
        return response

    def same_origin(origin,host):
        return origin in {f"http://{host}",f"https://{host}"}

    async def operator(request: Request, x_research_token: str = Header(default="")):
        if request.headers.get("origin") and not same_origin(request.headers["origin"],request.headers["host"]):
            raise HTTPException(403,"same-origin operator requests required")
        if not hmac.compare_digest(x_research_token,token):
            raise HTTPException(403,"local operator token required")

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse(status_code=409,content={"detail":str(exc)})

    @app.get("/api/health")
    async def health():
        return JSONResponse(status_code=503 if engine.health.get("engine_fault") else 200,content={"ok":not bool(engine.health.get("engine_fault")),"mode":"PAPER","source":cfg.source,"live_enabled":False})

    @app.get("/api/session")
    async def session(request:Request):
        if request.headers.get("origin") and not same_origin(request.headers["origin"],request.headers["host"]):
            raise HTTPException(403,"same-origin session required")
        response = JSONResponse({"token":token})
        response.set_cookie("research_session",token,httponly=True,samesite="strict")
        return response

    @app.get("/api/state")
    async def state(): return engine.snapshot()

    @app.get("/api/audit")
    async def audit():
        rows = engine.store.db.execute("SELECT id,source,channel,source_ts_ms,recv_utc_ns,seq,sid,hash FROM events ORDER BY id DESC LIMIT 100").fetchall()
        return {"chain_valid":engine.store.verify(),"events":[dict(r) for r in rows]}

    from .research_api import research_router
    research=research_router(engine,ROOT,operator)
    app.include_router(research)
    from .automation_api import automation_router
    app.include_router(automation_router(engine,ROOT,operator))
    from .practice_api import practice_router
    app.include_router(practice_router(practice,operator))

    @app.get("/metrics")
    async def metrics():
        from fastapi.responses import PlainTextResponse
        s = engine.snapshot()
        lines = [f'ufc_events_total {s["events_recorded"]}',f'ufc_sequence_gaps_total {engine.books.gaps}',f'ufc_kill_switch {int(engine.broker.killed)}']
        for m in s["markets"]:
            for metric,key in (("book_age_ms","book_age_ms"),("fight_age_ms","fight_age_ms")):
                if m[key] is not None: lines.append(f'ufc_{metric}{{ticker="{m["ticker"]}"}} {m[key]}')
        return PlainTextResponse("\n".join(lines)+"\n")

    @app.post("/api/paper/orders",dependencies=[Depends(operator)])
    async def order(intent:OrderIntent):
        return engine.submit(intent,now_ms())

    @app.post("/api/kill",dependencies=[Depends(operator)])
    async def kill():
        engine.broker.kill(now_ms()); return {"kill_switch":True}

    @app.post("/api/paper/reset-kill",dependencies=[Depends(operator)])
    async def reset_kill():
        engine.broker.killed = False; engine.store.put("killed",False)
        engine.store.append("operator","reset_kill",{"mode":"PAPER"})
        return {"kill_switch":False}

    @app.post("/api/mappings",dependencies=[Depends(operator)])
    async def mapping(review:MappingReview):
        return engine.confirm_mapping(**review.model_dump(),ts=now_ms())

    @app.post("/api/clock",dependencies=[Depends(operator)])
    async def clock(observation:ClockObservation):
        if abs(now_ms()-observation.measured_ms)>60000: raise ValueError("clock observation too old")
        engine.clock_offset_ms = observation.offset_ms
        engine.clock_observed_ms = observation.measured_ms
        engine.clock_uncertainty_ms = observation.uncertainty_ms
        engine.store.append("clock_monitor","clock_offset",observation)
        return {"accepted":True}

    @app.post("/api/models/approve",dependencies=[Depends(operator)])
    async def approve_model(request:Request):
        body = await request.json()
        if not engine.prefight.artifact or body.get("version") != engine.prefight.status["version"]:
            raise ValueError("matching trained model version required")
        engine.store.put("model_approval",{"version":body["version"],"approved_ms":now_ms(),"mode":"PAPER"})
        engine.store.append("operator","model_approval",engine.store.get("model_approval"))
        return {"approved_for":"PAPER"}

    @app.post("/api/predictions/prefight",dependencies=[Depends(operator)])
    async def predict(request:PreFightRequest):
        ts = now_ms()
        if request.available_ms > ts: raise ValueError("future features rejected")
        if set(request.features) != set(PREFIGHT_FEATURES): raise ValueError("feature registry mismatch")
        _,benchmark,fight = engine.probabilities(request.ticker)
        mapping = engine.mappings.get(request.ticker)
        if not mapping or not mapping["confirmed"] or not fight: raise ValueError("confirmed bout mapping required")
        if fight.status != "scheduled": raise ValueError("pre-fight prior frozen at fight start")
        if benchmark is None: raise ValueError("market benchmark unavailable")
        p = engine.prefight.predict(benchmark,request.features,as_of_ms=ts)
        fight.prior = p if mapping["yes_is_a"] else 1-p
        engine.store.append("core","prior",{"bout_id":fight.bout_id,"p_a":fight.prior},recv_utc_ns=ts*1_000_000)
        engine.persist_fights(); engine.record_prediction(request.ticker,ts)
        return {"p_yes":p,"model":engine.prefight.status}

    @app.post("/api/ingest/licensed")
    async def licensed(request:Request,x_feed_signature:str=Header(default=""),x_feed_timestamp:str=Header(default="")):
        secret = os.getenv("UFC_LICENSED_INGEST_SECRET")
        license_id = os.getenv("UFC_DATA_LICENSE_ID")
        if cfg.source != "kalshi" or not secret or not license_id:
            raise HTTPException(503,"licensed feed is not configured")
        raw = await request.body()
        if len(raw)>1_000_000: raise HTTPException(413,"feed event too large")
        try: signed_ms = int(x_feed_timestamp)
        except ValueError: raise HTTPException(401,"timestamp required")
        adapter = LicensedIngest(engine.store,secret.encode())
        event = adapter.accept(raw,x_feed_signature,signed_ms,now_ms(),license_id)
        changed = engine.fight_event(event,now_ms(),"licensed")
        return {"accepted":changed,"seq":event.seq}

    @app.post("/api/mock/{action}",dependencies=[Depends(operator)])
    async def mock(action:str):
        if cfg.source != "mock": raise ValueError("mock controls unavailable in real-data mode")
        if action == "pause": engine.mock_running = False
        elif action == "play": engine.mock_running = True
        elif action == "step": engine.mock_step(now_ms())
        elif action == "new":
            if engine.mock_index <= 33: raise ValueError("complete the current scenario first")
            engine.mock_cycle+=1; engine.new_mock(now_ms())
        else: raise HTTPException(404,"unknown control")
        return {"running":engine.mock_running}

    @app.websocket("/ws")
    async def stream(ws:WebSocket):
        if not same_origin(ws.headers.get("origin",""),ws.headers.get("host","")) or not hmac.compare_digest(ws.cookies.get("research_session",""),token):
            await ws.close(code=1008); return
        await ws.accept()
        try:
            while True:
                await ws.send_text(canonical(engine.snapshot()))
                await asyncio.sleep(.5)
        except (WebSocketDisconnect,RuntimeError): pass

    dist = ROOT/"frontend/dist"
    if (dist/"assets").exists(): app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/")
    async def index():
        if not (dist/"index.html").exists(): raise HTTPException(503,"build frontend first: npm run build")
        return FileResponse(dist/"index.html")

    return app
