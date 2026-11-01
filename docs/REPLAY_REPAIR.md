# Replay diagnosis — October 30, 2026

The reported Tommy McMillen / Marwan Rahiki replay was reproduced exactly on the still-running port 8004 server: probability 0.8526375193560606, one YES purchase at $0.69 plus $0.02 fees, then a $1 winner payout and $0.29 net profit. That profit was settlement profit, not profit from a sale.

## Root cause

The Python process on 8004 started before the shared profit policy was installed. Its state had neither `policy` nor `strategy`. Updating Git and refreshing the browser did not reload its Python modules. The separate 8005 preview had the newer policy, which compounded the confusion.

The legacy `PracticeController._decide` in revision 721a105 required this for a sale:

`bid - slippage - model probability - exit fee - uncertainty buffer >= exit edge`

It never compared proceeds with actual entry cost. At the scenario's maximum YES bid of $0.81, the calculation is approximately `.81 - .01 - .8526375 - .02 - .05 = -.1226375`, below the `.02` exit threshold. Thus even a profitable sale was rejected. This is a policy mismatch, not a reason to lower uncertainty or force trades. The shared policy already on main fixes that mismatch by evaluating fee-inclusive profit targets before entry cooldown/caps and then evaluating pullback re-entry using actual fills.

## Exact replay comparison

The current shared engine reran the same cached fight without retraining. Source commit, model hash, probability, quote algorithm and all 61 timestamped decision bid/ask pairs match. Original economic settings and the original six-order ceiling were preserved; new profit-policy fields use documented defaults. The full old and new local journals remain under `work/practice` and `work/diagnosis-mcmillen-current`.

| Virtual seconds | Actual paper fill | Price | Fee | Realized trading gain | Remaining |
|---:|---|---:|---:|---:|---:|
| 0.25 | BUY 1 YES | $0.69 | $0.02 | $0.00 | 1 |
| 30.25 | SELL 1 YES | $0.80 | $0.02 | $0.07 | 0 |
| 90.25 | BUY 1 YES | $0.53 | $0.02 | $0.00 | 1 |
| 240.25 | SELL 1 YES | $0.63 | $0.02 | $0.06 | 0 |
| 330.25 | BUY 1 YES | $0.53 | $0.02 | $0.00 | 1 |
| 480.25 | SELL 1 YES | $0.63 | $0.02 | $0.06 | 0 |

The repaired replay produced three cycles, $0.19 trading profit and zero settlement profit. Thirty later decisions reached the preserved six-order cap. The old strategy's $0.29 settlement result was higher in this scenario. More transactions did not improve this replay's profit. All quotes, fills and gains here are synthetic PAPER evidence, never historical exchange returns.

[Comparison report](replay-repair-evidence.json) and [all chronological decisions and fills](mcmillen-decision-audit.json) contain the detailed calculations, holdings, entry cost, liquidation value, entry/exit eligibility and exact reasons. Economic entry eligibility remains subject to the top-level decision blockers and broker risk checks.

To rerun a saved summary into a fresh isolated directory:

```powershell
python scripts/diagnose_practice_run.py --original path/to/summary.json --output work/new-diagnosis
# Add --manifest path/to/manifest.json when the summary was exported elsewhere.
```

## Repair and verification

- Both modes retain shared `backend/strategy.py`; no threshold tuning, model retraining or dataset downloads.
- BUY/SELL decisions now retain their numerical diagnostics. Skipped decisions have counts and specific explanations.
- Fill-derived trade ledger records execution time, price, fees, allocated realized gain and remaining inventory. Trading profit, settlement profit, estimated liquidation value and total net liquidation P&L are separate. Settlement no longer leaves a misleading open cycle or invents a SELL marker.
- React markers still use actual fills; tests also found and fixed invalid React SVG title children. Partial-sale cycles remain visible before the position closes.
- `/api/health` exposes a source fingerprint captured when the process starts and reports when source changes require a restart. The dashboard warns on changed code or an older engine without policy support.
- 232 Python tests, 9 frontend tests and the TypeScript/Vite production build passed. New deterministic p=.85 regression asserts four real broker fills, inventory 1/0/1/0, four $0.02 fees, $0.38 trading profit, cash, 250ms arrival timestamps, hidden future outcomes and no duplicate/post-finish fills. Existing shared-mode, partial-fill, risk and negative-HOLD tests passed. Separate settlement test reconstructs $0.53 settlement profit and zero trading profit after reopening storage.
- Restarted the existing 8004 preview with current code. Its HTTP replay reproduced the six fills above, matched ledger IDs/inventory/P&L and rejected additional steps after completion. Health reports PAPER, real trading disabled, and no restart needed. Browser automation is disabled in Work settings: no visual browser QA is claimed. Rendered component tests and production HTTP checks were used instead.

Port 8004 now shows the completed McMillen replay. Refresh it and press Start to watch another run. The six-order cap remains from the saved original policy used for this diagnosis. Its purpose is a risk ceiling, never a trade target. The separate 8005 process was not restarted.

The model remains fixed pre-fight only. Real in-fight estimates, profitability, licensed live data and the proposed 12-second reaction delay are still unvalidated. Real-money execution remains disabled. This review branch also retains the previously verified collector-supervision correction; no PR is merged automatically.
