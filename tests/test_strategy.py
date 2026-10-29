"""Synthetic policy scenarios verify accounting, never historical returns."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.book import Book
from backend.domain import D, Market, OrderIntent, Settings
from backend.execution import PaperBroker, risk_reasons
from backend.fees import FeeSchedule
from backend.models import FightState
from backend.store import Store
from backend.strategy import ProfitPolicy, StrategyLedger, decide_trade


def environment(tmp_path, **settings):
    cfg = Settings(order_latency_ms=0, slippage='0', **settings)
    broker = PaperBroker(Store(str(tmp_path/'strategy.db')), cfg)
    market = Market(ticker='X', event_ticker='E', status='active',
                    price_ranges=[{'start':'0','end':'1','step':'.01'}])
    context = {'market':market, 'book':Book(), 'fees':FeeSchedule(verified_ms=1000),
               'mapping':{'confirmed':True,'bout_id':'B','card_id':'E'},
               'fight':FightState('B', received_ms=1000, stale=False),
               'clock_offset_ms':0, 'model_ready':True}
    quote(context, 1000, '.43', '.45')
    return broker, context, cfg


def quote(context, ts, bid, ask, outcome='yes', depth='100'):
    yes, no = (D(bid), 1-D(ask)) if outcome == 'yes' else (1-D(ask), D(bid))
    context['book'].snapshot([[str(yes),depth]], [[str(no),depth]], ts)
    context['fight'].received_ms = ts
    context['fees'] = FeeSchedule(verified_ms=ts)


def execute(broker, context, ts, decision):
    intent = OrderIntent(id=uuid4(), ticker='X', outcome=decision['outcome'],
                         action=decision['action'].lower(), count=decision['quantity'],
                         limit_price=decision['limit'])
    order = broker.submit(intent, ts, context)
    assert order['status'] == 'pending', order
    broker.process(ts, lambda _:context)
    return broker.orders()[-1]


def choose(broker, context, cfg, ts, policy=None, p='.65', last_attempt=0):
    return decide_trade(broker, 'X', context, cfg, policy or ProfitPolicy(), p, ts, last_attempt)


def test_favored_profit_exit_pullback_reentry_and_multiple_cycles(tmp_path):
    broker, c, cfg = environment(tmp_path)
    policy = ProfitPolicy(max_orders_per_market_day=2)
    entry = choose(broker, c, cfg, 1000, policy)
    assert entry['action'] == 'BUY' and D(entry['limit']) == D('.45')
    execute(broker, c, 1000, entry)
    assert D(broker.positions['X|yes']['cost_basis']) == D('.47')
    quote(c, 2000, '.52', '.54')
    sale = choose(broker, c, cfg, 2000, policy, last_attempt=1000)
    assert sale['action'] == 'SELL' and sale['reasons'] == ['net_profit_target_reached']
    comparison = sale['metrics']['exit_comparisons']['yes']
    assert D(comparison['holding_advantage']) > 0  # Still favored; cash target is explicit.
    execute(broker, c, 2000, sale)
    # Clearing the cap does not force a trade: even a good current edge needs a pullback.
    policy = ProfitPolicy(cooldown_ms=0)
    quote(c, 3000, '.49', '.51')
    blocked = choose(broker, c, cfg, 3000, policy)
    assert blocked['action'] == 'NO-TRADE' and 'reentry_pullback_not_met' in blocked['reasons']
    quote(c, 4000, '.42', '.44')
    buy = choose(broker, c, cfg, 4000, policy)
    assert buy['action'] == 'BUY' and 'reentry' in buy['reasons'][0]
    execute(broker, c, 4000, buy)
    quote(c, 5000, '.53', '.55')
    execute(broker, c, 5000, choose(broker, c, cfg, 5000, policy))
    snapshot = StrategyLedger(broker, 'X').snapshot(c['book'], c['fees'], cfg, policy, '.65')
    assert snapshot['completed_cycles'] == 2 and len(snapshot['cycles']) == 2
    assert not snapshot['positions'] and D(snapshot['realized_pnl']) == D('.08')


def test_reentry_requires_independent_current_edge(tmp_path):
    broker, c, cfg = environment(tmp_path)
    execute(broker, c, 1000, choose(broker, c, cfg, 1000))
    quote(c, 2000, '.52', '.54')
    execute(broker, c, 2000, choose(broker, c, cfg, 2000))
    quote(c, 3000, '.42', '.44')
    result = choose(broker, c, cfg, 3000, p='.49')
    assert result['metrics']['reentry']['yes']['pullback_met']
    assert result['action'] == 'NO-TRADE'


def test_partial_profit_exit_and_restore_preserve_fee_inclusive_cycle(tmp_path):
    broker, c, cfg = environment(tmp_path)
    policy = ProfitPolicy(contracts=3, profit_take_fraction='.5')
    execute(broker, c, 1000, choose(broker, c, cfg, 1000, policy))
    quote(c, 2000, '.52', '.54')
    sale = choose(broker, c, cfg, 2000, policy)
    assert sale['action'] == 'SELL' and D(sale['quantity']) == 1
    execute(broker, c, 2000, sale)
    restored = PaperBroker(broker.store, cfg)
    ledger = StrategyLedger(restored, 'X')
    snapshot = ledger.snapshot(c['book'], c['fees'], cfg, policy, '.65')
    assert snapshot == StrategyLedger(broker, 'X').snapshot(c['book'], c['fees'], cfg, policy, '.65')
    assert snapshot['completed_cycles'] == 0 and snapshot['open_cycles'][0]['exit_quantity'] == '1'
    assert D(snapshot['positions'][0]['avg_entry_cost']) == D('.47')
    assert D(snapshot['realized_pnl']) == D('.03')
    # Entry cooldown and a reached order cap must never block reducing exits.
    quote(c, 3000, '.55', '.57')
    policy = ProfitPolicy(max_orders_per_market_day=1)
    final = choose(restored, c, cfg, 3000, policy, last_attempt=2999)
    assert final['action'] == 'SELL' and D(final['quantity']) == 2
    execute(restored, c, 3000, final)
    cycle = ledger.snapshot(c['book'], c['fees'], cfg, policy, '.65')['cycles'][0]
    assert D(cycle['entry_quantity']) == D(cycle['exit_quantity']) == 3
    assert D(cycle['realized_pnl']) == D(restored.positions['X|yes']['realized_pnl'])
    assert D(cycle['avg_exit_price']) == (D('.52')+2*D('.55'))/3


def test_unfilled_and_rejected_orders_never_manufacture_exit_anchor(tmp_path):
    broker, c, cfg = environment(tmp_path)
    execute(broker, c, 1000, choose(broker, c, cfg, 1000))
    quote(c, 2000, '.52', '.54')
    sale = choose(broker, c, cfg, 2000)
    quote(c, 2000, '.40', '.42')  # IOC limit now cannot fill.
    assert execute(broker, c, 2000, sale)['fill_count'] == '0'
    rejected = broker.submit(OrderIntent(id=uuid4(), ticker='X', outcome='yes', action='sell',
                                        count='2', limit_price='.52'), 2000, c)
    assert rejected['status'] == 'rejected'
    snapshot = StrategyLedger(broker, 'X').snapshot(c['book'], c['fees'], cfg, ProfitPolicy(), '.65')
    assert snapshot['last_exit'] == {} and snapshot['reentry'] == {} and snapshot['cycles'] == []


def test_actual_ioc_partial_fill_rebuilds_only_filled_quantity(tmp_path):
    broker, c, cfg = environment(tmp_path)
    policy = ProfitPolicy(contracts=3)
    entry = choose(broker, c, cfg, 1000, policy)
    assert D(entry['quantity']) == 3
    quote(c, 1000, '.43', '.45', depth='4')
    order = execute(broker, c, 1000, entry)
    assert order['status'] == 'canceled' and D(order['fill_count']) == 1
    restored = PaperBroker(broker.store, cfg)
    snapshot = StrategyLedger(restored, 'X').snapshot(c['book'], c['fees'], cfg, policy, '.65')
    assert D(snapshot['last_entry']['yes']['quantity']) == 1
    assert D(snapshot['open_cycles'][0]['entry_quantity']) == 1
    assert D(snapshot['positions'][0]['avg_entry_cost']) == D('.47')


def test_no_limit_rounds_on_yes_grid_and_band_gaps(tmp_path):
    broker, c, cfg = environment(tmp_path)
    cfg = cfg.model_copy(update={'slippage':D('.01')})
    c['market'] = c['market'].model_copy(update={'price_ranges': [
        # NO outcome prices are complements of this asymmetric YES grid.
        c['market'].price_ranges[0].model_copy(update={'start':D('.01'), 'end':D('.99'), 'step':D('.03')})]})
    quote(c, 1000, '.42', '.44', outcome='no')
    buy = choose(broker, c, cfg, 1000, p='.35')
    assert buy['outcome'] == 'no' and D(buy['limit']) >= D('.45')
    assert c['market'].on_grid(1-D(buy['limit']))
    # The nearest valid limit can be at the next band's boundary.
    c['market'] = c['market'].model_copy(update={'price_ranges': [
        c['market'].price_ranges[0].model_copy(update={'start':D('.01'), 'end':D('.4'), 'step':D('.01')}),
        c['market'].price_ranges[0].model_copy(update={'start':D('.5'), 'end':D('.99'), 'step':D('.01')})]})
    quote(c, 1000, '.43', '.45')
    buy = choose(broker, c, cfg, 1000, p='.75')
    assert buy['action'] == 'BUY' and D(buy['limit']) == D('.5')


def test_no_side_has_same_profit_and_reentry_rules(tmp_path):
    broker, c, cfg = environment(tmp_path)
    quote(c, 1000, '.43', '.45', outcome='no')
    entry = choose(broker, c, cfg, 1000, p='.35')
    assert entry['action'] == 'BUY' and entry['outcome'] == 'no'
    execute(broker, c, 1000, entry)
    quote(c, 2000, '.52', '.54', outcome='no')
    sale = choose(broker, c, cfg, 2000, p='.35')
    assert sale['action'] == 'SELL' and sale['outcome'] == 'no'
    execute(broker, c, 2000, sale)
    quote(c, 3000, '.42', '.44', outcome='no')
    assert choose(broker, c, cfg, 3000, p='.35')['action'] == 'BUY'


def test_scaling_requires_opt_in_pullback_and_total_position_cap(tmp_path):
    broker, c, cfg = environment(tmp_path)
    execute(broker, c, 1000, choose(broker, c, cfg, 1000))
    quote(c, 2000, '.39', '.41')
    assert choose(broker, c, cfg, 2000)['action'] == 'HOLD'
    policy = ProfitPolicy(scale_in_enabled=True, max_position_contracts=2)
    execute(broker, c, 2000, choose(broker, c, cfg, 2000, policy))
    quote(c, 3000, '.35', '.37')
    blocked = choose(broker, c, cfg, 3000, policy)
    assert blocked['action'] == 'HOLD' and blocked['reasons'] == ['position_contract_cap']


def test_fractional_exit_size_and_cent_cash_cost_are_conservative(tmp_path):
    broker, c, cfg = environment(tmp_path)
    c['market'].fractional_trading_enabled = True
    quote(c, 1000, '.43', '.45', depth='1')
    policy = ProfitPolicy(profit_take_fraction='.5')
    entry = choose(broker, c, cfg, 1000, policy)
    assert D(entry['quantity']) == D('.25')
    execute(broker, c, 1000, entry)
    actual_cost = D(broker.positions['X|yes']['cost_basis'])/D('.25')
    assert D(entry['net_edge_after_buffer']) <= D('.65')-actual_cost-policy.probability_buffer
    quote(c, 2000, '.68', '.70')
    sale = choose(broker, c, cfg, 2000, policy)
    assert sale['action'] == 'SELL' and D(sale['quantity']) == D('.12')
    before = D(broker.positions['X|yes']['realized_pnl'])
    # Fractional fills must amortize cent rounding using their actual proposed size.
    assert D(sale['metrics']['exit_comparisons']['yes']['estimated_exit_profit_per_contract']) >= D('.03')
    execute(broker, c, 2000, sale)
    assert D(broker.positions['X|yes']['realized_pnl'])-before >= D('.03')*D(sale['quantity'])


def test_partial_profit_requires_fresh_price_step_but_risk_exit_can_reduce(tmp_path):
    broker, c, cfg = environment(tmp_path)
    policy = ProfitPolicy(contracts=3, profit_take_fraction='.5')
    execute(broker, c, 1000, choose(broker, c, cfg, 1000, policy))
    quote(c, 2000, '.52', '.54')
    execute(broker, c, 2000, choose(broker, c, cfg, 2000, policy))
    quote(c, 3000, '.52', '.54')
    repeat = choose(broker, c, cfg, 3000, policy)
    assert repeat['action'] == 'HOLD'
    assert not repeat['metrics']['exit_comparisons']['yes']['partial_profit_step_met']
    # A model deterioration can still reduce all risk despite the repeat-profit guard.
    risk = choose(broker, c, cfg, 3000, policy, p='.35')
    assert risk['action'] == 'SELL' and D(risk['quantity']) == 2
    quote(c, 4000, '.54', '.56')
    fresh = choose(broker, c, cfg, 4000, policy)
    assert fresh['action'] == 'SELL' and D(fresh['quantity']) == 1


def test_tiny_profit_fraction_does_not_block_full_risk_reduction(tmp_path):
    broker, c, cfg = environment(tmp_path)
    c['market'].fractional_trading_enabled = True
    quote(c, 1000, '.43', '.45', depth='1')
    policy = ProfitPolicy(profit_take_fraction='.1')
    execute(broker, c, 1000, choose(broker, c, cfg, 1000, policy))
    quote(c, 2000, '.90', '.92')
    sale = choose(broker, c, cfg, 2000, policy)
    assert sale['action'] == 'SELL' and D(sale['quantity']) == D('.25')


def test_stale_and_fee_gates_remain_separate_from_submission_risk(tmp_path):
    broker, c, cfg = environment(tmp_path)
    decision = choose(broker, c, cfg, 1000)
    c['clock_offset_ms'] = None
    # The pure policy can explain the opportunity; execution must still fail closed.
    assert choose(broker, c, cfg, 1000)['action'] == 'BUY'
    intent = OrderIntent(id=uuid4(), ticker='X', outcome='yes', count=decision['quantity'], limit_price=decision['limit'])
    assert 'clock_unverified' in risk_reasons(intent, cfg=cfg, ts=1000, **c)
    c['book'].stale = True
    assert choose(broker, c, cfg, 1000)['reasons'] == ['book_stale_or_missing']
    c['book'].stale = False
    c['fees'] = None
    assert choose(broker, c, cfg, 1000)['reasons'] == ['fees_unverified']
    assert StrategyLedger(broker, 'X').snapshot(c['book'], None, cfg, ProfitPolicy(), None)['positions'] == []


@pytest.mark.parametrize('p', [None, 'nan', 'Infinity', '-.01', '1.01', 'garbage'])
def test_invalid_probability_is_no_trade(tmp_path, p):
    broker, c, cfg = environment(tmp_path)
    assert choose(broker, c, cfg, 1000, p=p)['reasons'] == ['invalid_probability']


@pytest.mark.parametrize('settings', [{'profit_take_fraction':'0'}, {'reentry_pullback':'NaN'},
                                     {'net_profit_target_per_contract':'Infinity'}, {'max_position_contracts':0}])
def test_invalid_policy_rejected(settings):
    with pytest.raises(ValidationError):
        ProfitPolicy(**settings)
