# Knockdown-to-market timing study

The user's working hypothesis is about **12 seconds between seeing a knockdown and the market odds changing**. This is a hypothesis, not an observed result of this repository. The analyzer measures changes in executable best bid/ask quotes. A last-traded price, website animation or cached screen can stay unchanged while executable quotes have already moved.

## What each clock means

| Clock | Observation and limitations |
|---|---|
| Physical event interval | An independently established occurrence interval, with identified source and verified synchronization. A packet generation time is not a substitute. |
| Provider generated | Timestamp the vendor assigns when making its packet. It may include collection, processing and batching delay. |
| Provider received | This recorder's UTC receive time; the earliest the event could influence this bot. |
| Broadcast observed | When the operator saw the knockdown on a named stream/broadcast. This includes the entire production and delivery path; the physical event may have happened earlier. |
| Exchange update / sending | Optional original exchange fields, retained for diagnostics without replacing local receipt time. |
| Quote received | When this recorder observed a usable book. REST polling creates an observation interval, not a precise reaction timestamp. |

The [official Sportradar Fight Stats schema](https://docs.sportradar.com/ufc/stream-endpoints-websocket/fight-stats) describes its timestamp as packet generation time and its packets as replacement cumulative snapshots. The [Fight Details stream](https://docs.sportradar.com/ufc/stream-endpoints-websocket/fight-details) carries event packets and sequence numbers, with licensed authentication and resumable stream positions. Neither publication alone demonstrates a twelve-second delay in this user's setup. Kalshi's [order-book stream](https://docs.kalshi.com/websockets/orderbook-updates) supplies snapshots/deltas; its [`seq` consistency and source clocks](https://docs.kalshi.com/websockets/websocket-connection) must be preserved rather than replaced with website display timing.

## Working software

`backend/latency.py` defines:

- `StudyEvent`: bout/card/ticker mapping; occurrence interval, provider generation, receipt, broadcast observation; clock uncertainty; source and mock status; optional expected direction with its documented basis.
- `QuoteObservation`: executable YES bid/ask, touch depths, receive time, optional original exchange/sending times, stale state, market lifecycle status and source protocol. It intentionally has no last-traded-price field. Unknown or inactive status is ineligible.
- `StudyConfig`: preregistered event windows, quote threshold, persistence, maximum spread/gap, depth and independent-card requirements.
- `analyze_latency(events, quotes, config=None)`: per-episode exclusions/reaction intervals, diagnostics, aggregate intervals, and hypothesis status.
- `read_journal(database_or_connection, session=None, max_records=None, start_ms=None, end_ms=None)`: WAL-safe extraction, with explicit operator `latency_annotation` records preferred over generic fight packets. Optional inclusive UTC millisecond receive-time bounds select an episode window; reversed/invalid bounds fail. A consistent read transaction selects only relevant normalized channels; raw frames, raw REST response bodies and base64 payload copies are not materialized. An optional row cap rejects oversized input and required metadata instead of silently truncating the baseline.
- `latency_stress_scenarios()`: labeled assumptions at 0, 5, 12 and 20 seconds. These values are sensitivity settings, not measurements.

The default search window is -15 to +30 seconds around the physical event interval, broadcast observation, packet generation or receipt, in that priority. This permits negative estimates. Baseline quotes come from the preceding 10 seconds, before the entire search window; the detector does not use a post-event price as its baseline. A move must exceed three probability points and persist for at least two observations and 500 ms. A confirmed directional event can use up/down; otherwise only an absolute displacement association is reported.

The first crossing is interval-censored between the last usable quote below threshold and the first sustained displaced quote. The interval is widened for supplied clock uncertainty and physical-event bounds. It is rejected if a stale/illiquid/wide/inactive book or a sampling gap obscures the first displacement. Opposing oscillations, unstable baselines, unconfirmed mappings and overlapping event windows are excluded. No displacement in the finite observed window is labeled `window_censored`, never inserted as zero seconds. A market could have completed its reaction before the negative search boundary, particularly when the broadcast is delayed; this status does not mean latency exceeds the positive search horizon.

For each detected episode:

```
physical delay = reaction interval - physical occurrence interval
available lead = reaction interval - event received locally
broadcast relative delay = reaction interval - observed broadcast event
generation relative delay = reaction interval - provider packet generation
```

A **negative available lead** means the observed market move preceded this bot's feed availability. A positive value only describes the interval to the observed displacement; it does not prove that stale offers were tradable, that the move was caused by the tagged event, or that a strategy can fill profitably after costs.

The aggregate reports median censoring bounds and an event-card cluster bootstrap interval. Repeated events in a single bout or card do not become independent samples. Two explicitly separate twelve-second hypotheses use a predeclared ±2-second equivalence band and need at least 20 independent cards:

- `hypothesis` tests independently verified **physical-event** timing. Without those clocks it remains `insufficient_physical_timing`.
- `broadcast_hypothesis` tests **broadcast-observation-relative** quote associations, the user's clarified question. It can be evaluated from genuine broadcast observations without claiming an octagon occurrence time. Missing paired broadcast evidence produces `insufficient_broadcast_observations`.

For either estimand, an interval entirely within 10–14 seconds is labeled compatible with that band; an interval wholly outside it is inconsistent; intersecting evidence is inconclusive. Each block reports its own real-episode count, independent-card count and confidence interval. Results are never substituted between the estimands. Mocks are excluded by default and can never support either real-world hypothesis, even in explicit mock demonstration mode.

This describes **detected episodes passing checks for known confounders and coverage only**. Unobserved competing causes may remain. Excluded and censored counts are retained; selective detection and correlated fight activity limit generalization. A price-change association is not causal attribution. Do not select the threshold/window after inspecting the same evaluation sample; freeze a configuration before a forward recording window.

## Capture protocol for the actual question

1. Keep PAPER execution. Record Kalshi authenticated WS books if a read-only key is available; otherwise record REST and retain the wider polling intervals.
2. Use a confirmed bout/ticker/YES mapping. Record the source and local UTC time when the knockdown was observed on the broadcast. Identify the broadcast service and device/path. Do not call that time the octagon occurrence time.
3. Record optional independently synchronized physical timing separately. A licensed provider must explicitly document occurrence timestamps if they are to serve this role; generated packets alone are insufficient.
4. Store a full `StudyEvent` payload in the append-only journal under `source=operator`, `channel=latency_annotation`. `broadcast_observed_ms` suffices for an observation-relative study; leave physical bounds absent unless established independently.
5. Keep market updates around the event and any competing knockdown, submission attempt, pause, round ending or feed gap. Overlapping episodes should remain excluded for simple attribution.
6. Compare executable quotes and available depth, not just last trade. Review the physical, broadcast, provider and local receive estimands separately.

No live feed license or paired real knockdown/book recording was present when this feature was implemented. Existing generic raw-market captures cannot establish the proposed delay. A hand-entered annotation is an operator assertion that must retain its provenance and uncertainty; the code cannot validate a claimed physical timestamp merely because it is entered in a form. Physical, broadcast and provider-generation timestamps ahead of local receipt beyond the explicitly stated clock uncertainty are rejected.

## Run the study

From the repository after installing dependencies:

```powershell
.venv/Scripts/python scripts/analyze_latency.py --journal data/kalshi.sqlite3 --output data/research/latency-report.json
```

Or supply an auditable JSON object containing `events` and `quotes` arrays matching the typed schemas:

```powershell
.venv/Scripts/python scripts/analyze_latency.py --input data/paired-capture.json --output data/research/latency-report.json --config data/preregistered-latency-config.json
```

`--session` limits a journal to one recording session. A journal containing multiple sessions is rejected unless one is explicitly selected. Cross-session UTC merging needs a separately reviewed synchronization procedure; process monotonic clocks cannot be compared across sessions. Earlier mappings/market metadata may initialize a selected session's state, but earlier event/quote observations are never merged into it. Existing canonical `FightEvent.source_ts_ms` is read as provider generation, never silently upgraded to physical occurrence. Explicit timing annotations replace dense stats/heartbeat packet associations for the same bout/ticker, while discrete starts/finishes/pauses/resumptions/round boundaries remain possible confounders. Canonical market records, raw lifecycle transitions and normalized book lifecycle metadata prevent finalization or inactive-market quote changes from becoming timing evidence.

The dashboard invokes bounded extraction/analysis on a worker thread with a separate read-only connection, keeping research scans off the recorder event loop. Offline CLI analysis is unbounded by default; `--max-records 50000` applies a deliberate cap when needed. Exceeding the limit requires selecting a narrower recording session or an offline analysis with a higher explicit budget. The reader carries only the latest earlier mapping/market metadata per ticker and counts that metadata against a configured cap. It does not alter the SQLite schema or silently merge data from separate sessions.

For an always-on recorder, `--start-ms` and `--end-ms` limit episode observations using the journal's local `recv_utc_ns`, not provider or physical timestamps. Include enough preceding data for the preregistered baseline and negative-lag search window; the detector excludes an event whose baseline was cut off. Metadata before the first selected observation is carried forward even without `--session`, while older quotes and fight events stay excluded. Session validation applies to the matching time window: if it still contains observations from multiple recording sessions, select one explicitly. A cap applies to the filtered input plus required latest metadata, never a silent arbitrary row tail.

The unit tests use artificial clocks to check interval bounds, negative leads, sparse/stale books, censoring, dependence, mock exclusion and provenance. Their scripted twelve-second fixture is **only a test case**, not a sample of real market latency.
