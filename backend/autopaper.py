"""Deterministic experimental pre-fight PAPER strategy; never imports a live gateway."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .domain import D, ONE, ZERO, OrderIntent, StrictModel, digest, now_ms
from .strategy import ProfitPolicy,StrategyLedger,decide_trade
from .execution import risk_reasons


class AutoPaperSettings(ProfitPolicy):
    enabled: bool = False
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
            self.engine.persist_fights()
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
            choice=decide_trade(self.engine.broker,ticker,context,self.engine.cfg,self.settings,p,ts,self.last_attempt.get(ticker,0))
            base |= {'strategy':choice['metrics']}
            if choice['action'] not in ('BUY','SELL'):
                self._record(ticker,ts,base|choice);continue
            edge,outcome,action,limit,quantity=(choice['net_edge_after_buffer'],choice['outcome'],choice['action'].lower(),choice['limit'],choice['quantity'])
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
                    'reasons':order['reasons'] or choice['reasons']})

    def snapshot(self):
        strategy={}
        for ticker in self.fixtures:
            c=self.engine.context(ticker)
            strategy[ticker]=StrategyLedger(self.engine.broker,ticker).snapshot(c['book'],c['fees'],self.engine.cfg,self.settings,self.probability(ticker,now_ms()))
        return {'settings':self.settings.model_dump(mode='json'),'strategy':strategy,'fixtures':self.fixtures,
            'predictions':self.predictions,'decisions':list(self.decisions.values()),
            'model':self.service.status() if self.service else {'available':False,'error':self._load_error},
            'scope':'Experimental pre-fight Kalshi PAPER only; never an in-fight or live-money model.'}
