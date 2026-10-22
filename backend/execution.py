"""IOC-only paper broker. No import or reference to the live network adapter."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR

from .book import Book
from .domain import D, ONE, ZERO, Market, OrderIntent, Settings, canonical, digest
from .fees import FeeAccumulator, FeeSchedule
from .store import Store


def date_utc(ts):
    return datetime.fromtimestamp(ts/1000,timezone.utc).date().isoformat()


def risk_reasons(intent, market, book, mapping, fight, cfg, ts, *, killed=False,
                 exchange_active=True, clock_offset_ms=None, model_ready=False,
                 fees=None):
    reasons = []
    if killed: reasons.append("kill_switch")
    if not exchange_active: reasons.append("exchange_halted")
    if market.status != "active": reasons.append("market_not_active")
    if not mapping or not mapping.get("confirmed") or mapping.get("replacement"):
        reasons.append("mapping_unconfirmed")
    if market.close_time and datetime.fromisoformat(market.close_time.replace("Z","+00:00")).timestamp()*1000 <= ts:
        reasons.append("market_closed_by_clock")
    if book.stale or not 0 <= ts-book.updated_ms <= cfg.max_book_age_ms:
        reasons.append("book_stale")
    if not fight or fight.stale or not 0 <= ts-fight.received_ms <= cfg.max_fight_age_ms:
        reasons.append("fight_feed_stale")
    if fight and fight.status in {"paused","finished"}: reasons.append("fight_paused_or_finished")
    if fight and fight.status == "live" and fight.clock_remaining <= cfg.blackout_seconds:
        reasons.append("round_blackout")
    if clock_offset_ms is None or abs(clock_offset_ms) > 50: reasons.append("clock_unverified")
    if not model_ready: reasons.append("model_not_approved")
    if fees is None or ts-fees.verified_ms > 300_000 or (fees.valid_until_ms is not None and ts >= fees.valid_until_ms):
        reasons.append("fees_unverified")
    yes_price = intent.limit_price if intent.outcome == "yes" else ONE-intent.limit_price
    if not market.on_grid(yes_price): reasons.append("off_grid")
    if not market.fractional_trading_enabled and intent.count != intent.count.to_integral_value():
        reasons.append("fractional_count_not_supported")
    if intent.count > cfg.max_order_contracts: reasons.append("order_size_cap")
    bid,ask = book.quote()
    if bid is None or ask is None: reasons.append("empty_book")
    elif ask-bid > cfg.max_spread: reasons.append("spread_cap")
    return reasons


class PaperBroker:
    def __init__(self, store: Store, cfg: Settings):
        self.store,self.cfg = store,cfg
        initial = store.get("account_initial_cash")
        if initial is not None and D(initial) != cfg.initial_cash:
            raise ValueError("initial cash cannot be changed on an existing paper account")
        store.put("account_initial_cash",str(cfg.initial_cash))
        self.positions = store.get("positions",{})
        self.settled = set(store.get("settled",[]))
        self.killed = store.get("killed",False)
        self.used_liquidity = {}

    @property
    def cash(self):
        return self.cfg.initial_cash+sum((D(r[0]) for r in self.store.db.execute("SELECT amount FROM ledger")),ZERO)

    def orders(self):
        return self.store.bodies("orders")

    def reserved(self, except_id=None):
        return sum((D(o["reserved"]) for o in self.orders() if o["status"] == "pending" and o["id"] != except_id),ZERO)

    def _save_order(self, body):
        self.store.db.execute("INSERT INTO orders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                              (body["id"],body["intent_hash"],canonical(body)))

    def exposure(self, bout=None, card=None):
        positions = sum((D(p["cost_basis"]) for p in self.positions.values()
                         if (bout is None or p["bout_id"] == bout) and (card is None or p["card_id"] == card)),ZERO)
        pending = sum((D(o["reserved"]) for o in self.orders() if o["status"] == "pending"
                       and (bout is None or o["bout_id"] == bout) and (card is None or o["card_id"] == card)),ZERO)
        return positions+pending

    def daily_spend(self, ts):
        return sum((max(ZERO,-D(r["amount"])) for r in self.store.db.execute("SELECT ts_ms,amount FROM ledger")
                    if date_utc(r["ts_ms"]) == date_utc(ts)),ZERO)

    def _ledger(self, ts, order_id, amount, kind):
        self.store.db.execute("INSERT INTO ledger(ts_ms,order_id,amount,kind) VALUES (?,?,?,?)", (ts,order_id,str(amount),kind))

    def submit(self, intent: OrderIntent, ts, context):
        key,h = str(intent.id),digest(intent)
        existing = self.store.db.execute("SELECT intent_hash,body FROM orders WHERE id=?",(key,)).fetchone()
        if existing:
            if existing["intent_hash"] != h:
                raise ValueError("idempotency key already used for a different intent")
            return json.loads(existing["body"])
        mapping = context.get("mapping") or {}
        reasons = risk_reasons(intent, cfg=self.cfg, ts=ts, killed=self.killed, **context)
        fee = context.get("fees")
        reserve = intent.limit_price*intent.count
        if fee:
            reserve += fee.fee(intent.limit_price,intent.count).quantize(D(".01"),rounding=ROUND_CEILING)
        position = self.positions.get(f"{intent.ticker}|{intent.outcome}")
        if intent.action == "buy":
            for scope,cap in (("bout",self.cfg.max_bout_loss),("card",self.cfg.max_event_loss),("all",self.cfg.max_total_loss)):
                exposure = self.exposure(bout=mapping.get("bout_id")) if scope == "bout" else self.exposure(card=mapping.get("card_id")) if scope == "card" else self.exposure()
                if scope == "all":
                    exposure += max(ZERO,-sum((D(p["realized_pnl"]) for p in self.positions.values()),ZERO))
                if exposure+reserve > cap: reasons.append(f"{scope}_exposure_cap")
            if reserve+self.reserved() > self.cash: reasons.append("insufficient_cash")
            if self.daily_spend(ts)+self.reserved()+reserve > self.cfg.max_daily_loss: reasons.append("daily_spend_cap")
            opposite = self.positions.get(f"{intent.ticker}|{'no' if intent.outcome == 'yes' else 'yes'}")
            if opposite and D(opposite["quantity"]) > 0: reasons.append("opposite_position")
        else:
            pending_sells = sum((D(o["intent"]["count"]) for o in self.orders() if o["status"] == "pending" and o["intent"]["action"] == "sell" and o["intent"]["ticker"] == intent.ticker and o["intent"]["outcome"] == intent.outcome),ZERO)
            if not position or D(position["quantity"])-pending_sells < intent.count: reasons.append("reduce_only_position_limit")
            reserve = ZERO
        if any(o["status"] == "pending" and o["intent"]["ticker"] == intent.ticker and o["intent"]["outcome"] != intent.outcome for o in self.orders()):
            reasons.append("opposite_open_order")
        result = {"id":key,"intent_hash":h,"intent":intent.model_dump(mode="json"),"created_ms":ts,
                  "arrival_ms":ts+self.cfg.order_latency_ms,"status":"rejected" if reasons else "pending",
                  "reasons":reasons,"fill_count":"0","remaining_count":str(intent.count) if not reasons else "0",
                  "canceled_count":str(intent.count) if reasons else "0","reserved":str(reserve) if not reasons else "0",
                  "bout_id":mapping.get("bout_id"),"card_id":mapping.get("card_id"),"mode":"PAPER"}
        with self.store.transaction():
            self.store.append("paper","order_intent",result,recv_utc_ns=ts*1_000_000)
            self._save_order(result)
        return result

    def process(self, ts, context_for):
        for order in self.orders():
            if order["status"] != "pending" or order["arrival_ms"] > ts:
                continue
            intent = OrderIntent.model_validate(order["intent"])
            context = context_for(intent.ticker)
            reasons = risk_reasons(intent,cfg=self.cfg,ts=ts,killed=self.killed,**context)
            if ts-order["arrival_ms"] > self.cfg.max_book_age_ms:
                reasons.append("arrival_processing_late")
            with self.store.transaction():
                if reasons:
                    order.update(status="canceled",reasons=reasons,canceled_count=str(intent.count),remaining_count="0",reserved="0")
                else:
                    self._execute(order,intent,context,ts)
                self._save_order(order)
                self.store.put("positions",self.positions)
                self.store.append("paper","order_result",order,recv_utc_ns=ts*1_000_000)

    def _execute(self, order, intent, context, ts):
        book,market,fees = context["book"],context["market"],context["fees"]
        buy = intent.action == "buy"
        levels = book.asks(intent.outcome) if buy else book.bids(intent.outcome)
        # Conservatively restrict fills to within two smallest grid steps of touch.
        if levels and market.price_ranges:
            touch = levels[0][0]
            distance = 2*min(b.step for b in market.price_ranges)
            levels = [(p,q) for p,q in levels if abs(p-touch) <= distance]
        remaining,filled,fee_total,notional = intent.count,ZERO,ZERO,ZERO
        acc = FeeAccumulator()
        position_key = f"{intent.ticker}|{intent.outcome}"
        mapping = context["mapping"]
        pos = self.positions.setdefault(position_key,{"ticker":intent.ticker,"outcome":intent.outcome,"quantity":"0","cost_basis":"0","realized_pnl":"0","bout_id":mapping["bout_id"],"card_id":mapping["card_id"]})
        for level_index,(price,displayed) in enumerate(levels):
            if buy and price > intent.limit_price or not buy and price < intent.limit_price:
                break
            # Slippage consumes the budget; never fill beyond the limit or the grid.
            target = price+self.cfg.slippage if buy else price-self.cfg.slippage
            choices = []
            for band in market.price_ranges:
                yes_target = target if intent.outcome == "yes" else ONE-target
                rounded = band.start+((yes_target-band.start)/band.step).to_integral_value(rounding=ROUND_CEILING if buy == (intent.outcome == "yes") else ROUND_FLOOR)*band.step
                effective = rounded if intent.outcome == "yes" else ONE-rounded
                if band.start <= rounded <= band.end and ZERO < effective < ONE and (effective >= target if buy else effective <= target):
                    choices.append(effective)
            if not choices: continue
            effective = min(choices) if buy else max(choices)
            if buy and effective > intent.limit_price or not buy and effective < intent.limit_price:
                continue
            liquidity_key = (intent.ticker,book.updated_ms,intent.outcome,intent.action,str(price))
            used = self.used_liquidity.get(liquidity_key,ZERO)
            cap = max(ZERO,displayed*self.cfg.max_participation-used)
            quantity = min(remaining,cap)
            quantum = D(".01") if market.fractional_trading_enabled else ONE
            quantity = (quantity/quantum).to_integral_value(rounding=ROUND_FLOOR)*quantum
            if quantity <= 0: continue
            revenue = (-ONE if buy else ONE)*effective*quantity
            change,fee = acc.apply(revenue,fees.fee(effective,quantity))
            self._ledger(ts,order["id"],change,"buy" if buy else "sell")
            q,basis = D(pos["quantity"]),D(pos["cost_basis"])
            if buy:
                pos["quantity"],pos["cost_basis"] = str(q+quantity),str(basis-change)
            else:
                released = basis*quantity/q
                pos["quantity"],pos["cost_basis"] = str(q-quantity),str(basis-released)
                pos["realized_pnl"] = str(D(pos["realized_pnl"])+change-released)
            fill = {"id":f"{order['id']}:{level_index}","order_id":order["id"],"ticker":intent.ticker,
                    "outcome":intent.outcome,"action":intent.action,"price":str(effective),"count":str(quantity),
                    "fee":str(fee),"cash_change":str(change),"ts_ms":ts,"book_ms":book.updated_ms,"mode":"PAPER"}
            self.store.db.execute("INSERT INTO fills VALUES (?,?,?)",(fill["id"],order["id"],canonical(fill)))
            self.used_liquidity[liquidity_key] = used+quantity
            remaining-=quantity; filled+=quantity; fee_total+=fee; notional+=effective*quantity
            if not remaining: break
        order.update(status="executed" if not remaining else "canceled",fill_count=str(filled),remaining_count="0",
                     canceled_count=str(remaining),reserved="0",fee=str(fee_total),
                     average_fill_price=str(notional/filled) if filled else None,
                     reasons=[] if not remaining else ["ioc_unfilled_remainder"])
        if len(self.used_liquidity) > 10000:
            self.used_liquidity = {k:v for k,v in self.used_liquidity.items() if k[1] >= ts-self.cfg.max_book_age_ms}

    def settle(self, ticker, value, ts, source="kalshi"):
        if source not in {"kalshi","mock"} or not ZERO <= value <= ONE:
            raise ValueError("exchange settlement value required")
        if ticker in self.settled: return
        with self.store.transaction():
            for pos in self.positions.values():
                if pos["ticker"] != ticker: continue
                quantity,basis = D(pos["quantity"]),D(pos["cost_basis"])
                payout = quantity*(value if pos["outcome"] == "yes" else ONE-value)
                self._ledger(ts,None,payout,"settlement")
                pos.update(quantity="0",cost_basis="0",realized_pnl=str(D(pos["realized_pnl"])+payout-basis))
            self.settled.add(ticker)
            self.store.put("positions",self.positions); self.store.put("settled",sorted(self.settled))
            self.store.append(source,"settlement",{"ticker":ticker,"value":str(value)},recv_utc_ns=ts*1_000_000)

    def kill(self, ts):
        self.killed = True
        with self.store.transaction():
            self.store.put("killed",True)
            for order in self.orders():
                if order["status"] == "pending":
                    order.update(status="canceled",reserved="0",remaining_count="0",canceled_count=order["intent"]["count"],reasons=["kill_switch"])
                    self._save_order(order)
            self.store.append("operator","kill_switch",{"enabled":True},recv_utc_ns=ts*1_000_000)

    def summary(self, books):
        rows,unrealized = [],ZERO
        for pos in self.positions.values():
            q = D(pos["quantity"])
            book = books.get(pos["ticker"])
            bid = book.quote(pos["outcome"])[0] if book else None
            mark = bid*q-D(pos["cost_basis"]) if bid is not None else -D(pos["cost_basis"])
            unrealized+=mark
            rows.append(pos | {"mark_bid":str(bid) if bid is not None else None,"unrealized_pnl":str(mark),"mark_stale":not book or book.stale})
        realized = sum((D(p["realized_pnl"]) for p in self.positions.values()),ZERO)
        return {"cash":str(self.cash),"reserved":str(self.reserved()),"available_cash":str(self.cash-self.reserved()),
                "realized_pnl":str(realized),"unrealized_pnl":str(unrealized),"net_pnl":str(realized+unrealized),
                "equity":str(self.cfg.initial_cash+realized+unrealized),"positions":rows,"kill_switch":self.killed}
