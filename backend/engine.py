from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

from .book import Book, BookStream
from .domain import D, ONE, ZERO, FightEvent, Market, OrderIntent, Settings, canonical, digest, now_ms
from .execution import PaperBroker, risk_reasons, date_utc
from .fees import FeeSchedule, expected_value
from .ingest import mock_event
from .models import CalibratedPreFight, FightState, InFightEngine
from .store import Store


class Engine:
    def __init__(self, cfg: Settings, model_path=None, infight_model_path=None):
        self.cfg = cfg
        self.store = Store(cfg.database)
        previous_source = self.store.get("source")
        if previous_source is not None and previous_source != cfg.source:
            raise ValueError("use separate databases for mock and real market data")
        self.store.put("source",cfg.source)
        self.store.append("core","run_config",{"config":cfg.model_dump(mode="json"),"sha256":digest(cfg)})
        self.books = BookStream()
        self.markets = {t:Market.model_validate(m) for t,m in self.store.get("markets",{}).items()}
        self.mappings = {m["ticker"]:m for m in self.store.bodies("mappings")}
        self.fights = {}
        for bid,state in self.store.get("fights",{}).items():
            self.fights[bid] = FightState(**state)
            self.fights[bid].stale = True  # Restart is never evidence of freshness.
        self.prefight = CalibratedPreFight(json.loads(Path(model_path).read_text()) if model_path else None)
        self.infight = InFightEngine(CalibratedPreFight(json.loads(Path(infight_model_path).read_text()),"infight") if infight_model_path else None)
        self.broker = PaperBroker(self.store,cfg)
        self.fees = {}
        self.series_fee = None
        self.health = {"rest":"not started","websocket":"not started","error":None}
        self.exchange_active = cfg.source == "mock"
        self.clock_offset_ms = 0 if cfg.source == "mock" else None
        self.clock_observed_ms = now_ms() if cfg.source == "mock" else 0
        self.history = self.store.get("history",[])[-180:]
        self.mock_index = self.store.get("mock_index",0)
        self.mock_running = True
        self.health_failures = 0
        self.started_ms = now_ms()
        self.tasks = []
        self.recorder = None
        self.mock_cycle = self.store.get("mock_cycle",1)
        if cfg.source == "mock" and not self.markets and cfg.mode != "replay":
            self.new_mock(now_ms())

    def add_market(self, market):
        old = self.markets.get(market.ticker)
        self.markets[market.ticker] = market
        if old and (old.yes_sub_title != market.yes_sub_title or old.rules_primary != market.rules_primary):
            mapping = self.mappings.get(market.ticker)
            if mapping:
                mapping.update(confirmed=False,replacement=True)
                self.save_mapping(mapping)
        self.store.put("markets",{t:m.model_dump(mode="json") for t,m in self.markets.items()})
        self.store.append("core","market",market)

    def apply_market(self, payload, ts):
        market = Market.model_validate(payload)
        self.add_market(market)
        if market.status != "active" and market.ticker in self.books.books:
            self.books.books[market.ticker].stale = True
        # Determination can be disputed. Pay only a finalized exchange value.
        if market.status == "finalized" and market.settlement_value_dollars is not None:
            self.broker.settle(market.ticker,market.settlement_value_dollars,ts)

    def lifecycle(self, msg, ts):
        ticker = msg["market_ticker"]
        market = self.markets[ticker]
        kind = msg["event_type"]
        statuses = {"deactivated":"inactive","activated":"active","determined":"determined","settled":"finalized"}
        if kind in statuses:
            market.status = statuses[kind]
            if market.status != "active":self.books.books.setdefault(ticker,Book()).stale=True
        if kind in {"price_level_structure_updated","metadata_updated","close_date_updated"}:
            self.books.books.setdefault(ticker,Book()).stale = True
            market.price_ranges = []  # REST re-verification required.
            if self.recorder: self.recorder.last_discovery = 0
        if kind == "metadata_updated" and ticker in self.mappings:
            self.mappings[ticker]["confirmed"] = False
            self.save_mapping(self.mappings[ticker])
        if kind == "settled":
            # Do not infer settlement from fight winner or undocumented WS fields.
            if self.recorder: self.recorder.last_discovery = 0
        self.add_market(market)

    def save_mapping(self, mapping):
        self.mappings[mapping["ticker"]] = mapping
        self.store.db.execute("INSERT INTO mappings VALUES (?,?) ON CONFLICT(ticker) DO UPDATE SET body=excluded.body",(mapping["ticker"],canonical(mapping)))

    def confirm_mapping(self, ticker, bout_id, card_id, fighter_a, fighter_b, yes_is_a, rounds, ts):
        if ticker not in self.markets: raise ValueError("unknown market")
        mapping = {"ticker":ticker,"bout_id":bout_id,"card_id":card_id,"fighter_a":fighter_a,"fighter_b":fighter_b,
                   "yes_is_a":yes_is_a,"confirmed":True,"replacement":False,"confidence":1.,"reviewed_ms":ts}
        self.save_mapping(mapping)
        if bout_id not in self.fights:
            # Prior must be supplied from a pre-fight feature prediction separately.
            self.fights[bout_id] = FightState(bout_id,rounds=rounds)
        self.store.append("operator","mapping_review",mapping,recv_utc_ns=ts*1_000_000)
        return mapping

    def fight_event(self, event: FightEvent, ts, source="licensed", record=True):
        if self.cfg.source == "kalshi" and source == "mock":
            raise ValueError("mock fight events cannot enter a real-market database")
        if record:
            self.store.append(source,"fight",event,source_ts_ms=event.source_ts_ms,seq=event.seq,recv_utc_ns=ts*1_000_000)
        if event.source_ts_ms > ts+50: raise ValueError("provider clock is ahead of receive time")
        fight = self.fights.setdefault(event.bout_id,FightState(event.bout_id))
        changed = fight.apply(event,ts)
        self.persist_fights()
        if changed:
            for ticker,mapping in self.mappings.items():
                if mapping["bout_id"] == event.bout_id:
                    self.record_prediction(ticker,ts)
        return changed

    def persist_fights(self):
        self.store.put("fights",{bid:{k:v for k,v in vars(state).items() if k != "seen"} for bid,state in self.fights.items()})

    def probabilities(self, ticker):
        mapping = self.mappings.get(ticker)
        fight = self.fights.get(mapping["bout_id"]) if mapping else None
        book = self.books.books.get(ticker)
        bid,ask = book.quote() if book else (None,None)
        benchmark = float((bid+ask)/2) if bid is not None and ask is not None else None
        if fight and mapping and mapping["confirmed"]:
            p = self.infight.probability(fight) if fight.status != "scheduled" else fight.prior
            return (p if mapping["yes_is_a"] else 1-p),benchmark,fight
        return benchmark,benchmark,fight

    def record_prediction(self, ticker, ts):
        p,benchmark,fight = self.probabilities(ticker)
        if p is None: return
        self.store.prediction(ts,ticker,{"p_yes":p,"market_mid":benchmark,"model":self.prefight.status["version"],
            "updater":self.infight.model.status["version"],"mock":self.cfg.source=="mock","fight_seq":fight.last_seq if fight else None})

    def record_book(self, ticker, ts, metadata=None):
        book = self.books.books[ticker]
        timing = {"source_protocol":"unknown","exchange_ts_ms":None,"sending_ts_ms":None,
                  "market_status":self.markets[ticker].status}
        if metadata: timing.update(metadata)
        self.store.append(self.cfg.source,"book",{"ticker":ticker,**book.view(),**timing},recv_utc_ns=ts*1_000_000)
        self.record_prediction(ticker,ts)

    def context(self, ticker, ts=None):
        ts = now_ms() if ts is None else ts
        market = self.markets[ticker]
        mapping = self.mappings.get(ticker)
        fight = self.fights.get(mapping["bout_id"]) if mapping else None
        approval = self.store.get("model_approval")
        model_ready = self.cfg.source == "mock" or bool(self.prefight.artifact and approval and approval["version"] == self.prefight.status["version"])
        if fight and fight.status not in {"scheduled","finished"} and not self.infight.model.artifact and self.cfg.source != "mock": model_ready = False
        clock = self.clock_offset_ms if 0 <= ts-self.clock_observed_ms <= 60000 or self.cfg.source == "mock" else None
        return dict(market=market,book=self.books.books.setdefault(ticker,Book()),mapping=mapping,fight=fight,
                    clock_offset_ms=clock,exchange_active=self.exchange_active,model_ready=model_ready,fees=self.fees.get(ticker))

    def submit(self, intent, ts):
        if intent.ticker not in self.markets: raise ValueError("unknown market")
        if self.store.db.execute("SELECT 1 FROM orders WHERE id=?",(str(intent.id),)).fetchone():
            return self.broker.submit(intent,ts,self.context(intent.ticker,ts))
        p,benchmark,fight = self.probabilities(intent.ticker)
        if p is not None and ((benchmark is not None and abs(p-benchmark) > .35) or
                (fight and fight.status == "live" and not .01 <= p <= .99)):
            self.broker.kill(ts)
        result = self.broker.submit(intent,ts,self.context(intent.ticker,ts))
        bad_data = {"book_stale","fight_feed_stale","clock_unverified"}
        if bad_data.intersection(result["reasons"]):
            self.health_failures += 1
            if self.health_failures >= 3 and not self.broker.killed: self.broker.kill(ts)
        elif result["status"] == "pending": self.health_failures = 0
        return result

    def tick(self, ts):
        if any(o["status"] == "pending" and o["arrival_ms"] <= ts for o in self.broker.orders()):
            self.store.append("core","execution_tick",{"ts_ms":ts},recv_utc_ns=ts*1_000_000)
        self.broker.process(ts,lambda ticker:self.context(ticker,ts))
        portfolio = self.broker.summary(self.books.books)
        pnl = D(portfolio["net_pnl"])
        day = date_utc(ts)
        baseline = self.store.get("day_baseline")
        if not baseline or baseline["date"] != day:
            baseline = {"date":day,"pnl":str(pnl),"peak":str(pnl)}
        peak = max(D(baseline["peak"]),pnl)
        baseline["peak"] = str(peak)
        self.store.put("day_baseline",baseline)
        if pnl-D(baseline["pnl"]) <= -self.cfg.max_daily_loss or peak-pnl >= self.cfg.daily_drawdown:
            if not self.broker.killed: self.broker.kill(ts)

    def new_mock(self, ts):
        if self.cfg.source != "mock": raise ValueError("mock source disabled")
        old_ticker = f"MOCK-UFC-{self.mock_cycle:03}"
        if old_ticker in self.markets and self.markets[old_ticker].status == "active":
            raise ValueError("finish the current mock scenario before starting another")
        ticker = f"MOCK-UFC-{self.mock_cycle:03}"
        bout_id = f"MOCK-BOUT-{self.mock_cycle:03}"
        market = Market(ticker=ticker,event_ticker="MOCK-CARD",title="Avery North vs Blair Vale",yes_sub_title="Avery North",status="active",price_ranges=[{"start":"0","end":"1","step":".01"}],rules_primary="Fictional deterministic research scenario. YES = Avery North.")
        self.add_market(market)
        self.confirm_mapping(ticker,bout_id,"MOCK-CARD","Avery North","Blair Vale",True,3,ts)
        self.fights[bout_id] = FightState(bout_id,prior=.60,received_ms=ts,stale=False)
        self.store.append("core","prior",{"bout_id":bout_id,"p_a":.60},recv_utc_ns=ts*1_000_000)
        self.mock_index = 0
        self.store.put("mock_cycle",self.mock_cycle)
        self.mock_step(ts)

    def mock_step(self, ts):
        ticker = f"MOCK-UFC-{self.mock_cycle:03}"
        bout_id = f"MOCK-BOUT-{self.mock_cycle:03}"
        if self.mock_index > 33: return
        if self.markets[ticker].status != "active": return
        if self.mock_index == 33:
            event = FightEvent(bout_id=bout_id,provider_event_id=f"mock:{self.mock_cycle}:finish",kind="finish",seq=34,source_ts_ms=ts,round=3,clock_remaining=0,winner="a",strikes_a=96,strikes_b=64,knockdowns_a=1,control_a=128,control_b=64,snapshot=True)
            self.fight_event(event,ts,"mock")
            self.markets[ticker].status = "finalized"
            self.books.books[ticker].stale = True
            self.broker.settle(ticker,ONE,ts,"mock")
        else:
            e = mock_event(self.mock_index,ts-self.mock_index*1000,bout_id)
            self.fight_event(e,ts,"mock")
            bid = D(".47")+D((self.mock_index%7)-3)/100
            book = self.books.books.setdefault(ticker,Book())
            book.snapshot([[str(bid),"80"],[str(bid-D(".01")),"120"]],[[str(ONE-bid-D(".04")),"80"],[str(ONE-bid-D(".05")),"100"]],ts)
            self.fees[ticker] = FeeSchedule(verified_ms=ts)
            self.store.append("mock","fees",{"ticker":ticker,"fee_type":"quadratic","multiplier":"1","verified_ms":ts},recv_utc_ns=ts*1_000_000)
            self.record_book(ticker,ts)
        self.clock_observed_ms = ts
        self.mock_index+=1
        self.store.put("mock_index",self.mock_index)
        self.store.put("markets",{t:m.model_dump(mode="json") for t,m in self.markets.items()})

    def snapshot(self, ts=None):
        ts = now_ms() if ts is None else ts
        rows = []
        for ticker,market in self.markets.items():
            p,benchmark,fight = self.probabilities(ticker)
            book = self.books.books.setdefault(ticker,Book())
            c = self.context(ticker,ts)
            fee = c["fees"]
            edges = {}
            for outcome in ("yes","no"):
                bid,ask = book.quote(outcome)
                if p is not None and ask is not None and fee and market.status == "active" and not book.stale and 0 <= ts-book.updated_ms <= self.cfg.max_book_age_ms and ts-fee.verified_ms <= 300000 and (fee.valid_until_ms is None or ts < fee.valid_until_ms):
                    edges[outcome] = expected_value(p if outcome == "yes" else 1-p,ask,ONE,fee,self.cfg.slippage)
            # Full gate explanation at a conservative valid touch limit.
            ask = book.quote()[1]
            reason = []
            if ask is not None:
                check = OrderIntent(id="00000000-0000-0000-0000-000000000000",ticker=ticker,outcome="yes",count="1",limit_price=str(min(D(".99"),ask+self.cfg.slippage)))
                reason = risk_reasons(check,cfg=self.cfg,ts=ts,killed=self.broker.killed,**c)
            else: reason = ["empty_book"]
            rows.append({**market.model_dump(mode="json"),"book":book.view(),"mapping":self.mappings.get(ticker),
                         "p_yes":p,"market_mid":benchmark,"edges":edges,"fight":self.infight.view(fight) if fight else None,
                         "book_age_ms":max(0,ts-book.updated_ms) if book.updated_ms else None,
                         "fight_age_ms":max(0,ts-fight.received_ms) if fight and fight.received_ms else None,
                         "provider_latency_ms":fight.received_ms-fight.source_ts_ms if fight and fight.received_ms else None,
                         "risk_reasons":reason})
        return {"server_ms":ts,"mode":"PAPER","source":self.cfg.source,"live_enabled":False,
                "markets":rows,"portfolio":self.broker.summary(self.books.books),"orders":self.broker.orders()[-100:],
                "fills":self.store.bodies("fills")[-100:],"health":self.health|{"sequence_gaps":self.books.gaps,"clock_offset_ms":self.clock_offset_ms,"clock_verified_ms":self.clock_observed_ms},
                "model":self.prefight.status,"infight_model":self.infight.model.status,"history":self.history[-180:],
                "mock_running":self.mock_running,"mock_complete":self.mock_index>33,
                "events_recorded":self.store.db.execute("SELECT count(*) FROM events").fetchone()[0]}

    async def run(self):
        if self.cfg.source == "kalshi":
            from .kalshi import KalshiRecorder
            from .licensed_runtime import configured_recorders
            licensed=configured_recorders(self)
            self.recorder = KalshiRecorder(self)
            self.tasks = [asyncio.create_task(self.recorder.polling()),asyncio.create_task(self.recorder.streaming())]
            self.tasks.extend(asyncio.create_task(feed.run()) for feed in licensed)
        try:
            last_mock,last_history = 0,0
            while True:
                ts = now_ms()
                if self.cfg.source == "mock" and self.mock_running and ts-last_mock >= 2000:
                    self.mock_step(ts); last_mock = ts
                self.tick(ts)
                if ts-last_history >= 1000:
                    active = next((t for t,m in self.markets.items() if m.status == "active"),None)
                    if active:
                        p,m,_ = self.probabilities(active)
                        self.history.append({"ts_ms":ts,"ticker":active,"model":p,"market":m,"pnl":self.broker.summary(self.books.books)["net_pnl"]})
                        self.history = self.history[-180:]
                    last_history = ts
                await asyncio.sleep(.05)
        finally:
            for task in self.tasks: task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)
            if self.recorder: await self.recorder.rest.close()
            self.store.put("history",self.history)
            self.persist_fights()
