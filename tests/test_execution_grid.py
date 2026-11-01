"""Synthetic gap-grid cases: order limits and fills must use the same price grid."""
from uuid import uuid4

import pytest

from backend.domain import D,OrderIntent,PriceBand
from backend.strategy import _limit
from test_execution import setup


@pytest.mark.parametrize('outcome,action,bid,ask,expected',[
    ('yes','buy','.39','.40','.50'),
    ('no','buy','.49','.50','.60'),
    ('yes','sell','.50','.51','.40'),
    ('no','sell','.60','.61','.50'),
])
def test_gap_boundary_fills_at_conservative_shared_grid(tmp_path,outcome,action,bid,ask,expected):
    broker,c=setup(tmp_path)
    # Establish a genuine simulated holding through the broker before a sale.
    if action=='sell':
        c['book'].snapshot([['.19','100']],[['.79','100']],1000)
        entry=OrderIntent(id=uuid4(),ticker='X',outcome=outcome,count='2',limit_price='.9')
        assert broker.submit(entry,1000,c)['status']=='pending'
        broker.process(1250,lambda _:c)
        assert D(broker.positions[f'X|{outcome}']['quantity'])==2
    c['market'].price_ranges=[PriceBand(start='.01',end='.40',step='.01'),PriceBand(start='.50',end='.99',step='.01')]
    if outcome=='yes':yes,no=D(bid),1-D(ask)
    else:yes,no=1-D(ask),D(bid)
    c['book'].snapshot([[str(yes),'100']],[[str(no),'100']],2000)
    c['fight'].received_ms=2000
    price=D(ask) if action=='buy' else D(bid)
    target=price+broker.cfg.slippage if action=='buy' else price-broker.cfg.slippage
    limit=_limit(c['market'],target,outcome,action=='buy')
    assert limit==D(expected)
    order=OrderIntent(id=uuid4(),ticker='X',outcome=outcome,action=action,count='2',limit_price=str(limit))
    assert broker.submit(order,2000,c)['status']=='pending'
    broker.process(2250,lambda _:c)
    result=broker.orders()[-1]
    assert result['status']=='executed' and D(result['fill_count'])==2
    assert D(result['average_fill_price'])==limit
    assert c['market'].on_grid(limit if outcome=='yes' else 1-limit)
    broker.store.close()


def test_gap_does_not_override_a_tighter_valid_limit(tmp_path):
    broker,c=setup(tmp_path)
    c['market'].price_ranges=[PriceBand(start='.01',end='.40',step='.01'),PriceBand(start='.50',end='.99',step='.01')]
    c['book'].snapshot([['.39','100']],[['.60','100']],1000)
    order=OrderIntent(id=uuid4(),ticker='X',outcome='yes',count='1',limit_price='.40')
    cash=broker.cash
    assert broker.submit(order,1000,c)['status']=='pending'
    broker.process(1250,lambda _:c)
    assert broker.orders()[-1]['status']=='canceled'
    assert broker.store.bodies('fills')==[] and broker.cash==cash
    broker.store.close()
