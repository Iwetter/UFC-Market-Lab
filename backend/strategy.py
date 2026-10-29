"""Shared deterministic PAPER profit-taking policy, reconstructed from actual fills.

Decisions never submit orders or bypass execution risk gates. A profitable sale is
a configurable cash-profit objective; the holding comparison remains visible.
"""
from __future__ import annotations

from decimal import InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from pydantic import Field

from .domain import D, ONE, ZERO, StrictModel
from .execution import date_utc


class ProfitPolicy(StrictModel):
    min_entry_edge: D = Field(default=D('.05'), ge=0, le=D('.5'))
    min_exit_edge: D = Field(default=D('.02'), ge=0, le=D('.5'))
    probability_buffer: D = Field(default=D('.05'), ge=0, le=D('.4'))
    cooldown_ms: int = Field(default=60000, ge=0, le=86400000)
    max_orders_per_market_day: int | None = Field(default=20, ge=1, le=10000)
    contracts: int = Field(default=1, ge=1, le=1000)
    net_profit_target_per_contract: D = Field(default=D('.03'), gt=0, le=1)
    profit_take_fraction: D = Field(default=ONE, gt=0, le=1)
    partial_profit_step: D = Field(default=D('.02'), gt=0, le=1)
    reentry_pullback: D = Field(default=D('.02'), ge=0, lt=1)
    max_position_contracts: int = Field(default=3, ge=1, le=1000)
    scale_in_enabled: bool = False
    scale_in_pullback: D = Field(default=D('.02'), ge=0, lt=1)
    min_hold_edge: D | None = Field(default=None, ge=-1, le=1)


def _probability(value):
    try:
        p = D(str(value))
        return p if p.is_finite() and ZERO <= p <= ONE else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _limit(market, price, outcome, buy):
    """Round the YES-leg grid in the conservative execution direction."""
    target = price if outcome == 'yes' else ONE-price
    choices = []
    for band in market.price_ranges:
        rounding = ROUND_CEILING if buy == (outcome == 'yes') else ROUND_FLOOR
        band_target = max(target, band.start) if rounding == ROUND_CEILING else min(target, band.end)
        yes = band.start+((band_target-band.start)/band.step).to_integral_value(rounding=rounding)*band.step
        effective = yes if outcome == 'yes' else ONE-yes
        if market.on_grid(yes) and (effective >= price if buy else effective <= price):
            choices.append(effective)
    return (min(choices) if buy else max(choices)) if choices else None


def _fee(fees, price, quantity):
    # Includes the broker's cent cash alignment, rather than just the raw formula.
    return fees.fee(price, quantity).quantize(D('.01'), rounding=ROUND_CEILING)


def _size(market, amount):
    quantum = D('.01') if market.fractional_trading_enabled else ONE
    return max(ZERO, (amount/quantum).to_integral_value(rounding=ROUND_FLOOR)*quantum)


def _quote(book, market, cfg, outcome, buy):
    levels = book.asks(outcome) if buy else book.bids(outcome)
    if not levels:
        return None, ZERO
    price, depth = levels[0]
    if not price.is_finite() or not depth.is_finite() or depth <= 0:
        return None, ZERO
    return _limit(market, price+cfg.slippage if buy else price-cfg.slippage, outcome, buy), depth


def _strings(value):
    if isinstance(value, D):
        return str(value)
    if isinstance(value, dict):
        return {k: _strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strings(v) for v in value]
    return value


class StrategyLedger:
    """Read-only fill reconstruction; rejected/unfilled orders create no anchors."""
    def __init__(self, broker, ticker):
        self.broker, self.ticker = broker, ticker

    def _history(self):
        cycles, current, last_exit, last_entry, orders = [], {}, {}, {}, {}
        # SQLite insertion order breaks equal-timestamp ties, including multi-level fills.
        fills = [f for f in self.broker.store.bodies('fills') if f['ticker'] == self.ticker]
        fills = sorted(fills, key=lambda f: f['ts_ms'])
        for sequence, fill in enumerate(fills):
            outcome, q, price = fill['outcome'], D(fill['count']), D(fill['price'])
            if q <= 0:
                continue
            buy = fill['action'] == 'buy'
            cash = D(fill['cash_change'])
            order = orders.setdefault(fill['order_id'], {'quantity': ZERO, 'notional': ZERO, 'net': ZERO, 'realized': ZERO})
            order['quantity'] += q
            order['notional'] += price*q
            order['net'] += -cash if buy else cash
            anchor = {'order_id': fill['order_id'], 'ts_ms': fill['ts_ms'],
                      'fill_sequence': sequence,
                      'quantity': order['quantity'], 'price': order['notional']/order['quantity'],
                      'net_price': order['net']/order['quantity']}
            (last_entry if buy else last_exit)[outcome] = anchor
            if buy and outcome not in current:
                cycle = {'outcome': outcome, 'started_ms': fill['ts_ms'], 'closed_ms': None,
                         'entry_quantity': ZERO, 'exit_quantity': ZERO, 'entry_notional': ZERO,
                         'exit_notional': ZERO, 'entry_cost': ZERO, 'exit_net': ZERO,
                         'entry_fees': ZERO, 'exit_fees': ZERO, 'realized_pnl': ZERO,
                         'open_quantity': ZERO, 'open_cost_basis': ZERO, 'completed': False}
                cycles.append(cycle)
                current[outcome] = cycle
            cycle = current.get(outcome)
            if cycle is None:
                continue  # An incomplete imported ledger cannot invent a cost basis.
            if buy:
                cycle['entry_quantity'] += q
                cycle['entry_notional'] += price*q
                cycle['entry_cost'] -= cash
                cycle['entry_fees'] += -cash-price*q
                cycle['open_quantity'] += q
                cycle['open_cost_basis'] -= cash
            else:
                if q > cycle['open_quantity']:
                    continue
                released = cycle['open_cost_basis']*q/cycle['open_quantity']
                order['realized'] += cash-released
                anchor['realized_profit_per_contract'] = order['realized']/order['quantity']
                cycle['exit_quantity'] += q
                cycle['exit_notional'] += price*q
                cycle['exit_net'] += cash
                cycle['exit_fees'] += price*q-cash
                cycle['realized_pnl'] += cash-released
                cycle['open_quantity'] -= q
                cycle['open_cost_basis'] -= released
                if not cycle['open_quantity']:
                    cycle.update(completed=True, closed_ms=fill['ts_ms'])
                    del current[outcome]
        for cycle in cycles:
            cycle['avg_entry_price'] = cycle['entry_notional']/cycle['entry_quantity']
            cycle['avg_entry_cost'] = cycle['entry_cost']/cycle['entry_quantity']
            cycle['avg_exit_price'] = cycle['exit_notional']/cycle['exit_quantity'] if cycle['exit_quantity'] else None
            cycle['avg_exit_net'] = cycle['exit_net']/cycle['exit_quantity'] if cycle['exit_quantity'] else None
        return cycles, last_exit, last_entry

    def snapshot(self, book, fees, cfg, policy, p_yes):
        cycles, last_exit, last_entry = self._history()
        positions, unrealized = [], ZERO
        known_marks = True
        probability = _probability(p_yes)
        for pos in self.broker.positions.values():
            if pos['ticker'] != self.ticker or D(pos['quantity']) <= 0:
                continue
            q, basis, outcome = D(pos['quantity']), D(pos['cost_basis']), pos['outcome']
            avg = basis/q
            # The public snapshot does not need market metadata; NO complement grid
            # rounding is performed again by decide_trade using the actual market.
            levels = book.bids(outcome) if book else []
            price = max(ZERO, levels[0][0]-cfg.slippage) if levels else None
            # Cent-align net proceeds downward, covering both fees and cash rounding.
            net = ((price*q-_fee(fees, price, q)).quantize(D('.01'), rounding=ROUND_FLOOR)
                   if price is not None and ZERO < price < ONE and fees else None)
            profit = net-basis if net is not None else None
            fair = (probability if outcome == 'yes' else ONE-probability) if probability is not None else None
            holding = fair-policy.probability_buffer if fair is not None else None
            if profit is None:
                known_marks = False
            else:
                unrealized += profit
            positions.append(pos | {'avg_entry_cost': avg, 'estimated_exit_price': price,
                'estimated_exit_net': net, 'estimated_exit_net_per_contract': net/q if net is not None else None,
                'estimated_exit_profit': profit, 'estimated_exit_profit_per_contract': profit/q if profit is not None else None,
                'net_profit_target_per_contract': policy.net_profit_target_per_contract,
                'profit_target_net_price': avg+policy.net_profit_target_per_contract,
                'profit_target_reached': profit is not None and profit/q >= policy.net_profit_target_per_contract,
                'holding_value_after_buffer': holding,
                'holding_advantage': holding-net/q if holding is not None and net is not None else None,
                'mark_stale': not book or book.stale})
        reentry = {}
        for outcome, anchor in last_exit.items():
            asks = book.asks(outcome) if book else []
            price = asks[0][0]+cfg.slippage if asks else None
            maximum = anchor['price']-policy.reentry_pullback
            reentry[outcome] = {'last_exit_price': anchor['price'], 'maximum_entry_price': maximum,
                'current_entry_price': price, 'pullback_met': price is not None and price <= maximum,
                'requires_current_entry_edge': True}
        realized = sum((D(p['realized_pnl']) for p in self.broker.positions.values() if p['ticker'] == self.ticker), ZERO)
        return _strings({'ticker': self.ticker, 'positions': positions,
            'cycles': [c for c in cycles if c['completed']], 'open_cycles': [c for c in cycles if not c['completed']],
            'completed_cycles': sum(c['completed'] for c in cycles), 'last_exit': last_exit,
            'last_entry': last_entry, 'reentry': reentry, 'realized_pnl': realized,
            'unrealized_pnl': unrealized if known_marks else None,
            'net_profit_target_per_contract': policy.net_profit_target_per_contract,
            'profit_take_fraction': policy.profit_take_fraction})


def decide_trade(broker, ticker, context, cfg, policy, p_yes, ts, last_attempt=0):
    """Choose a single trade; callers apply all their execution gates on submission."""
    book, fees, market = context.get('book'), context.get('fees'), context.get('market')
    metrics = StrategyLedger(broker, ticker).snapshot(book, fees, cfg, policy, p_yes)
    result = {'action': 'HOLD' if metrics['positions'] else 'NO-TRADE', 'reasons': [],
              'outcome': None, 'limit': None, 'quantity': None, 'net_edge_after_buffer': None,
              'metrics': metrics}
    def stop(*reasons):
        return result | {'reasons': list(reasons)}
    p = _probability(p_yes)
    if p is None:
        return stop('invalid_probability')
    if market is None or book is None or book.stale or not 0 <= ts-book.updated_ms <= cfg.max_book_age_ms:
        return stop('book_stale_or_missing')
    if fees is None or not 0 <= ts-fees.verified_ms <= 300000 or (fees.valid_until_ms is not None and ts >= fees.valid_until_ms):
        return stop('fees_unverified')
    orders = broker.orders()
    if any(o['status'] == 'pending' and o['intent']['ticker'] == ticker for o in orders):
        return stop('order_pending')
    positions = {x['outcome']: x for x in metrics['positions']}
    candidates = []
    # Reducing exits have priority and are independent of entry cooldown/order caps.
    for outcome, pos in positions.items():
        limit, depth = _quote(book, market, cfg, outcome, False)
        q, avg = D(pos['quantity']), D(pos['avg_entry_cost'])
        amount = q*policy.profit_take_fraction
        # A fractional take-profit on a whole-contract market must sell at least one.
        minimum = D('.01') if market.fractional_trading_enabled else ONE
        quantity = _size(market, min(q, max(minimum, amount), depth*cfg.max_participation, cfg.max_order_contracts))
        if limit is None or quantity <= 0:
            continue
        net = (limit*quantity-_fee(fees, limit, quantity)).quantize(D('.01'), rounding=ROUND_FLOOR)/quantity
        fair = p if outcome == 'yes' else ONE-p
        profit = net-avg
        holding_edge = fair-policy.probability_buffer-net
        rich_edge = net-fair-policy.probability_buffer
        comparison = {'estimated_exit_net_per_contract': net, 'estimated_exit_profit_per_contract': profit,
            'holding_value_after_buffer': fair-policy.probability_buffer, 'holding_advantage': holding_edge,
            'exit_rich_edge_after_buffer': rich_edge,
            'opportunity_cost_note': ('Selling realizes the configured cash profit; buffered holding value is higher.'
                                    if holding_edge > 0 else 'Executable net sale value meets or exceeds buffered holding value.')}
        metrics.setdefault('exit_comparisons', {})[outcome] = _strings(comparison)
        previous_exit, previous_entry = metrics['last_exit'].get(outcome), metrics['last_entry'].get(outcome)
        fresh_profit_level = not (previous_exit and previous_entry
            and previous_exit['fill_sequence'] > previous_entry['fill_sequence']
            and D(previous_exit.get('realized_profit_per_contract', '0')) > 0
            and net < D(previous_exit['net_price'])+policy.partial_profit_step)
        comparison = metrics['exit_comparisons'][outcome]
        comparison['partial_profit_step_met'] = fresh_profit_level
        if profit >= policy.net_profit_target_per_contract and fresh_profit_level:
            candidates.append((2, profit, outcome, limit, quantity, ['net_profit_target_reached'], rich_edge))
        else:
            # Risk reduction uses the whole allowed size, with its own fee calculation.
            reduce_quantity = _size(market, min(q, depth*cfg.max_participation, cfg.max_order_contracts))
            reduce_net = (limit*reduce_quantity-_fee(fees, limit, reduce_quantity)).quantize(D('.01'), rounding=ROUND_FLOOR)/reduce_quantity
            reduce_rich_edge = reduce_net-fair-policy.probability_buffer
            reduce_holding_edge = fair-policy.probability_buffer-reduce_net
            if reduce_rich_edge >= policy.min_exit_edge or (policy.min_hold_edge is not None and reduce_holding_edge < policy.min_hold_edge):
                candidates.append((1, reduce_rich_edge, outcome, limit, reduce_quantity, ['holding_edge_below_policy_threshold'], reduce_rich_edge))
    if candidates:
        _, _, outcome, limit, quantity, reasons, edge = max(candidates)
        return result | _strings({'action': 'SELL', 'outcome': outcome, 'limit': limit, 'quantity': quantity,
                                 'reasons': reasons, 'net_edge_after_buffer': edge})
    last = last_attempt.get(ticker, 0) if isinstance(last_attempt, dict) else last_attempt
    if last and ts-last < policy.cooldown_ms:
        return stop('cooldown')
    cap = policy.max_orders_per_market_day
    if cap is not None and sum(o['intent']['ticker'] == ticker and date_utc(o['created_ms']) == date_utc(ts) for o in orders) >= cap:
        return stop('daily_market_order_cap')
    reasons = []
    exposure = sum((D(x['quantity']) for x in positions.values()), ZERO)
    if positions and not policy.scale_in_enabled:
        return stop('holding_position_scale_in_disabled')
    if exposure >= policy.max_position_contracts:
        return stop('position_contract_cap')
    for outcome in ('yes', 'no'):
        if positions and outcome not in positions:
            continue
        limit, depth = _quote(book, market, cfg, outcome, True)
        quantity = _size(market, min(D(policy.contracts), depth*cfg.max_participation,
                                    cfg.max_order_contracts, D(policy.max_position_contracts)-exposure))
        if limit is None or quantity <= 0:
            reasons.append('no_executable_depth_or_grid')
            continue
        fair = p if outcome == 'yes' else ONE-p
        entry_cost = (limit*quantity+_fee(fees, limit, quantity)).quantize(D('.01'), rounding=ROUND_CEILING)/quantity
        edge = fair-entry_cost-policy.probability_buffer
        if edge < policy.min_entry_edge:
            reasons.append('no_net_edge_after_costs_and_buffer')
            continue
        anchor = metrics['last_entry'].get(outcome) if positions else metrics['last_exit'].get(outcome)
        pullback = policy.scale_in_pullback if positions else policy.reentry_pullback
        if anchor and limit > D(anchor['price'])-pullback:
            reasons.append('scale_in_pullback_not_met' if positions else 'reentry_pullback_not_met')
            continue
        candidates.append((edge, outcome, limit, quantity, bool(anchor)))
    if not candidates:
        return stop(*(list(dict.fromkeys(reasons)) or ['no_net_edge_after_costs_and_buffer']))
    edge, outcome, limit, quantity, anchored = max(candidates)
    reason = 'scale_in_pullback_and_current_net_edge' if positions else 'reentry_pullback_and_current_net_edge' if anchored else 'current_net_edge_exceeds_policy_threshold'
    return result | _strings({'action': 'BUY', 'outcome': outcome, 'limit': limit, 'quantity': quantity,
                             'reasons': [reason], 'net_edge_after_buffer': edge})
