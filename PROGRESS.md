# Progress — October 29, 2026

Repo Iwetter/UFC-Market-Lab; current checkout is this directory. Branch research/data-latency-2026-10-29, draft PR https://github.com/Iwetter/UFC-Market-Lab/pull/1 (not merged). Existing same-chat daily 05:15 America/New_York continuation remains active; no duplicate schedule. Read AGENTS.md and ROADMAP.md; acquire the development lease before edits.

## User-visible priority: watch the bot practice

The user could see fictional/live fights but could not find a working automatic old-fight practice flow. Documented the product failures and acceptance criteria in docs/PRODUCT_ISSUES.md and pushed that diagnosis immediately (02494d7).

Implemented historical Practice as the default home screen. Search all eligible cached bouts (2024 onward; first 100 matches returned), choose one, press Start, and watch automatic BUY/SELL/HOLD/NO-TRADE, orders, fills, holdings and fake-money P&L. Pause/Resume/Step/speed controls work. Manual orders and Live PAPER are explicitly separate; advanced tools moved into a menu. HOLD rows are optional so actual trades and final results remain visible.

Real bout identity, date, result and completed-round aggregates come from the pinned source. The fitted pre-fight prediction uses only earlier reconstructed history and stays fixed. Synthetic price/depth paths depend only on bout ID/time, never winner/future statistics. Outcome is hidden until replay finishes. There is no video or trained in-fight model. Each practice run has a UUID directory, isolated broker/account, append-only SQLite journal, manifest and summary. Close/replacement safely cancels/liquidates practice risk. Practice failures cannot kill the main paper account.

## Preserved infrastructure and evidence

- CC BY 4.0 pinned source: 6,743 bouts, 29,937 judge records, through Oct10 2026. 3,828 pre-fight samples / 9,866 conditional round-majority samples. Raw data ignored under data/research/nlbair; hashes/attribution in docs/data_sources.md.
- Frozen research baseline: 992 reused test bouts, LL 0.655993. New validation-selected/calibrated logistic: LL 0.657287, Brier 0.232633, accuracy 61.79%; slightly worse than frozen baseline. Elo/GBT/logistic comparison and walk-forward: docs/model-comparison.json. No new untouched holdout, demographic guesses or timestamped market benchmark. No unchanged retraining/downloads.
- Real Kalshi REST discovery/books, optional authenticated WS, durable recorder/backups/leases. Real bounded REST capture: 26 markets/104 books/zero paired fights. Separate licensed Sportradar adapter unverified with credentials. Twelve seconds remains unverified; no eligible exact-position cohort.
- Real Polymarket US public books captured; sustained polling encountered429. Pacing/Retry-After/backoff/access-denial handling is implemented. International explicitly opt-in; authenticated streams and Polymarket execution remain unfinished/unverified.
- Advanced Live PAPER handles reviewed pre-fight Kalshi fixtures with cost/risk/cooldown gates. Real host SNTP measured about -0.72s; offset-plus-uncertainty gate blocks until genuine synchronization. Never fake a zero. Practice uses a labeled virtual clock in its separate account.
- Docker/private VPS deployment prepared but not executed on this host; no host/license/subscription bought. No real orders sent; live CLI remains isolated and per-order approved.

## Verification / next task

Full integration: 197 Python + 4 frontend tests, TypeScript/Vite production build passed. After final search/isolation/error-display changes, 22 focused practice/API tests and rebuilt dashboard passed. Browser completed a real cached Gerald Meerschaert/Julius Walker replay: automatic NO buys and reduce-only sales visibly filled, pause worked, completed-round stats appeared and actual result was revealed at recorded finish. This is synthetic-market execution verification, not returns evidence. Earlier remote cc82b22 CI passed all four OS/Python variants; check the current PR's CI after this milestone push.

The practice launcher is `python start.py --prepare-data`; use existing cached files, never retrain at startup. docs/PRACTICE.md explains real versus synthetic data and exact controls. Current user-facing local preview runs on 127.0.0.1:8004 for this handoff; it is not cloud deployment or an always-on recorder.

Next: prioritize user feedback on this Practice flow and any demonstrated reliability problems. Acquire synchronized genuine market books for future true market replay; do not describe synthetic practice P&L as historical profitability. Keep the bot observable and avoid adding more setup screens. See ROADMAP.md for remaining feed/model/operations work. Usage checked once this batch; no credits/reset used. Release the development lease before ending.
