# Watch the bot on a past UFC fight

Run from the project folder:

```powershell
python start.py --prepare-data
```

The home screen is **Practice**. Existing verified historical data is reused;
missing pinned source files are acquired only with `--prepare-data`. Startup does
not retrain models. You do not need exchange keys, a live event feed, fighter
mapping forms or host time synchronization for this isolated simulation.

1. Search a fighter name or date, then choose a historical fight. Eligible bouts
   occur after the selected model's training/calibration cutoff (2024 onward)
   and both fighters need sufficient earlier history. Up to 100 matches appear
   per search. The winner is hidden.
2. Choose playback speed and press **Start practice**. At 5×, fifteen minutes of
   active fight time takes roughly three minutes. A real recorded finish can
   end it sooner. There is no video, round-break timing or inferred event feed.
3. Watch the model probability, practice quotes and **What the bot is doing**.
   The bot places orders automatically. BUY opens a holding; SELL reduces that
   same outcome; HOLD/NO-TRADE explains why there is no new order.
4. Inspect **Bot activity** for each decision, limit, fill count, execution
   result and plain-language reason. Positions, fake cash, fees and P&L remain
   on the same page. Orders use the shared PAPER broker, including virtual
   250 ms arrival latency, depth, slippage, fees and position/risk caps.
5. **Pause bot** freezes replay time and new actions. **Step one event** pauses
   and advances one event: a strategy frame is at most 15 active fight seconds,
   while an order arrival can be a smaller step. **Resume bot** continues.
6. At completion, the actual result is revealed, outstanding orders are
   canceled and the hypothetical winner contract is settled. Review which
   fighter the model favored and every entry/exit. Start another run to compare.

## What is real and what is simulated

| Element | Basis |
| --- | --- |
| Fighters, bout date, result and recorded round aggregates | Pinned, hashed Noah Bair/UFCStats historical snapshot, CC BY 4.0 |
| Pre-fight probability | Actual fitted portable research model using reconstructed earlier history; selected-bout/future statistics excluded |
| Probability during playback | Fixed at its pre-fight value; no trained in-fight model is available |
| Statistics during a round | Hidden; only completed round checkpoints are revealed, with missing fields marked unavailable |
| Prices and depth | Explicitly synthetic deterministic scenario based on bout ID and elapsed time, independent of winner, model probability and future statistics |
| Fees and fills | Assumed practice fee schedule and simulated shared PAPER execution |
| Balance, positions and P&L | A separate fake-money practice account, never the real-data paper account |

The synthetic price path deliberately exercises changing market conditions. A
run may contain buys and sales, or no trade if the policy finds no qualifying
edge. It does not force orders just to make the screen look active. A five-order
run cap, cooldown and simulated risk limits can stop further entries.

This flow lets you judge how the bot responds to prices and compare its fixed
prediction with a historical result. **Practice P&L is not a historical exchange
return.** There are no synchronized historical Kalshi/Polymarket books in this
dataset. This current snapshot also reconstructs publication availability using
an assumed embargo, rather than observed historical receive times. The reused
holdout and synthetic fills cannot establish profitability or a latency lead.

## Saved runs and isolation

Each server-generated UUID run has a separate `practice.sqlite3`, `manifest.json`
and `summary.json` under the main database folder's `practice` subfolder. The
manifest records source/model hashes, policy, clock, quote assumptions and
licensing attribution. Replacing or closing a run cancels pending orders and
liquidates remaining simulated holdings using the current synthetic book, with
no future-outcome lookup. Restart does not silently resume a previous run.

No practice code imports the live-order gateway or uses exchange credentials.
The virtual clock is explicitly a simulation input; it never bypasses real
market clock/freshness gates. A practice worker failure is reported separately
and cannot kill the live-data paper account.

**Live PAPER** remains the separate experiment against real Kalshi quotes and
requires its reviewed contract/schedule and genuine data/clock gates.
**Manual orders** is where the user places individual fake-money orders.
Neither is needed to press Start on the historical Practice screen.

See [documented product problems](PRODUCT_ISSUES.md), [model comparison](ml-pipeline.md)
and [source attribution](data_sources.md) for the remaining limitations.
