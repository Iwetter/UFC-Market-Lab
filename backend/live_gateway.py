"""Isolated optional order gateway. NEVER imported by app, engine or strategy.

Run only as an operator CLI. No server endpoint or auto-approval facility.
Production needs explicit file + env + arm + fresh signed per-order approval.
Unknown submission outcomes are not retried: reconcile or disarm.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

import httpx
from pydantic import Field

from .domain import D, OrderIntent, StrictModel, V2Order, V2OrderResponse, canonical, digest, now_ms
from .kalshi import DEMO_REST, PROD_REST, Signer


class LiveConfig(StrictModel):
    enabled: bool = False
    production_enabled: bool = False
    max_order_contracts: D = Field(default=D("2"),gt=0,le=2)
    max_session_notional: D = Field(default=D("10"),gt=0,le=25)
    allowed_series: list[str] = ["KXUFCFIGHT"]
    database: str = "data/live-audit.sqlite3"


class ApprovalAuthority:
    def __init__(self, secret:bytes):
        if len(secret)<32: raise ValueError("approval signing secret must be at least 32 bytes")
        self.secret = secret

    def sign(self, body):
        return {"body":body,"signature":hmac.new(self.secret,canonical(body).encode(),hashlib.sha256).hexdigest()}

    def verify(self, signed, ts):
        expected = self.sign(signed["body"])["signature"]
        if not hmac.compare_digest(expected,signed["signature"]): raise ValueError("invalid approval signature")
        body = signed["body"]
        if not body["issued_ms"] <= ts <= body["expires_ms"]: raise ValueError("approval expired or from future")
        return body

    def arm(self, cfg, environment, ts, duration_ms=3600000):
        if not 0 < duration_ms <= 14_400_000: raise ValueError("maximum arming duration is four hours")
        return self.sign({"type":"arm","id":secrets.token_hex(16),"environment":environment,"issued_ms":ts,
                          "expires_ms":ts+duration_ms,"max_notional":str(cfg.max_session_notional),"allowed_series":cfg.allowed_series})

    def approve(self, intent, arm, review_hash, ts):
        return self.sign({"type":"approval","intent_hash":digest(intent),"session_id":arm["body"]["id"],
                          "review_hash":review_hash,"issued_ms":ts,"expires_ms":ts+60000,"nonce":secrets.token_hex(16)})


class LiveGateway:
    def __init__(self,cfg:LiveConfig,authority:ApprovalAuthority,signer:Signer,*,environment="demo",client=None):
        if not cfg.enabled: raise ValueError("live adapter disabled in explicit config")
        if cfg.allowed_series != ["KXUFCFIGHT"]: raise ValueError("gateway is restricted to KXUFCFIGHT")
        if environment not in {"demo","prod"}: raise ValueError("invalid environment")
        if environment == "prod" and (not cfg.production_enabled or os.getenv("KALSHI_ENV") != "prod"):
            raise ValueError("production configuration and KALSHI_ENV=prod are required")
        self.cfg,self.authority,self.signer,self.environment=cfg,authority,signer,environment
        base = PROD_REST if environment == "prod" else DEMO_REST
        self.client=client or httpx.AsyncClient(base_url=base,timeout=10)
        # A caller cannot redirect a production signature to an arbitrary host.
        if client is not None and str(client.base_url).rstrip("/") != base:
            raise ValueError("live client host mismatch")
        Path(cfg.database).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(cfg.database,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY,intent_hash TEXT NOT NULL,session_id TEXT NOT NULL,
          notional TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,approval_nonce TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS revoked(session_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS groups(session_id TEXT PRIMARY KEY,group_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        stored=self.db.execute("SELECT value FROM metadata WHERE key='environment'").fetchone()
        if stored and stored[0] != environment: raise ValueError("demo and production require separate audit databases")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('environment',?)",(environment,))

    async def request(self,method,path,body=None):
        # Writes are deliberately never automatically retried.
        response=await self.client.request(method,path,json=body,headers=self.signer.headers(method,"/trade-api/v2"+path))
        response.raise_for_status()
        return json.loads(response.content,parse_float=D) if response.content else {}

    def checks(self,intent,arm,approval,review,ts):
        session=self.authority.verify(arm,ts); approved=self.authority.verify(approval,ts)
        if session["type"] != "arm" or approved["type"] != "approval": raise ValueError("wrong token type")
        if session["environment"] != self.environment: raise ValueError("arming environment mismatch")
        if session["expires_ms"]-session["issued_ms"] > 14_400_000 or approved["expires_ms"]-approved["issued_ms"] > 60000:
            raise ValueError("token lifetime exceeds hard cap")
        if approved["intent_hash"] != digest(intent) or approved["session_id"] != session["id"] or approved["review_hash"] != digest(review):
            raise ValueError("approval does not bind this exact order and review")
        if self.db.execute("SELECT 1 FROM revoked WHERE session_id=?",(session["id"],)).fetchone(): raise ValueError("session revoked")
        if not intent.ticker.startswith("KXUFCFIGHT-") or session["allowed_series"] != ["KXUFCFIGHT"]:
            raise ValueError("series not permitted")
        if intent.count > self.cfg.max_order_contracts: raise ValueError("live order size cap")
        if not 0 <= ts-review["server_ms"] <= 5000 or review["risk_reasons"] or not review["model_trained"] or review["ticker"] != intent.ticker:
            raise ValueError("fresh, trained, passing risk review required")
        if intent.action == "sell" and D(review.get("held_contracts","0")) < intent.count: raise ValueError("reduce-only holding required")
        used=sum((D(r[0]) for r in self.db.execute("SELECT notional FROM submissions WHERE session_id=?",(session["id"],))),D(0))
        notional=intent.limit_price*intent.count+D(review["fee_upper_bound"])
        if used+notional > min(D(session["max_notional"]),self.cfg.max_session_notional): raise ValueError("session notional cap")
        return session,approved,notional

    async def submit(self,intent,arm,approval,review,ts):
        started_mono=time.monotonic()
        existing=self.db.execute("SELECT * FROM submissions WHERE id=?",(str(intent.id),)).fetchone()
        if existing:
            if existing["intent_hash"] != digest(intent): raise ValueError("idempotency payload mismatch")
            return json.loads(existing["body"])
        session,approved,notional=self.checks(intent,arm,approval,review,ts)
        if self.db.execute("SELECT 1 FROM submissions WHERE status='unknown'").fetchone(): raise ValueError("unresolved submission; reconcile before another order")
        reconciliation=await self.reconcile(arm)
        if not reconciliation["matched"]: raise ValueError("dedicated account positions/fills do not match gateway ledger; session revoked")
        # Check public market lifecycle immediately before touching order endpoints.
        market=(await self.request("GET",f"/markets/{intent.ticker}"))["market"]
        from .domain import Market
        m=Market.model_validate(market)
        yes_price=intent.limit_price if intent.outcome == "yes" else D(1)-intent.limit_price
        if m.status != "active" or not m.on_grid(yes_price): raise ValueError("market inactive or price grid changed")
        status=await self.request("GET","/exchange/status")
        if not status["exchange_active"] or not status["trading_active"]: raise ValueError("exchange halted")
        group=self.db.execute("SELECT group_id FROM groups WHERE session_id=?",(session["id"],)).fetchone()
        if not group:
            group_id=(await self.request("POST","/portfolio/order_groups/create",{"contracts_limit_fp":str(self.cfg.max_order_contracts)}))["order_group_id"]
            self.db.execute("INSERT INTO groups VALUES (?,?)",(session["id"],group_id))
        else: group_id=group[0]
        # Persist UNKNOWN before the network side effect. A crash is reconcilable.
        unknown={"id":str(intent.id),"status":"unknown","intent":intent.model_dump(mode="json"),"approved_review":review}
        # Recheck expiry, freshness and cumulative caps at send time under a
        # write transaction so concurrent operator processes cannot race caps.
        send_ms=ts+int((time.monotonic()-started_mono)*1000)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            session,approved,notional=self.checks(intent,arm,approval,review,send_ms)
            if self.db.execute("SELECT 1 FROM submissions WHERE status='unknown'").fetchone():
                raise ValueError("another submission is unresolved")
            self.db.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?)",(str(intent.id),digest(intent),session["id"],str(notional),"unknown",canonical(unknown),approved["nonce"]))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        wire=V2Order.from_intent(intent,group_id)
        try:
            response=await self.request("POST","/portfolio/events/orders",wire.model_dump(mode="json"))
            parsed=V2OrderResponse.model_validate(response)
            filled,remaining=parsed.fill_count,parsed.remaining_count
            if remaining != 0 or not D(0) <= filled <= intent.count: raise ValueError("unexpected IOC response; reconcile required")
            result=unknown|{"status":"executed" if filled == intent.count else "canceled","exchange":response,"fill_count":str(filled),"remaining_count":"0","canceled_count":str(intent.count-filled)}
            self.db.execute("UPDATE submissions SET status=?,body=? WHERE id=?",(result["status"],canonical(result),str(intent.id)))
            return result
        except BaseException:
            self.db.execute("INSERT OR IGNORE INTO revoked VALUES (?)",(session["id"],))
            raise

    async def kill(self,arm):
        session=arm["body"]["id"]
        self.db.execute("INSERT OR IGNORE INTO revoked VALUES (?)",(session,))
        return await self.request("DELETE","/portfolio/events/orders")

    async def reconcile(self,arm):
        """Compare accepted IOC fills by order ID. Any uncertainty revokes arming."""
        data=await self.request("GET","/portfolio/fills?limit=1000")
        fills=data["fills"]
        # Incomplete history is not evidence of a match.
        okay=not bool(data.get("cursor"))
        for row in self.db.execute("SELECT body FROM submissions"):
            local=json.loads(row[0]); exchange=local.get("exchange")
            if not exchange: okay=False; continue
            actual=sum((D(f["count_fp"]) for f in fills if f["order_id"] == exchange["order_id"]),D(0))
            if actual != D(local["fill_count"]): okay=False
        positions=await self.request("GET","/portfolio/positions?limit=1000")
        if positions.get("cursor"): okay=False
        actual_positions={p["ticker"]:D(p["position_fp"]) for p in positions["market_positions"] if D(p["position_fp"])}
        expected_positions={}
        for row in self.db.execute("SELECT body FROM submissions"):
            local=json.loads(row[0]);i=local["intent"]
            if local["status"] == "unknown": okay=False;continue
            delta=D(local["fill_count"])*(D(1) if (i["outcome"] == "yes") == (i["action"] == "buy") else D(-1))
            expected_positions[i["ticker"]]=expected_positions.get(i["ticker"],D(0))+delta
        expected_positions={k:v for k,v in expected_positions.items() if v}
        if expected_positions != actual_positions: okay=False
        if not okay: self.db.execute("INSERT OR IGNORE INTO revoked VALUES (?)",(arm["body"]["id"],))
        return {"matched":okay,"positions":positions,"note":"Existing external positions require manual reconciliation; no automatic resubmission."}

    async def close(self):
        await self.client.aclose();self.db.close()


async def cli(args):
    cfg=LiveConfig.model_validate_json(Path(args.config).read_text())
    if not cfg.enabled: raise ValueError("adapter disabled; review config/live.example.json before opting in")
    secret=os.environ.get("UFC_LIVE_APPROVAL_SECRET","").encode()
    authority=ApprovalAuthority(secret)
    env="prod" if args.production else "demo"
    if args.command == "arm":
        if env == "prod" and (not cfg.production_enabled or os.getenv("KALSHI_ENV") != "prod"): raise ValueError("production not explicitly enabled")
        phrase=input(f"Arm {env.upper()} manual gateway for one hour? Type ARM {env.upper()}: ")
        if phrase != f"ARM {env.upper()}": raise ValueError("arming canceled")
        Path(args.session).parent.mkdir(parents=True,exist_ok=True)
        Path(args.session).write_text(canonical(authority.arm(cfg,env,now_ms())))
        print("Expiring session saved locally. Each order still needs individual approval.")
        return
    arm=json.loads(Path(args.session).read_text())
    signer=Signer(os.environ["KALSHI_ORDER_KEY_ID"],Path(os.environ["KALSHI_ORDER_KEY_PATH"]).read_bytes())
    gateway=LiveGateway(cfg,authority,signer,environment=env)
    try:
        if args.command == "kill": print(canonical(await gateway.kill(arm))); return
        if args.command == "watch":
            while True:
                report=await gateway.reconcile(arm); print(canonical(report))
                if not report["matched"]: raise ValueError("reconciliation mismatch: session revoked")
                await asyncio.sleep(30)
        intent=OrderIntent.model_validate_json(Path(args.intent).read_text())
        async with httpx.AsyncClient(timeout=5) as client:
            snapshot=(await client.get("http://127.0.0.1:8000/api/state")).json()
        row=next(m for m in snapshot["markets"] if m["ticker"] == intent.ticker)
        live_positions=await gateway.request("GET","/portfolio/positions?limit=1000")
        holding=next((D(p["position_fp"]) for p in live_positions["market_positions"] if p["ticker"] == intent.ticker),D(0))
        held=max(D(0),holding if intent.outcome == "yes" else -holding)
        fee=row["edges"][intent.outcome]["fee_upper_bound"]
        review={"ticker":intent.ticker,"server_ms":snapshot["server_ms"],"risk_reasons":row["risk_reasons"],
                "model_trained":snapshot["model"]["trained"],"fee_upper_bound":str(D(fee)*intent.count),
                "p_yes":row["p_yes"],"market_mid":row["market_mid"],"book":row["book"],"held_contracts":str(held)}
        print(canonical({"environment":env,"intent":intent.model_dump(mode="json"),"yes_leg":V2Order.from_intent(intent,"pending").price,"review":review,
                         "worst_case_loss":str(intent.count*intent.limit_price+D(review["fee_upper_bound"]))}))
        phrase=input(f"Approve ONLY this {env.upper()} order? Type APPROVE {intent.id}: ")
        if phrase != f"APPROVE {intent.id}": raise ValueError("order approval canceled")
        ts=now_ms(); approval=authority.approve(intent,arm,digest(review),ts)
        print(canonical(await gateway.submit(intent,arm,approval,review,ts)))
        # The one-order CLI stays in reconciliation mode; it never queues another order.
        while now_ms() < arm["body"]["expires_ms"]:
            await asyncio.sleep(30)
            report=await gateway.reconcile(arm)
            print(canonical(report))
            if not report["matched"]:
                await gateway.kill(arm)
                raise ValueError("reconciliation mismatch: session revoked, cancel-all sent")
        await gateway.kill(arm)
    finally:
        if args.command == "submit":
            gateway.db.execute("INSERT OR IGNORE INTO revoked VALUES (?)",(arm["body"]["id"],))
        await gateway.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",choices=["arm","submit","kill","watch"])
    p.add_argument("--config",default="config/live.example.json")
    p.add_argument("--session",default="data/live-session.json")
    p.add_argument("--intent")
    p.add_argument("--production",action="store_true")
    args=p.parse_args()
    if args.command == "submit" and not args.intent: p.error("submit requires --intent")
    asyncio.run(cli(args))


if __name__ == "__main__": main()
