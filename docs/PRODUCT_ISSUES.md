# Why the current product feels broken

The user wants to select an old UFC fight, press Play, and watch the bot predict,
buy, sell and explain itself. The existing product does not provide that flow.
Passing software tests did not establish that this user task was achievable.

## Implemented correction — October 29

The new home screen now implements the historical Practice flow below. It has searchable real bouts, Start/Pause/Resume/Step/speed controls, fixed trained predictions, visible automatic entry/exit decisions, fills, holdings and fake-money P&L, plus an end-of-replay result reveal. The issues table records the previous user-blocking behavior and its required correction. True historical market books and a trained in-fight model remain unavailable. See [how to run Practice](PRACTICE.md).

## Observed failures

| Problem | Effect on the user | Required correction |
| --- | --- | --- |
| Updates previously remained only on the development branch/PR. | Pulling main could show an older version and make completed work appear absent. | Resolved: PR #1 is merged into main; normal clone/start instructions now include historical Practice. |
| The default screen shows a fictional fight or live quotes, with no historical practice entry point. | The user cannot find or observe the bot they requested. | Make **Practice** the entry screen, with a real historical bout picker and Start button. |
| **Simulate buy** is a manual order button, although it sounds like running the bot. | The user clicks individual orders and expects autonomous decisions. | Label manual orders explicitly; put automatic strategy controls and status together. |
| Model research, market viewing, mapping and automation are separate workflows. | Configuration looks like unfinished functionality; no clear next action is visible. | Provide a complete practice session without keys, mapping forms, external schedule input or real clock synchronization. |
| Stale quotes, missing mappings, no edge, paused automation and clock failures can all result in no trades. | A correctly blocked or idle strategy looks identical to a broken bot. | Show plain-language status, the latest decision and reason, and a visible event/order timeline. |
| Trades, model predictions and portfolio results require switching views. | The user cannot judge why a trade happened or what followed it. | Show probabilities, quotes, BUY/SELL/HOLD, fills, positions and P&L on one screen. |
| The current automated strategy is pre-fight only. | A user watching a fight expects responses to live developments that this model cannot provide. | State the model scope directly. A historical practice replay must not claim a calibrated in-fight model. |
| Historical fights exist, but synchronized historical exchange books do not. | A replay could misleadingly look like evidence of past trading returns. | Label synthetic practice prices beside the chart, trades and results; never call them recorded Kalshi/Polymarket prices. |

## Historical practice implementation target

Select an eligible real historical fight after the model's training/calibration
cutoff and generate its prediction using only earlier reconstructed history.
Press **Start practice** to watch a deterministic accelerated run, with Pause,
Resume and Step controls. The winner stays hidden until the end.

The pre-fight probability stays fixed. Real completed-round observations may be
revealed only at their recorded round boundaries. There is no per-second event
feed or fight video in the cached data. Do not invent knockdown times, position
changes, within-round observations or an in-fight probability update.

The market is a plainly labeled synthetic price/depth scenario, generated without
using the winner or future fight statistics. It exercises autonomous decisions
and the existing PAPER broker's fees, slippage, latency, depth, positions and
reduce-only exits in a separate practice account. Buying and selling are not
guaranteed on every fight; no-edge and blocked decisions must be visible.

This lets the user judge prediction versus the actual result and inspect the
bot's execution behavior. Simulated P&L under synthetic prices is a scenario
result, not a historical trading backtest or evidence of profitable edge.

## Acceptance checks

- A new user can reach a running historical practice session from the home screen.
- It works with cached licensed/attributed public history and no exchange keys.
- Missing history has one clear setup command; startup never silently downloads or retrains.
- Automatic decisions, orders, fills, holdings and balance are visible together.
- Pause stops the virtual clock and new actions; Step advances one deterministic frame.
- Outcome/future statistics cannot influence earlier features, quotes or decisions.
- The practice account and journal cannot submit real orders or change the live-data paper account.
- Completion reveals the historical result and labels every synthetic assumption.
- Offline tests cover the user flow; browser verification checks what the user sees.

## Remaining limitations

Authenticated streams, licensed fast fight feeds, true historical order-book
replay, a trained in-fight strategy, Polymarket paper execution and prospective
trading validation remain separate work. The 12-second delay is unverified.
Real-money execution stays disabled. See PROGRESS.md for implementation status.
