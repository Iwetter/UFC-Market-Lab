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

Not verified with real credentials/data: authenticated Kalshi WS, Sportradar or other vendor-specific integration, demo/prod order submission, measured real feed latency, sustained uptime, trained UFC calibration or profitable edge. Live-gateway tests use an in-memory HTTP transport pointed at the official demo URL contract, with no network dispatch.

The GitHub Actions workflow is provided but has not run remotely; Linux/Python 3.12 compatibility is configured in CI, not claimed from local Windows testing. No external GitHub repository was modified.
