# Entry, profit taking and pullback re-entry

Historical Practice and Live PAPER now use the same deterministic policy in
`backend/strategy.py`. They retain separate accounts, clocks and execution/data
gates. No model training or dataset downloads are required for this correction.

Entry compares the outcome's current model probability with a grid-valid executable
ask plus slippage, conservative rounded fees and an uncertainty buffer. A positive
displayed midpoint difference is insufficient. Entry size and total quantity are
capped. Scaling is optional and requires an independent qualifying edge and a
pullback from the last actual entry fill.

A holding's average cost includes actual entry cash debits and fees. A profit exit
compares conservative executable bid proceeds after exit fees/slippage with that
average cost. Clearing the configurable net-profit target can trigger a full or
partial sale **without any bearish probability change**. The decision also reports
buffered holding value, immediate liquidation value and the opportunity cost of
selling. The profit target is a user policy objective, not a proof that selling
maximizes expected terminal wealth. Risk-reduction exits can also trigger when
holding edge falls below the configured threshold.

Re-entry requires both a fresh entry edge and a sufficiently lower executable
price than the last actual sale. Pending, rejected and unfilled orders never create
entry/exit anchors. Anchors and weighted-cost cycles are reconstructed from the
persistent fill ledger, so restart cannot forget the last sale and chase a worse
price. Cooldowns and daily order caps restrict **entries**, not reducing exits.
Repeated partial profit exits require an additional configurable improvement in
net sale price (default 2 cents), so an unchanged bid does not drain a position
every tick. Risk-reduction exits remain independent of that profit-step guard.
Order caps are safety ceilings, never goals. There is no required trade count.

Use **Profit taking and re-entry** on Practice or Live PAPER to change the net
target, exit fraction, pullback, sizing and scaling controls. Applying settings
does not train a model or authorize live funds. Fractions respect market quantity
increments; a whole-contract market must exit at least one contract. Partial
sales retain their remaining cost basis, and complete buy-to-flat cycles show
their actual net cash result separately from open-cycle sales.

## Reproduce the chronological execution fixture

```powershell
python scripts/validate_profit_cycle.py
```

The command creates a new isolated SQLite journal and a JSON report under `work`.
If that database already exists, choose another `--database` path; it never
overwrites a previous run. This fixed synthetic scenario uses a 65% model
probability, a 4-cent re-entry pullback and a 3-cent net profit target:

| Virtual seconds | Bid / ask | Decision | Execution including 1-cent slippage |
| ---: | --- | --- | --- |
| 0 | 43¢ / 45¢ | BUY YES | 46¢ fill + 2¢ rounded entry fees = 48¢ cost |
| 1 | 55¢ / 57¢ | SELL YES | 54¢ fill − 2¢ exit fees = 52¢ net; 4¢ gain |
| 12 | 49¢ / 51¢ | WAIT | Current edge exists, but required pullback is not met |
| 13 | 42¢ / 44¢ | BUY YES | 45¢ fill + 2¢ fees = 47¢ cost |
| 14 | 54¢ / 56¢ | SELL YES | 53¢ fill − 2¢ fees = 51¢ net; 4¢ gain |

Each IOC arrives 250 simulated milliseconds after its decision and uses the
arrival book. The fixture holds the quote through arrival; separate broker tests
cover changed prices, depth restrictions and canceled/partial fills. The same
sequence is exercised through both actual mode integrations. These are **synthetic
gains**, not recorded exchange returns. No historical profitability is claimed.

## Probability and operating limits

The fitted historical pre-fight model stays available with its original provenance
and validation limitations. Practice deliberately holds this estimate fixed while
revealing real round aggregates; it does not treat those aggregates as a trained
in-fight win model. Live PAPER still stops before the reviewed earliest start,
or when actual fight-start evidence arrives. Reliable timestamped fight information
and a separately validated model are still required for real in-fight probability
updates and execution. This correction does not enable speculative live-fight
trading with a pre-fight probability.

Real mode still checks mappings, exact rules, lifecycle, depth, fee freshness,
clock uncertainty, feed freshness and loss/exposure limits at submission and
arrival. Practice uses its explicit synthetic simulation context. Unreliable
data, risk stops or no qualifying edge can produce no trades. Real-money trading
and Polymarket execution remain disabled/unimplemented as documented previously.
