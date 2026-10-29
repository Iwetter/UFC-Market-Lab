"""Isolated historical practice: real bout history, explicitly synthetic books.

No execution-engine approvals, host-clock observations, live adapters or network
calls are involved. The broker's clock/heartbeat/fee inputs below are an exact
virtual simulation context; they are never represented as real observations.
"""
from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

from .autopaper import AutoPaperSettings
from .book import Book
from .domain import D, ONE, ZERO, Market, OrderIntent, Settings, canonical
from .execution import PaperBroker
from .fees import FeeSchedule
from .historical import ATTRIBUTION, SOURCE_COMMIT, number
from .prediction_service import PredictionService
from .store import Store
from .strategy import StrategyLedger,decide_trade


LIMITATIONS = [
    "Real recorded bout/round statistics; current source snapshot with assumed seven-day availability, not recorded forward predictions.",
    "The trained pre-fight probability stays fixed throughout replay. No trained in-fight win model is available.",
    "Quotes, depth, fees and fills are synthetic practice assumptions, not historical exchange observations or evidence of profit.",
    "Quote path uses only bout ID and elapsed time, independently of outcome and recorded statistics. Trades are not guaranteed.",
    "Only completed round aggregates are revealed; no strike, knockdown or position timestamps are inferred. Round breaks are omitted.",
    "Previously viewed 2024–2026 holdout; experimental PAPER only, never live approval.",
]
SOURCES = {"fight": "pinned nlbair/ufc-data / UFCStats historical snapshot",
           "model": "fixed trained pre-fight portable research model",
           "market": "SYNTHETIC deterministic practice order book; not historical exchange data",
           "execution": "isolated PaperBroker simulation; 250ms virtual latency, quadratic assumed fees",
           "clock": "virtual active fight time; no host clock or real feed claim"}


def _day_ms(day):
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc).timestamp()*1000)


def synthetic_quote(fight_id, elapsed_ms):
    """Fixed triangular swing. Deliberately has no probability/outcome inputs."""
    phase = int(hashlib.sha256(fight_id.encode()).hexdigest()[:8], 16) % 240_000
    x = ((elapsed_ms+phase) % 240_000)/120_000
    triangle = x if x <= 1 else 2-x
    mid = D(str(.17+.66*triangle)).quantize(D('.01'))
    return mid-D('.02'), mid+D('.02')


class PracticeController:
    def __init__(self, root, directory, *, service=None):
        self.root, self.directory = Path(root), Path(directory)
        self._service = service
        self._catalog = None
        self._rows = {}
        self.store = self.broker = None
        self._closed = False
        self._last_snapshot = None
        self.status = "idle"
        self.speed = 30.
        self.run_id = None
        self.policy = AutoPaperSettings(enabled=True)
        self._wall_mark = time.monotonic()

    @property
    def service(self):
        if self._service is None:
            self._service = PredictionService(self.root)
        return self._service

    def catalog(self,query=''):
        if self._catalog is not None:
            result=json.loads(canonical(self._catalog))
            matches=[f for f in result['fights'] if query.strip().casefold() in f['label'].casefold()]
            result.update(total_eligible=len(result['fights']),matched_count=len(matches),fights=matches[:100],
                          default_fight_id=matches[0]['id'] if matches else None)
            return result
        status = self.service.status()
        result = {"available": False, "error": status.get('error'), "fights": [],
                  "limitations": LIMITATIONS, "sources": SOURCES, "default_fight_id": None}
        if status.get('available'):
            histories = defaultdict(list)
            for row in self.service.rows:
                for key in ('f1_id', 'f2_id'):
                    histories[row[key]].append(row['event_date'])
            for days in histories.values():
                days.sort()
            cutoff = status['trained_through_ms']
            for row in sorted(self.service.rows, key=lambda r:(r['event_date'], r['fight_id']), reverse=True):
                day = date.fromisoformat(row['event_date'])
                if day.year < 2024 or _day_ms(row['event_date'])-1 < cutoff:
                    continue
                released_day = (day-timedelta(days=7)).isoformat()
                if min(bisect.bisect_right(histories[row[k]], released_day) for k in ('f1_id','f2_id')) < 2:
                    continue
                duration = number(row.get('fight_duration_sec'))
                rounds = number(row.get('scheduled_rounds'))
                if duration is None or duration <= 0 or rounds not in (3,5) or duration > rounds*300:
                    continue
                a, b = sorted((row['f1_id'], row['f2_id']))
                names = {row['f1_id']:row.get('f1_name') or row['f1_id'], row['f2_id']:row.get('f2_name') or row['f2_id']}
                public = {"id":row['fight_id'], "label":f"{names[a]} vs {names[b]} · {row['event_date']}",
                          "event_date":row['event_date'], "event":row['event_name'],
                          "fighter_a":names[a], "fighter_b":names[b]}
                self._rows[row['fight_id']] = (row, public, a, b)
                result['fights'].append(public)
            result['available'] = bool(result['fights'])
            result['error'] = None if result['available'] else 'No eligible cached bouts after model training with two prior bouts per fighter.'
            result['default_fight_id'] = result['fights'][0]['id'] if result['fights'] else None
        self._catalog = result
        return self.catalog(query)

    @staticmethod
    def _speed(value):
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not 1 <= value <= 120:
            raise ValueError('practice speed must be between 1 and 120')
        return float(value)

    def start(self, fight_id, speed=30):
        speed = self._speed(speed)
        if not self.catalog()['available'] or fight_id not in self._rows:
            raise ValueError('choose an eligible cached practice fight')
        row, public, a, b = self._rows[fight_id]
        cutoff = _day_ms(row['event_date'])-1  # strictly before selected bout day
        prediction = self.service.predict(a, b, _day_ms(row['event_date']), cutoff)
        if prediction['trained_through_ms'] > cutoff or prediction['features_asof_ms'] > cutoff:
            raise ValueError('practice prediction contains future information')
        if self.store is not None:
            self._archive('replaced_by_new_run')
        self._closed = False
        self._last_snapshot = None
        self.run_id = str(uuid4())
        self.run_directory = self.directory/self.run_id
        self.run_directory.mkdir(parents=True, exist_ok=False)
        self.cfg = Settings(mode='replay', source='mock', database=str(self.run_directory/'practice.sqlite3'))
        self.store = Store(self.cfg.database)
        self.broker = PaperBroker(self.store, self.cfg)
        self.row, self.fight, self.a_id, self.b_id = dict(row), dict(public), a, b
        self.prediction = prediction
        self.start_ms = _day_ms(row['event_date'])
        self.elapsed_ms = 0
        self._finish_ms = int(float(row['fight_duration_sec'])*1000)
        self.duration_ms = int(float(row['scheduled_rounds'])*300_000)
        self.speed, self.status = speed, 'running'
        self.ticker = 'PRACTICE-'+fight_id
        self.market = Market(ticker=self.ticker, event_ticker='PRACTICE', status='active',
                             title=public['label'], rules_primary='Synthetic winner contract; draw/NC returns 0.5. Practice only.',
                             price_ranges=[{'start':'0','end':'1','step':'.01'}])
        self.book = Book()
        self.history, self.checkpoints = [], []
        self.peak_equity = self.cfg.initial_cash
        self.risk_stop = None
        self.store.put('practice_peak_equity',str(self.peak_equity))
        self.last_attempt = None
        self._next_decision_ms = 15_000
        self._virtual_budget = 0.
        self._wall_mark = time.monotonic()
        manifest = {'run_id':self.run_id, 'fight':self.fight, 'created_utc':datetime.now(timezone.utc).isoformat(),
                    'source_commit':SOURCE_COMMIT, 'attribution':ATTRIBUTION, 'sources':SOURCES,
                    'prediction':prediction, 'settings':self.cfg.model_dump(mode='json'),
                    'policy':self.policy.model_dump(mode='json'), 'limitations':LIMITATIONS,
                    'quote_algorithm':'sha256(bout_id) phase; 240s triangular mid .17–.83; .04 spread; 100 contracts/level',
                    'automatically_resume':False}
        (self.run_directory/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        self._refresh()
        self.store.append('practice', 'run_started', manifest, recv_utc_ns=self.ts*1_000_000)
        self._decide()
        return self.snapshot()

    @property
    def ts(self):
        return self.start_ms+self.elapsed_ms

    def _refresh(self):
        bid, ask = synthetic_quote(self.fight['id'], self.elapsed_ms)
        self.book.snapshot([[str(bid),'100'],[str(bid-D('.01')),'100']],
                           [[str(ONE-ask),'100'],[str(ONE-ask-D('.01')),'100']],self.ts)
        self.store.append('practice_synthetic', 'book', self.book.view(), recv_utc_ns=self.ts*1_000_000)

    def _context(self, ticker):
        if ticker != self.ticker:
            raise ValueError('foreign ticker in isolated practice account')
        # These exact virtual values are solely broker simulation assumptions.
        # They never enter Engine, AutoPaper, live eligibility or clock monitors.
        fight = SimpleNamespace(status='live', stale=False, received_ms=self.ts,
                                clock_remaining=300-(self.elapsed_ms//1000)%300)
        return {'market':self.market,'book':self.book,
                'mapping':{'confirmed':True,'bout_id':self.fight['id'],'card_id':'PRACTICE'},
                'fight':fight, 'clock_offset_ms':0, 'model_ready':True,
                'exchange_active':True, 'fees':FeeSchedule(verified_ms=self.ts)}

    def _record(self, action, reasons, **extra):
        portfolio = self.broker.summary({self.ticker:self.book})
        bid, ask = self.book.quote()
        record = {'action':action, 'reasons':reasons, 'reason':'; '.join(r.replace('_',' ') for r in reasons),
                  'elapsed_s':self.elapsed_ms/1000,
                  'virtual_time_ms':self.ts, 'model_version':self.prediction['model_version'],
                  'input_ms':self.prediction['features_asof_ms'], 'probability_a':self.prediction['probability_a'],
                  'probability_buffer':str(self.policy.probability_buffer), 'mode':'PRACTICE',
                  'yes_bid':str(bid), 'yes_ask':str(ask),
                  'net_pnl':portfolio['net_pnl'],'equity':portfolio['equity'],
                  'live_eligible':False, **extra}
        self.history.append(record)
        self.store.append('practice_strategy','decision',record,recv_utc_ns=self.ts*1_000_000)

    def _decide(self):
        if not self.policy.enabled:
            self._record('NO-TRADE',['automation_disabled']);return
        if self.risk_stop:
            self._record('NO-TRADE',[self.risk_stop]); return
        choice=decide_trade(self.broker,self.ticker,self._context(self.ticker),self.cfg,self.policy,
                            self.prediction['probability_a'],self.ts,self.last_attempt or 0)
        if choice['action'] not in ('BUY','SELL'):
            self._record(choice['action'],choice['reasons'],strategy=choice['metrics']);return
        edge,outcome,action,limit,quantity=(choice['net_edge_after_buffer'],choice['outcome'],choice['action'].lower(),choice['limit'],choice['quantity'])
        orders=self.broker.orders()
        intent = OrderIntent(id=uuid5(NAMESPACE_URL,f'{self.run_id}:{len(orders)}'),ticker=self.ticker,
                             outcome=outcome,action=action,count=str(quantity),limit_price=str(limit))
        order = self.broker.submit(intent,self.ts,self._context(self.ticker))
        self.last_attempt = self.ts
        self._record(action.upper() if order['status']=='pending' else 'NO-TRADE',
                     order['reasons'] or choice['reasons'],
                     outcome=outcome,limit=str(limit),quantity=str(quantity),order_id=order['id'],net_edge_after_buffer=str(edge))

    def _checkpoint(self):
        for round_no in range(1,6):
            if round_no*300_000 > self.elapsed_ms or any(c['round']==round_no for c in self.checkpoints):
                continue
            fields = ('sig_str_landed','kd','td_landed','sub_att','ctrl_sec','distance_landed','clinch_landed','ground_landed')
            data = {}
            for label, fid in (('a',self.a_id),('b',self.b_id)):
                prefix = 'f1' if fid==self.row['f1_id'] else 'f2'
                data[label] = {field:number(self.row.get(f'{prefix}_r{round_no}_{field}')) for field in fields}
            checkpoint = {'round':round_no,'elapsed_s':round_no*300,'statistics':data,
                          'source':'real recorded completed-round aggregate; retrospective availability',
                          'available':any(v is not None for side in data.values() for v in side.values())}
            self.checkpoints.append(checkpoint)
            self.store.append('practice_historical','completed_round',checkpoint,recv_utc_ns=self.ts*1_000_000)

    def _risk_guards(self):
        equity = D(self.broker.summary({self.ticker:self.book})['equity'])
        self.peak_equity = max(self.peak_equity,equity)
        self.store.put('practice_peak_equity',str(self.peak_equity))
        reason = ('practice_daily_drawdown_cap' if self.peak_equity-equity >= self.cfg.daily_drawdown else
                  'practice_daily_loss_cap' if self.cfg.initial_cash-equity >= self.cfg.max_daily_loss else None)
        if reason and not self.risk_stop:
            self.risk_stop = reason
            self.store.put('practice_risk_stop',reason)
            self.broker.kill(self.ts)
            self._record('NO-TRADE',[reason])

    def _next_time(self):
        pending = [o['arrival_ms']-self.start_ms for o in self.broker.orders() if o['status']=='pending']
        return min([self._next_decision_ms,self._finish_ms,*pending])

    def step(self):
        """Advance one event (<=15 virtual seconds), even while paused."""
        if self.store is None or self._closed or self.status=='finished':
            return self.snapshot()
        self.elapsed_ms = self._next_time()
        self._refresh()
        # Arrival is processed at its own event before the next strategy frame.
        self.broker.process(self.ts,self._context)
        self._risk_guards()
        self._checkpoint()
        if self.elapsed_ms >= self._finish_ms:
            self._finish()
        elif self.elapsed_ms >= self._next_decision_ms:
            self._decide()
            self._next_decision_ms += 15_000
        self._virtual_budget = max(self._virtual_budget,float(self.elapsed_ms))
        return self.snapshot()

    def _finish(self):
        self.broker.kill(self.ts)  # cancel any arrival still pending before settlement
        result_a = self.row['result_f1'] if self.a_id==self.row['f1_id'] else self.row['result_f2']
        result_b = self.row['result_f2'] if self.a_id==self.row['f1_id'] else self.row['result_f1']
        value = ONE if (result_a,result_b)==('W','L') else ZERO if (result_a,result_b)==('L','W') else D('.5')
        self.broker.settle(self.ticker,value,self.ts,source='mock')
        self.status = 'finished'
        self.fight['result'] = {'winner':'a' if value==ONE else 'b' if value==ZERO else 'draw_or_nc',
                                'winner_name':self.fight['fighter_a'] if value==ONE else self.fight['fighter_b'] if value==ZERO else 'Draw / no contest',
                                'is_correct_prediction':(self.prediction['probability_a']>=.5)==(value==ONE) if value in (ZERO,ONE) else None,
                                'method':self.row.get('method'), 'end_round':self.row.get('end_round'),
                                'end_time':self.row.get('end_time'), 'actual_duration_s':self.elapsed_ms/1000,
                                'settlement_note':'Practice settlement: recorded binary winner pays 1; draw/NC pays 0.5.'}
        self._record('HOLD',['replay_finished_recorded_result_revealed'])
        self.store.append('practice','run_finished',self.snapshot(),recv_utc_ns=self.ts*1_000_000)
        self._write_summary()

    def control(self, action, speed=None):
        if speed is not None:
            speed = self._speed(speed)
        if action not in ('pause','resume','step','speed','stop'):
            raise ValueError('practice action must be pause, resume, step, speed or stop')
        if self.store is None or self._closed:
            raise ValueError('start a practice run first')
        if speed is not None:
            self.speed = speed
        self._wall_mark = time.monotonic()
        self._virtual_budget = float(self.elapsed_ms)
        if action=='stop':
            self._archive('operator_stopped')
        elif action=='pause' and self.status!='finished':
            self.status = 'paused'
        elif action=='resume' and self.status!='finished':
            self.status = 'running'
        elif action=='step':
            if self.status!='finished':
                self.status = 'paused'
            return self.step()
        return self.snapshot()

    async def run(self):
        """Single application-lifetime driver. Restart never resumes old runs."""
        while not self._closed:
            await asyncio.sleep(.05)
            now = time.monotonic()
            if self.store is not None and self.status=='running':
                self._virtual_budget += min(1.,now-self._wall_mark)*1000*self.speed
                for _ in range(20):
                    if self.status!='running' or self._next_time()>self._virtual_budget:
                        break
                    self.step()
            self._wall_mark = now

    def snapshot(self):
        if self._last_snapshot is not None:
            return json.loads(canonical(self._last_snapshot))
        if self.store is None:
            return {'status':'idle','run_id':None,'sources':SOURCES,'limitations':LIMITATIONS,'policy':self.policy.model_dump(mode='json')}
        result = {'status':self.status, 'run_id':self.run_id, 'speed':self.speed, 'mode':'PRACTICE',
                  'closed':self._closed,'fight':self.fight, 'elapsed_s':self.elapsed_ms/1000,
                  'duration_s':self.duration_ms/1000, 'round':min(int(self.row['scheduled_rounds']),max(1,math.ceil(self.elapsed_ms/300_000))),
                  'clock_remaining':300 if not self.elapsed_ms else (300_000-(self.elapsed_ms-1)%300_000-1)//1000,
                  'virtual_time_ms':self.ts, 'simulated_account':True,
                  'probability_a':self.prediction['probability_a'], 'model_version':self.prediction['model_version'],
                  'input_ms':self.prediction['features_asof_ms'], 'prediction':self.prediction,
                  'quotes':self.book.view(), 'decision':self.history[-1] if self.history else None,
                  'history':self.history, 'checkpoints':self.checkpoints, 'orders':self.broker.orders(),
                  'fills':self.store.bodies('fills'), 'portfolio':self.broker.summary({self.ticker:self.book}),
                  'risk':{'peak_equity':str(self.peak_equity),'stop_reason':self.risk_stop,
                          'daily_drawdown_cap':str(self.cfg.daily_drawdown),'daily_loss_cap':str(self.cfg.max_daily_loss)},
                  'sources':SOURCES, 'limitations':LIMITATIONS, 'artifacts':{'run_id':self.run_id,
                  'manifest':'manifest.json','journal':'practice.sqlite3','summary':'summary.json'},
                  'live_eligible':False,'policy':self.policy.model_dump(mode='json'),
                  'strategy':StrategyLedger(self.broker,self.ticker).snapshot(self.book,FeeSchedule(verified_ms=self.ts),self.cfg,self.policy,self.prediction['probability_a'])}
        return json.loads(canonical(result))

    def configure(self,policy):
        policy=AutoPaperSettings.model_validate(policy)
        self.policy=policy
        if self.store is not None:
            self.store.append('operator','practice_policy',policy,recv_utc_ns=self.ts*1000000)
            self.store.put('practice_policy',policy.model_dump(mode='json'))
        return self.snapshot()

    def _write_summary(self):
        (self.run_directory/'summary.json').write_text(json.dumps(self.snapshot(),indent=2),encoding='utf-8')

    def _archive(self, reason):
        # Cancel pending entries, then liquidate positions against the current
        # synthetic book (no outcome lookup) so replacement leaves no open risk.
        self.broker.kill(self.ts)
        self.broker.killed = False
        self.store.put('killed',False)
        for pos in list(self.broker.positions.values()):
            quantity = D(pos['quantity'])
            if quantity <= 0:
                continue
            bid = self.book.quote(pos['outcome'])[0]
            intent = OrderIntent(id=uuid4(),ticker=self.ticker,outcome=pos['outcome'],action='sell',
                                 count=str(quantity),limit_price=str(bid-self.cfg.slippage))
            # Explicit operator liquidation omits the strategy round blackout;
            # all broker depth, fees, reduce-only and exposure checks still apply.
            context = self._context(self.ticker)
            context['fight'].clock_remaining = 300
            order = self.broker.submit(intent,self.ts,context)
            if order['status']=='pending':
                self.elapsed_ms += self.cfg.order_latency_ms
                # Preserve quoted liquidation level over the virtual latency.
                self.book.updated_ms = self.ts
                context = self._context(self.ticker)
                context['fight'].clock_remaining = 300
                self.broker.process(self.ts,lambda _:context)
        self.broker.kill(self.ts)
        if any(D(p['quantity'])>0 for p in self.broker.positions.values()):
            raise RuntimeError('practice liquidation failed; original account remains open for review')
        if self.status!='finished':
            self.status = 'stopped'
        self.store.append('practice','run_closed',{'reason':reason},recv_utc_ns=self.ts*1_000_000)
        self._last_snapshot = self.snapshot() | {'closed':True,'close_reason':reason}
        self._write_summary()
        self.store.close()
        self.store = None

    def close(self):
        if self.store is not None:
            self._archive('controller_closed')
        self._closed = True
