# Verification record

Software and public data integration checked October 21, 2026 on Windows, Python 3.13.3 and Node 24.15.0. These are implementation checks, not research results.

| Increment | Executed checks | Observed result |
|---|---|---|
| Capture/storage/books | Immutability, persistence, complemented/unified quotes, subscription-wide sequence gap, config/grid safety | 4 Python tests passed |
| Models, fees and paper accounting | Temporal training/calibration, exact symmetry, future feature canary, fee arithmetic, delayed/partial IOC execution, idempotency, settlement, risk | 16 cumulative Python tests passed |
| Market data / ingest | Public REST pagination, signing and V2 direction, deterministic receive-order replay, licensed HMAC/raw boundary | 20 cumulative Python tests passed |
| API / isolated gateway / replay | Same-origin and Host security, paper route, no live web route, restart/source isolation, deterministic replay hash, finalized-only settlement, approval gates | 27 cumulative Python tests passed |
| Fault injection | 429/503 retries, overflow-25, WS gap resnapshot/reconnect, circuit breaker idempotency, unknown submission persistence/no retry | 32 cumulative Python tests passed |
| Dashboard | Formatting/grid tests, TypeScript compile and Vite production build | 2 frontend tests passed; build passed |
| Final integrated check | Earlier checks plus walk-forward temporal folds, actual dashboard WS receipt, projected scope caps and immutable starting capital | 38 Python tests and 2 frontend tests passed; production build and source secret scan passed |
| Clean startup | Ran `python start.py --no-browser --port 8001` with a new virtual environment, dependency install and frontend build | HTTP 200 from `/api/health`, source mock, mode PAPER, live disabled |
| Browser | Live state stream, limit IOC cancellation on quote movement, filled paper buy, open position mark, reduce-only close | Visibly verified; example round trip realized a simulated loss, not a trading-performance result |
| Official public REST | Full recorder poll/discovery/book cycle | 28 markets / 28 books, current series metadata, exchange status, verified event hash chain; zero order endpoint calls |
| API contract | Fresh official OpenAPI/AsyncAPI downloads and V2 required-field/side/path assertions | Passed; hashes in api-contract-verification.json |

The first browser test exposed a missing WebSocket serialization import. It was corrected and the API test now receives an actual WS state message. The first local check-script run exposed a missing parent temporary directory. It was fixed and the complete check command passed. No failures are hidden by replacing real data with mock data.

The test suite may emit a Starlette warning that its httpx test client integration is deprecated in favor of httpx2. Runtime HTTP/WS behavior and test assertions pass; this is a dependency migration notice, not a suppressed failure.

At the October 21 baseline, not verified with real credentials/data: authenticated Kalshi WS, Sportradar or other vendor-specific integration, demo/prod order submission, measured real feed latency, sustained uptime, trained UFC calibration or profitable edge. The October 29 extension below adds explicitly limited historical model evaluation. Live-gateway tests use an in-memory HTTP transport pointed at the official demo URL contract, with no network dispatch.

At the October 21 baseline, the GitHub Actions workflow had not run remotely; Linux/Python 3.12 compatibility was configured in CI, not claimed from local Windows testing. That baseline was delivered as a local export. Check the pull request for current remote CI results.


## October 29 extension

- Acquired and trained on the real pinned CC BY 4.0 dataset; exact corrected counts, embargo, train/cal/test partitions, fitted model hashes and actual holdout metrics are in `data-training-report.json`. No unchanged model was retrained for subsequent validation passes.
- Final local integration: **110 Python tests**, **2 frontend tests**, TypeScript/Vite production build and source secret scan passed. Added coverage includes source integrity, nonbinary history, deduplication, test-label perturbation/frozen artifacts, lifecycle/timing/window/session guards, backups, single-writer launcher leases, licensed adapter fault handling, development-run leases, and no promotion from historical inference to execution.
- Bounded headless real REST capture: 26 markets, 104 book observations across four polls, 12 seconds, healthy collector, no event feed and no orders. `latency-capture-report.json` reports insufficient physical and broadcast timing evidence, not a measured 12-second delay.
- Browser verified the Research tab's actual historical scores, zero real physical timing/position samples, and mock-tag exclusion.
- Docker/Compose YAML is prepared and parsed; Docker binary, cloud access, Kalshi read key and licensed vendor entitlement were unavailable. Container execution, authenticated live feeds and deployment are not claimed.
- One daily 05:15 America/New_York continuation heartbeat is active on this chat. Native schedule ID is recorded in PROGRESS; its target is the existing conversation. The host must remain on with the app running for local file access. No credits, reset credits, subscriptions or hosting were purchased/activated.

## October 29 priority upgrade

- Full final integration: **176 Python tests and 2 frontend tests passed**, with production TypeScript/Vite build and source private-key scan. Model artifact/sklearn parity, lagged identities, timestamp bounds, replay, YES/NO reduce-only automatic exits, atomic saves, throttle handling and SNTP validation are covered. Source scan checks private-key files/PEM blocks, not every possible secret format.
- Actual bounded public Polymarket US recorder: 2 books, 6 events, verified hash chain, International disabled. Integrated browser showed real Kalshi and US quotes, actual model scores and parent-event identity candidates. Sustained public US requests encountered 429; quotes were explicitly stale. Pacing/Retry-After/backoff was added and fault-tested; sustained post-fix recovery is not claimed.
- Public SNTP did return a measured host offset of about -0.72 seconds with low sample delay. The offset-plus-uncertainty execution gate blocked correctly; no system time was modified and no zero offset fabricated.
- Frozen/reused holdout metrics and split limitations are documented in model-comparison.json and ml-pipeline.md. No genuine-market strategy fill, return, authenticated stream, International network integration, credentialed live-feed packet, Docker/cloud execution or in-fight predictive calibration is claimed.
- Existing remote PR CI passed on 6a90613; check current PR for latest commit's CI. Existing daily 05:15 continuation is unchanged.

Clean-runner CI on a6a5813 exposed one test relying on the unversioned local data cache (175 passed, one failed). The test now creates a labeled synthetic CSV/manifest/artifact fixture and verifies hashes, inference provenance and missing-source failure without network access. It no longer assumes developer-machine data. No runtime validation or source pins were weakened.

## Historical Practice user-flow milestone

The previous UI did not provide the user's requested old-fight automatic practice flow; passing engineering tests did not prove that user goal. docs/PRODUCT_ISSUES.md records the observed failures and corrections. Practice is now the home screen, with searchable historical bouts, Start/Pause/Resume/Step/speed, fixed trained predictions, automatic order activity, a separate account and result reveal.

Full local integration passed 197 Python and 4 frontend tests plus production build. After final search/isolated-worker/error-display changes, 22 targeted practice/API tests and the production build passed. Tests are fully offline with labeled source/model fixtures and validate source hashes, future-result exclusion, model input cutoffs, winner-independent synthetic quotes, YES/NO reduce-only exits, virtual arrival/depth/cost accounting, pause/step, deterministic repeat, archive/close, risk caps, authenticated controls and main-account isolation.

Browser verification on cached real source completed Gerald Meerschaert vs Julius Walker (2026-10-10). The trained pre-fight probability stayed fixed; automatic NO buys and reduce-only sales visibly executed under the synthetic book. Pause worked. Only completed-round statistics appeared, and the recorded result was revealed at the 515-second active-time finish. No synthetic P&L is presented as historical exchange returns or profitable edge. The flow requires no live credentials or real-clock bypass. No fight video or trained in-fight prediction is supplied.

## Profit taking / pullback policy correction

Full local checks passed 222 Python tests and7 frontend tests, production TypeScript/Vite build, private-key scan and diff checks. Rebuilt after adding live-workspace actual-fill markers. Shared strategy tests and mode integration tests exercise favored65%/ask45 entry, profitable sale while probability remains unchanged, wait for insufficient pullback, ask44 re-entry and another sale; actual broker fees/slippage/depth/250ms arrival are used. Partial profit ratcheting, fractional costs, risk exits, scaling caps, NO/grid symmetry, restore and no phantom fill anchors are covered.

The reproducible synthetic validation writes its journal/report under work via scripts/validate_profit_cycle.py; see PROFIT_STRATEGY.md for exact quote/fill sequence. Local running API completed a real cached bout with synthetic quotes/fills and fixed historical model. All outputs remain labeled; no historical return or genuine-market automated profit is claimed. Browser QA could not run because local browser use is disabled in Work settings. No exchange contract changed or live gateway was enabled. Authenticated/live-feed and calibrated in-fight model limitations remain.
