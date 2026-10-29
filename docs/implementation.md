> **Update October 29, 2026:** This initial architecture mapping is retained for context. Subsequent work adds pinned historical acquisition, fitted research baselines, judging samples, interval-censored physical/broadcast timing studies, licensed DDE raw/TIP capture, independent-bout position evidence, a Research dashboard tab, and a single-writer always-on deployment. Current scope and actual results are documented in the new linked research/deployment documents and `data-training-report.json`. No card-level empirical trading/latency gate is implied by the software extension.

# Implementation decisions and architecture mapping

Prepared October 21, 2026. Original materials are retained unmodified as design references. No numerical result from an attached reference-repository review is asserted as a result of this platform.

| Architecture area | Working implementation | Empirical or advanced work still required |
|---|---|---|
| Capture first | Raw REST/WS/provider bytes, receive UTC + monotonic clocks, session IDs, append-only hash-chained SQLite | Two-card uptime/latency acceptance, external replication/anchoring |
| Book consistency | Subscription-wide gaps, overflow-25, stale blocking, authenticated WS, get_snapshot, reconnect, REST fallback, top-five comparisons | Credentialed stream soak and measured p99 resync |
| Point-in-time data | Availability guards, registered features, model cutoff checks, immutable snapshots | Licensed UFC history, profile vintage tracking, own historical scraper/odds-source agreements |
| Entity mapping | Mandatory manual bout/card/YES-orientation review, invalidation on metadata changes | Provider-specific alias database and high-confidence date-constrained fuzzy proposals |
| Pre-fight | Symmetric regularized market-offset logistic, train-only scaling, chronological temperature calibration, expanding-year walk-forward, event bootstrap, frozen later holdout | Trained UFC artifact, benchmark superiority, complete temporal data quality audit, full nested searches if tuning added |
| In-fight | Separate state reducer, cumulative events, dedup/gaps, round/clock/pauses, exact initial prior, optional calibrated checkpoint artifact | Judge-scorecard training, competing finish hazards, licensed history, calibration by checkpoint/event type, martingale and information-lead studies |
| Execution | Shared pure risk and fees, delayed IOC, executable book walk, partial fills/cancelled remainder, slippage, participation, positions, ledger and settlement | Maker queue/trade-through simulation, stochastic latency, adverse-selection horizons and sensitivity report |
| Replay | Same core and recorded processing ticks, monotonic receive order, latency injection, code/config/input/output hashes, isolated databases | Cross-host synchronized merge, run-start snapshots for arbitrary subwindows, full historical depth history |
| Risk | Freshness, lifecycle, grids, fees, mapping, model approval, caps, reserves, close-only exits, blackout, data/model/drawdown circuits | Deployment-specific policies and real measured operating limits |
| Live gateway | Separate disabled CLI, separate keys, explicit production file/env/flag, signed arm and exact one-order approval, groups, safe IOC states, persistent unknown state, kill, positions/fills reconciliation | Credentialed demo certification, monitored production deployment, comprehensive account/subaccount and external-flow reconciliation |
| Dashboard | Actual React client backed by FastAPI and WS; state, probabilities, quotes, edge, positions, simulated orders/fills, P&L, latency, freshness, audit | No research-performance claims are displayed |
| Warehouse | Operational SQLite WAL plus isolated DuckDB JSON export | Attached normalized warehouse.sql remains a proposed broader research warehouse; the runtime does not falsely claim to populate every proposed table |

## Deliberate choices

SQLite rather than DuckDB handles operational transactions and the single event-loop writer. This avoids two concurrent processes writing a DuckDB ledger. The attached DDL is preserved for future analytical normalization. Amounts remain Decimal strings; a SQLite REAL column is never used for money.

Local startup and private market-data treatment take precedence over publishing a static dashboard. Sites hosting runs Workers and cannot host the required local Python/database engine as-is; no misleading public mock site was deployed. The Sites skill's local preview workflow was used for the dashboard. No GitHub destination was specified, so no remote repository or PR was created; CI source is supplied for a repository of the user's choice.

Only manually initiated paper intents are enabled. Positive displayed EV is not automatic authorization. No unvalidated strategy generates real-market orders. The default uncalibrated updater is confined to research display/mock execution; real in-fight fills require a checkpoint artifact. A fitted artifact alone is not a statistical acceptance pass.

## Gates that cannot honestly be completed in a software build

The original M0–M7 plan spans data licenses, multiple recorded cards and at least a year of forward paper evaluation. D-1/D-3 sustained availability, clock discipline, PF benchmark tests, IF checkpoint calibration and information lead, E net-of-cost significance, multiple-testing correction and forward-edge gates are **not measured/passed** here. The repo provides working collection/training/replay/execution infrastructure to perform that work. It intentionally does not invent observations to satisfy the gates.

The checkpoint updater is a supported alternative to the design's full judge/hazard model, not a claim that a four-cause hazard model has been trained. Unsupported maker orders are rejected rather than optimistically filled. Static historical UFC acquisition, sportsbook ingestion, betting-label settlement transformations, deflated-Sharpe/multiple-testing reports and a full feature-engineering warehouse remain separate research extensions.
