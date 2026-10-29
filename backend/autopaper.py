"""Deterministic experimental pre-fight PAPER strategy; never imports a live gateway."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from decimal import ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .domain import D, ONE, ZERO, OrderIntent, StrictModel, digest


class AutoPaperSettings(StrictModel):
    enabled: bool = False
    min_entry_edge: D = Field(default=D('.05'), ge=D('.01'), le=D('.5'))
    min_exit_edge: D = Field(default=D('.02'), ge=0, le=D('.5'))
    probability_buffer: D = Field(default=D('.05'), ge=D('.02'), le=D('.4'))
    cooldown_ms: int = Field(default=60000, ge=10000, le=86400000)
    max_orders_per_market_day: int = Field(default=5, ge=1, le=20)
    contracts: int = Field(default=1, ge=1, le=10)
    # Stop before the declared earliest plausible start; never extend into live fighting.
    start_guard_ms: int = Field(default=300000, ge=60000, le=86400000)


class FixtureReview(StrictModel):
    ticker: str
    card_id: str = Field(min_length=3,max_length=200)
    fighter_a_id: str
    fighter_b_id: str
    scheduled_ms: int = Field(gt=0)
    yes_is_a: bool
    rounds: int = Field(default=3, ge=3, le=5)
    rules_sha256: str = Field(min_length=64, max_length=64)
    rules_reviewed: bool
    schedule_source: str = Field(min_length=5, max_length=500)


def normalize_name(name):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()).strip()


def rules_hash(market):
    return digest({k: getattr(market, k) for k in ('ticker','event_ticker','title','event_title','yes_sub_title','rules_primary','rules_secondary')})


@lru_cache(maxsize=2)
def prediction_service(root):
    from .prediction_service import PredictionService
    return PredictionService(root)


class AutoPaper:
    def __init__(self, engine, root=None, service=None):
        self.engine = engine
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self._service = service
        self._load_error = None
        self.settings = AutoPaperSettings.model_validate(engine.store.get('autopaper_settings', {}))
        self.fixtures = engine.store.get('autopaper_fixtures', {})
        self.predictions = engine.store.get('autopaper_predictions', {})
        self.decisions = engine.store.get('autopaper_decisions', {})
        self.last_attempt = engine.store.get('autopaper_last_attempt', {})
        self.last_run = 0

    @property
    def service(self):
        if self._service is None and self._load_error is None:
            try:
                self._service = prediction_service(str(self.root))
            except (OSError, ValueError, ImportError) as exc:
                self._load_error = type(exc).__name__
        return self._service

    def configure(self, settings, ts):
        self.settings = AutoPaperSettings.model_validate(settings)
        self.engine.store.put('autopaper_settings', self.settings.model_dump(mode='json'))
        self.engine.store.append('operator', 'autopaper_settings', self.settings, recv_utc_ns=ts*1000000)
        return self.settings.model_dump(mode='json')

    def bind(self, review, ts):
        review = FixtureReview.model_validate(review)
        market = self.engine.markets.get(review.ticker)
        if not market or market.status != 'active' or not market.rules_primary:
            raise ValueError('active market with settlement rules required')
        if not review.rules_reviewed or review.rules_sha256 != rules_hash(market):
            raise ValueError('review current complete settlement rules before binding')
        if review.rounds not in (3,5) or review.fighter_a_id == review.fighter_b_id:
            raise ValueError('distinct fighters and three or five rounds required')
        if review.scheduled_ms <= ts+self.settings.start_guard_ms:
            raise ValueError('earliest plausible fight start must be after the pre-fight guard')
        if self.service is None:
            raise ValueError('historical prediction service unavailable')
        roster = {f['id']:f for f in self.service.fighters()}
        if review.fighter_a_id not in roster or review.fighter_b_id not in roster:
            raise ValueError('canonical historical fighter IDs required')
        a,b = roster[review.fighter_a_id],roster[review.fighter_b_id]
        yes = a if review.yes_is_a else b
        aliases = lambda f: {normalize_name(n) for n in [f['name'],*f.get('aliases',[])]}
        if normalize_name(market.yes_sub_title) not in aliases(yes):
            raise ValueError('YES subtitle must exactly match the selected fighter identity')
        title = ' '+normalize_name(market.event_title or market.title)+' '
        if not all(any(' '+n+' ' in title for n in aliases(f)) for f in (a,b)):
            raise ValueError('both canonical fighters must appear in the market title')
        previous = self.fixtures.get(review.ticker)
        if previous and any(D(p['quantity'])>0 and p['ticker']==review.ticker for p in self.engine.broker.positions.values()):
            raise ValueError('cannot replace a fixture while a paper position is open')
        prediction = self.service.predict(a['id'], b['id'], review.scheduled_ms, ts)
        bout_id = digest({'fighters':sorted([a['id'],b['id']]),'card_id':review.card_id})
        for other in self.fixtures.values():
            if other['ticker']!=review.ticker and other['bout_id']==bout_id and other['scheduled_ms']!=review.scheduled_ms:
                raise ValueError('contracts for the same reviewed bout must share the earliest start')
        fixture = review.model_dump() | {'bout_id':bout_id, 'reviewed_ms':ts,
            'fighter_a':a['name'],'fighter_b':b['name']}
        with self.engine.store.transaction():
            self.engine.confirm_mapping(market.ticker,bout_id,review.card_id,a['name'],b['name'],review.yes_is_a,review.rounds,ts)
            self.fixtures[market.ticker] = fixture
            self.predictions[market.ticker] = prediction | {'generated_ms':ts}
            self.engine.store.put('autopaper_fixtures',self.fixtures)
            self.engine.store.put('autopaper_predictions',self.predictions)
            self.engine.store.append('operator','autopaper_fixture',fixture,recv_utc_ns=ts*1000000)
            self.engine.store.append('historical_model','autopaper_prediction',{'ticker':market.ticker,**self.predictions[market.ticker]},recv_utc_ns=ts*1000000)
        return fixture

    def eligible(self, ticker, ts):
        f,p = self.fixtures.get(ticker),self.predictions.get(ticker)
        if not f or not p:return False
        market = self.engine.markets.get(ticker)
        mapping = self.engine.mappings.get(ticker)
        fight = self.engine.fights.get(f['bout_id'])
        return bool(market and mapping and mapping.get('confirmed') and not mapping.get('replacement')
            and mapping.get('bout_id')==f['bout_id'] and mapping.get('yes_is_a')==f['yes_is_a']
            and all(mapping.get(k)==f[k] for k in ('fighter_a','fighter_b','card_id'))
            and rules_hash(market)==f['rules_sha256'] and market.status=='active'
            and fight and fight.status=='scheduled'
            and 0<=ts-p['generated_ms']<=86400000 and p['features_asof_ms']<=ts
            and p['trained_through_ms']<=ts and ts<f['scheduled_ms']-self.settings.start_guard_ms)

    def probability(self, ticker, ts):
        if not self.eligible(ticker,ts):return None
        p = self.predictions[ticker]['probability_a']
        return p if self.fixtures[ticker]['yes_is_a'] else 1-p

    def context(self, ticker, ts, context):
        # Only the paper engine receives this context; execution models/approvals are untouched.
        if self.settings.enabled and self.engine.cfg.mode in ('paper','replay') and self.eligible(ticker,ts):
            return context | {'model_ready':True, 'prefight_cutoff_ms':self.fixtures[ticker]['scheduled_ms']-self.settings.start_guard_ms}
        return context

    def _record(self, ticker, ts, decision):
        signature = digest({k:v for k,v in decision.items() if k not in ('ts_ms',)})
        previous = self.decisions.get(ticker,{})
        if previous.get('signature')==signature:return
        row = decision | {'ts_ms':ts,'signature':signature,'ticker':ticker,'mode':'PAPER',
                          'experimental':True,'live_eligible':False}
        self.decisions[ticker] = row
        self.engine.store.put('autopaper_decisions',self.decisions)
        self.engine.store.append('strategy','autopaper_decision',row,recv_utc_ns=ts*1000000)

    def run_once(self, ts):
        if self.engine.cfg.mode!='paper' or ts-self.last_run<1000:return
        self.last_run = ts
        for ticker in self.fixtures:
            base = {'action':'NO-TRADE','reasons':[]}
            if not self.settings.enabled:
                self._record(ticker,ts,base|{'reasons':['automation_disabled']});continue
            f=self.fixtures[ticker];p=self.predictions.get(ticker,{})
            market=self.engine.markets.get(ticker)
            if (ts-p.get('generated_ms',0)>=43200000 and market and rules_hash(market)==f['rules_sha256']
                    and ts<f['scheduled_ms']-self.settings.start_guard_ms and self.service):
                try:
                    refreshed=self.service.predict(f['fighter_a_id'],f['fighter_b_id'],f['scheduled_ms'],ts)|{'generated_ms':ts}
                    with self.engine.store.transaction():
                        self.predictions[ticker]=refreshed
                        self.engine.store.put('autopaper_predictions',self.predictions)
                        self.engine.store.append('historical_model','autopaper_prediction',{'ticker':ticker,**refreshed},recv_utc_ns=ts*1000000)
                except ValueError:
                    self._record(ticker,ts,base|{'reasons':['prediction_refresh_failed']});continue
            if not self.eligible(ticker,ts):
                self._record(ticker,ts,base|{'reasons':['prediction_mapping_or_prefight_window_invalid']});continue
            context = self.engine.context(ticker,ts)
            book,fee = context['book'],context['fees']
            p = D(str(self.probability(ticker,ts)))
            prediction = self.predictions[ticker]
            base |= {'model_version':prediction['model_version'],'probability_yes':str(p),
                'input_ms':prediction['features_asof_ms'],'book_ms':book.updated_ms,
                'uncertainty_note':'Conservative policy buffer; not a statistical confidence interval.',
                'probability_buffer':str(self.settings.probability_buffer)}
            orders = self.engine.broker.orders()
            if any(o['status']=='pending' and o['intent']['ticker']==ticker for o in orders):
                self._record(ticker,ts,base|{'action':'HOLD','reasons':['order_pending']});continue
            if ts-self.last_attempt.get(ticker,0)<self.settings.cooldown_ms:
                self._record(ticker,ts,base|{'action':'HOLD','reasons':['cooldown']});continue
            from .execution import date_utc,risk_reasons
            if sum(o['intent']['ticker']==ticker and date_utc(o['created_ms'])==date_utc(ts) for o in orders)>=self.settings.max_orders_per_market_day:
                self._record(ticker,ts,base|{'reasons':['daily_market_order_cap']});continue
            positions = [x for x in self.engine.broker.positions.values() if x['ticker']==ticker and D(x['quantity'])>0]
            candidates = []
            for outcome in ('yes','no'):
                fair = p if outcome=='yes' else ONE-p
                pos = next((x for x in positions if x['outcome']==outcome),None)
                action = 'sell' if pos else 'buy'
                if positions and not pos:continue
                levels = book.bids(outcome) if pos else book.asks(outcome)
                if not levels or fee is None:continue
                price,depth = levels[0]
                target = price-self.engine.cfg.slippage if pos else price+self.engine.cfg.slippage
                choices = []
                for band in context['market'].price_ranges:
                    yes_target = target if outcome=='yes' else ONE-target
                    rounding = ROUND_CEILING if (action=='buy')==(outcome=='yes') else ROUND_FLOOR
                    yes_price = band.start+((yes_target-band.start)/band.step).to_integral_value(rounding=rounding)*band.step
                    limit = yes_price if outcome=='yes' else ONE-yes_price
                    if context['market'].on_grid(yes_price) and (limit<=target if pos else limit>=target):choices.append(limit)
                if not choices:continue
                limit = max(choices) if pos else min(choices)
                quantity = min(D(self.settings.contracts),depth*self.engine.cfg.max_participation,self.engine.cfg.max_order_contracts)
                if pos:quantity=min(quantity,D(pos['quantity']))
                quantum = D('.01') if context['market'].fractional_trading_enabled else ONE
                quantity=(quantity/quantum).to_integral_value(rounding=ROUND_FLOOR)*quantum
                if quantity<=0:continue
                cost=fee.fee(limit,quantity).quantize(D('.01'),rounding=ROUND_CEILING)/quantity
                edge=(limit-fair if pos else fair-limit)-cost-self.settings.probability_buffer
                threshold=self.settings.min_exit_edge if pos else self.settings.min_entry_edge
                if edge>=threshold:candidates.append((edge,outcome,action,limit,quantity))
            if not candidates:
                self._record(ticker,ts,base|{'action':'HOLD' if positions else 'NO-TRADE','reasons':['no_net_edge_after_costs_and_buffer']});continue
            edge,outcome,action,limit,quantity=max(candidates)
            # Persist a monotonic sequence with the intent before submission: no restart duplicates.
            sequence=self.engine.store.get('autopaper_sequence',0)+1
            intent=OrderIntent(id=str(uuid5(NAMESPACE_URL,f'ufc-autopaper:{self.engine.store.session}:{ticker}:{sequence}')),
                ticker=ticker,outcome=outcome,action=action,count=str(quantity),limit_price=str(limit))
            reasons=risk_reasons(intent,cfg=self.engine.cfg,ts=ts,killed=self.engine.broker.killed,**context)
            if reasons:
                self._record(ticker,ts,base|{'reasons':reasons});continue
            with self.engine.store.transaction():
                self.engine.store.put('autopaper_sequence',sequence)
                self.last_attempt[ticker]=ts
                self.engine.store.put('autopaper_last_attempt',self.last_attempt)
                order=self.engine.submit(intent,ts)
                self._record(ticker,ts,base|{'action':action.upper(),'outcome':outcome,'limit':str(limit),
                    'quantity':str(quantity),'net_edge_after_buffer':str(edge),'order_id':order['id'],
                    'reasons':order['reasons'] or ['net_execution_edge_exceeds_policy_threshold']})

    def snapshot(self):
        return {'settings':self.settings.model_dump(mode='json'),'fixtures':self.fixtures,
            'predictions':self.predictions,'decisions':list(self.decisions.values()),
            'model':self.service.status() if self.service else {'available':False,'error':self._load_error},
            'scope':'Experimental pre-fight Kalshi PAPER only; never an in-fight or live-money model.'}
