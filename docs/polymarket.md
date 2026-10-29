# Polymarket public-data recording

`backend.polymarket.PolymarketRecorder(engine, ...)` records independent US and
International projections in `engine.store`. `await recorder.run()` starts
bounded discovery/polling and optional streams. `recorder.snapshot()` returns
JSON-safe `venues.polymarket_us` and `venues.polymarket_international`, each
with metadata, books, recent trade observations, status, errors and reconnects.
It does not populate Kalshi tickers or the execution broker. Every projection
has `execution_eligible: false`; no market equivalence is inferred from names.

Both venues are disabled by default. All writes use the existing append-only
Store synchronously on the same event loop. Deploy in the owning engine process
or use the CLI with its database writer lease; never run two database writers.
Raw response bodies/frames precede parsing; normalized channels are prefixed
`polymarket_us.*` or `polymarket_international.*`. Source timestamps and separate
UTC/monotonic receive clocks remain distinct. Headers, credentials and exception
messages are never journaled. Raw responses are venue market data, not private
account traffic. Store hash verification covers the original bytes.

## US configuration and use

PowerShell, using the installed project interpreter:

```powershell
$env:POLYMARKET_US_ENABLED = 'true'
../ufc-research/.venv/Scripts/python scripts/capture_polymarket.py --once --max-markets 2
```

Without selected slugs, discovery uses one bounded `/v1/search?query=UFC` result
set, retaining at most `max_markets` markets. Results are not an exhaustive UFC
catalog. Only active, unclosed results with `ufc` in the slug/title are selected.
`POLYMARKET_US_SLUGS` accepts comma-separated exact market slugs and uses the
official `/v1/markets` filter instead. Rules, market sides, fees, tick sizes,
status and identifiers remain in additive metadata. Discovery includes UFC
futures and next-opponent propositions; these are not necessarily bout winner
contracts. Review the venue's exact rules and outcome/side before any mapping.

REST uses **https://gateway.polymarket.us**, the official public market-data
host, without credentials. The separate authenticated host `api.polymarket.us`
rejects unauthenticated discovery with 401. It is not the public REST fallback.

For the market stream, optionally set `POLYMARKET_US_KEY_ID` and
`POLYMARKET_US_SECRET_FILE`. The latter is a local UTF-8 file containing only
the base64 Ed25519 seed (32 bytes, or official SDK's 64-byte secret form).
Keep the file outside the repository and configure read-only market-data access
with the provider. Keys are loaded only when US capture is enabled; only the
market WebSocket handshake receives signed `X-PM-*` headers. No inline secret
environment variable is supported. No credentials were supplied or tested.

The only US socket used is `wss://api.polymarket.us/v1/ws/markets`, with
`SUBSCRIPTION_TYPE_MARKET_DATA` and `SUBSCRIPTION_TYPE_TRADE`, camelCase fields
and a maximum of 100 selected markets. The user-supplied private endpoint is
intentionally unused. Market data is a complete replacement of the supplied
top-level snapshot, not an additive delta. Both arrays must be present;
missing arrays cannot silently erase depth. Lite quotes and heartbeats cannot
refresh L2 freshness. Trade quantity preserves the venue's unusual Amount
object; it is not silently relabeled as contracts or USD notional.

## International configuration

Enable `POLYMARKET_INTERNATIONAL_ENABLED=true` **only after the operator reviews
regional eligibility and data-use terms**. This flag acknowledges that review;
the software cannot establish permission from an IP response. No International
network requests occur by default. Configure exact comma-separated
`POLYMARKET_INTERNATIONAL_SLUGS` (Gamma metadata plus returned token IDs) and/or
`POLYMARKET_INTERNATIONAL_ASSETS` (explicit outcome token IDs). Slug selection
is bounded; there is no global dataset download. With only asset IDs configured,
market settlement rules/metadata have not been discovered.

International uses only unauthenticated Gamma `/markets`, CLOB GET `/book`, and
the public market socket
`wss://ws-subscriptions-clob.polymarket.com/ws/market`, with application `PING`
every 10 seconds. It never opens a wallet, signs an order, queries a user's
account, or bypasses a denied response using a proxy or alternate host. US and
International prices, outcomes, fees, settlement mechanisms, permissions and
rules remain separate. The official geoblock documentation addresses **order
placement**; a public-data HTTP 200 does not establish regional or reuse rights.

## Continuity and limits

Neither verified market wire contract supplies a documented monotonic sequence.
The recorder leaves Store `seq` empty and reports `continuity_verified: false`.
An opaque International `hash` is stored, not treated as a sequence or verified
checksum. US full book snapshots replace preceding books. International full
`book` messages and REST books replace preceding books. Typed International
`price_change` events are journaled, then invalidate depth pending a complete
snapshot; they do not claim reconstructed delta continuity. This deliberately
trades usable depth coverage for honest continuity.

Invalid/malformed/crossed packets, source timestamp regressions, disconnects,
poll failures and lifecycle changes invalidate books. Snapshot reads mark books
stale after 15 seconds by default using monotonic receive age; book age is
independent of heartbeats and trades. Polling defaults to 30 seconds, so REST-only
capture intentionally has stale intervals. Source timestamps remain visible;
receive freshness is not proof that the provider's underlying market changed.
Reconnect uses bounded exponential backoff and fresh subscriptions; full
snapshots are required for recovery. No unobserved sequence gap can be detected
when a venue supplies no sequence. Trades are append-only observations, not
deduplicated fills or settlement outcomes. Only the latest 100 are exposed in
memory; the journal retains all captured frames.

## Verified sources and smoke evidence (2026-10-29)

Primary sources:

- [US API introduction: distinct public/authenticated hosts](https://docs.polymarket.us/api-reference/introduction).
- [US market socket wire examples and auth](https://docs.polymarket.us/api-reference/websocket/markets).
- [US search API, nested event markets](https://docs.polymarket.us/api-reference/search/search).
- [Official Python SDK at pinned commit 6fde8f2](https://github.com/Polymarket/polymarket-us-python/tree/6fde8f2b6ea88198a199b38f7b6d8951d015a06a): `auth.py`, `resources/markets.py`, `websocket/base.py`, `websocket/types.py`. Its camelCase contract agrees with the specific market page; the generic WS overview still contains contradictory snake_case examples and is not used as the wire contract.
- [International raw market socket and heartbeat](https://docs.polymarket.com/market-data/realtime-data).
- [International GET book schema](https://docs.polymarket.com/api-reference/market-data/get-order-book).
- [International market discovery](https://docs.polymarket.com/api-reference/markets/list-markets).
- [Geographic restrictions and their order-placement scope](https://docs.polymarket.com/api-reference/geoblock).

Actual bounded HTTP checks: unauthenticated api-host `/v1/markets` returned 401;
public gateway `/v1/markets?limit=2&active=true&closed=false` returned 200;
gateway `/v1/search?query=UFC&limit=2` returned 200 with two events. One returned
market slug, `nocufc-ufc-alevol-nextopp-2027-12-31-aljste`, returned a real 200
book with bids, offers, `MARKET_STATE_OPEN` and `transactTime`. These demonstrate
public discovery/book access, not a moneyline mapping, an observed fight outcome,
real-time lead or profitable strategy. No authenticated WS, full UFC card,
credential access, International permission or International live stream was
verified. Offline tests use clearly synthetic official-shaped fixtures for
full replacements, raw-byte ordering, corrupt packets, disconnect invalidation,
staleness, signing, venue separation and subscription shape.

A bounded recorder smoke subsequently captured two real US books to ignored
`data/polymarket-public-smoke-20261029.sqlite3`: six journal events, hash chain
verified, status `rest_only_no_ws_credentials`, International disabled. Focused
offline verification: 15 tests passed. Run with the installed interpreter and a
repo-local temp directory:

```powershell
../ufc-research/.venv/Scripts/python -m pytest tests/test_polymarket.py -q --basetemp work/pytest-polymarket
```

Integrated defaults cap discovery at five markets and poll every five seconds, below the 15-second stale threshold. Select explicit slugs for useful bout coverage; broad discovery can return futures and is deliberately bounded.

Live integrated verification encountered HTTP 429 at the public gateway. Requests are now paced (at least one second apart); the poller honors numeric/date Retry-After and exponential backoff, invalidates depth, and reports rate_limited. HTTP 401/403/451 stops that polling loop for operator review. Stored quotes may therefore be stale; no rate-limit bypass is attempted. Authenticated streaming remains unverified.
