"""Reproduce a labeled synthetic 65% / 45c entry-profit-pullback cycle."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL,uuid5

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.book import Book
from backend.domain import D,Market,OrderIntent,Settings
from backend.execution import PaperBroker
from backend.fees import FeeSchedule
from backend.store import Store
from backend.strategy import ProfitPolicy,StrategyLedger,decide_trade


def run(database):
    if Path(database).exists():raise ValueError('choose a new isolated demonstration database')
    cfg=Settings(database=str(database),source='mock',mode='replay')
    store=Store(str(database));broker=PaperBroker(store,cfg);book=Book()
    policy=ProfitPolicy(cooldown_ms=10000,reentry_pullback='.04')
    market=Market(ticker='SYNTHETIC-CYCLE',event_ticker='FIXTURE',status='active',
        price_ranges=[{'start':'0','end':'1','step':'.01'}])
    trace=[];last_attempt=0
    try:
        for seconds,bid,ask in [(0,'.43','.45'),(1,'.55','.57'),(12,'.49','.51'),(13,'.42','.44'),(14,'.54','.56')]:
            ts=1800000000000+seconds*1000
            book.snapshot([[bid,'100']],[[str(1-D(ask)),'100']],ts)
            context=dict(market=market,book=book,mapping={'confirmed':True,'bout_id':'fixture','card_id':'fixture'},
                fight=SimpleNamespace(status='scheduled',stale=False,received_ms=ts,clock_remaining=300),
                clock_offset_ms=0,exchange_active=True,model_ready=True,fees=FeeSchedule(verified_ms=ts))
            choice=decide_trade(broker,market.ticker,context,cfg,policy,.65,ts,last_attempt)
            if choice['action'] in ('BUY','SELL'):
                intent=OrderIntent(id=uuid5(NAMESPACE_URL,f'synthetic-cycle:{seconds}'),ticker=market.ticker,
                    outcome=choice['outcome'],action=choice['action'].lower(),count=choice['quantity'],limit_price=choice['limit'])
                broker.submit(intent,ts,context);last_attempt=ts
                # Actual IOC arrival 250ms later against the still-executable fixture book.
                broker.process(ts+cfg.order_latency_ms,lambda _:context)
            trace.append({'elapsed_s':seconds,'probability':.65,'bid':bid,'ask':ask,
                'action':choice['action'],'reason':choice['reasons'],'filled_orders':broker.orders(),
                'portfolio':broker.summary({market.ticker:book})})
        report={'source':'SYNTHETIC chronological quotes, fixed model fixture; NOT historical returns',
            'policy':policy.model_dump(mode='json'),'trace':trace,
            'strategy':StrategyLedger(broker,market.ticker).snapshot(book,context['fees'],cfg,policy,.65),
            'journal_valid':store.verify(),'mode':'PAPER','live_enabled':False}
        store.append('synthetic_validation','report',report)
        return report
    finally:store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',default='work/profit-cycle.sqlite3')
    parser.add_argument('--output',default='work/profit-cycle-report.json')
    args=parser.parse_args();report=run(args.database)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'source':report['source'],'actions':[r['action'] for r in report['trace']],
        'completed_cycles':report['strategy']['completed_cycles'],'report':str(path)}))
