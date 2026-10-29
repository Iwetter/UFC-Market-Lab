# Progress — October 29, 2026

Repo: Iwetter/UFC-Market-Lab; checkout is this directory. Base main: 3166554. Working branch: research/data-latency-2026-10-29. Draft PR: https://github.com/Iwetter/UFC-Market-Lab/pull/1 (not merged). Earlier implementation: d1e18ff. Priority upgrade continues on the same branch/PR.

## Completed

- Pinned/hashed CC BY 4.0 historical acquisition: 6,743 bouts, 29,937 judge rows. Corrected nonbinary appearance history, canonical fighter ordering, strict dedup and embargo.
- 3,828 pre-fight samples; 9,866 majority-judge round samples. Frozen research-only artifacts: artifacts/research. Train through 2022, calibrate 2023, test 2024–Oct10 2026. Pre-fight test 992 bouts: LL 0.6560 vs Elo 0.6828, accuracy 62.30%. Full provenance/metrics: docs/data-training-report.json. No trading-edge result.
- Physical/broadcast timing study, quote/depth/lifecycle/confound guards, interval uncertainty, card bootstrap, negative leads and session/window bounds. Actual REST smoke: 26 markets / 104 book observations / zero paired fight events. Twelve seconds is unverified.
- Typed position evidence with one predeclared checkpoint/bout and uncertainty; no eligible public mount/back exposure cohort found. Finish-only tables rejected.
- Optional licensed Sportradar raw/TIP recorder with durable resume and secret guards; integrated same-journal collectors are opt-in. No credentials tested. Research API and dashboard are isolated from execution approvals.
- Always-on private Docker/PAPER deployment prepared; single-writer leases, health, restart/failure handling, atomic WAL backups. Docker/cloud execution unverified because Docker/server access is unavailable. No purchases.
- Daily continuation created: automation `ufc-market-lab-focused-development`, heartbeat attached to this existing chat, daily 05:15 America/New_York, starting Oct29. Do not create another. No main-session model switch tool available; user should select Sol Medium. Usage checked once this batch; ordinary usage was available, no credits/reset consumed.
- Added AGENTS/ROADMAP and cooperating development lease for non-overlapping bounded batches.

## Priority upgrade: connected experimental PAPER milestone

- Compared fixed Elo/logistic/boosted-tree candidates with validation-only selection, separate calibration and walk-forward diagnostics. Selected logistic C=0.1: reused 992-bout holdout LL 0.657287, Brier 0.232633, accuracy 61.79%; frozen prior baseline remains better (LL 0.655993). No new untouched holdout, demographic guesses or timestamped market benchmark. JSON inference and hashed caching; no unchanged baseline retraining/downloads.
- Upcoming fixture service reconstructs canonical lagged history from cached source. Automation UI binds exact identities, parent event title, card ID, rule hash and verified earliest start. Historical estimates remain experimental and live-ineligible.
- Deterministic opt-in pre-fight PAPER BUY/SELL/HOLD/NO-TRADE, conservative cost/uncertainty policy, YES/NO reduce-only exits, durable cooldown/order caps, atomic broker submission and replay. Stops before earliest-start guard or fight-start evidence; no live-fight probability claim. Existing risk/fees/depth/accounting and disabled live gateway preserved.
- Independent read-only Polymarket US public gateway + authenticated market-stream adapter; International explicitly opt-in. Real bounded smoke: 2 US books/6 events/hash valid; integrated dashboard observed both venues' public quotes. Gateway returned 429 in sustained verification: paced requests, Retry-After/backoff, explicit stale/rate-limit states, no bypass. Private socket unused. Polymarket execution and authenticated feeds remain unfinished/unverified.
- Automatic public SNTP observations stay off the event loop and never invent freshness. Live check measured roughly -0.72s host offset, beyond the 50ms offset-plus-uncertainty gate: execution correctly remained blocked. Do not fake zero; host time synchronization is needed. No cloud/services/orders purchased or activated.
- Previous PR head remote CI passed (6a90613). The same daily continuation remains active; no schedule duplication. Usage read once this batch; no reset/credit consumed.

## Verification and immediate next task

Final integration: 176 Python tests + 2 frontend tests, TypeScript/Vite build and source private-key scan passed. Focused stage checks covered canonical features/JSON tree parity, market transport/429/denied access, automatic YES/NO entry/exit, restart cooldown, rule/identity/start-window gates, uncertainty, atomic rollback and deterministic order replay. Browser verified fitted model availability, exact parent-event candidates, real venue quotes, explicit stale/error state and actual comparison scores. No genuine-market automated fill or profitability is claimed; execution tests are labeled synthetic fixtures. Docker/cloud/authenticated streams remain unverified.

Next: improve Polymarket capture under provider throttling and validate exact bout-winner contract/fee semantics before any Polymarket paper execution; obtain explicit selected slugs rather than assuming futures are moneylines. Also verify a full real Kalshi card capture with genuine clock sync and reviewed fixtures; test prospective PAPER outcomes without selecting on known holdout. Keep recording/ML/strategy processes independent of Codex. See ROADMAP.md and docs/autopaper.md.
