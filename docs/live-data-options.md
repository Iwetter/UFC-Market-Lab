# Live data choice — reviewed October 29, 2026

Use **Sportradar UFC DDE** as the primary licensed feed candidate, with Kalshi's authenticated WebSocket book as the market source. Start recording official public Kalshi REST immediately; add a read-only WS key for precise timing. This is a proposed deployment, not a purchase or an assertion of access.

| Source | Suitable use | Limitations / decision |
|---|---|---|
| Kalshi official public REST + authenticated WS | Executable UFC bids, asks, depth, trades and lifecycle | REST is available without a key; WS needs a read key. Record our own book history. Never infer executable prices solely from last trade. [Docs](https://docs.kalshi.com/websockets/orderbook-updates) |
| Sportradar UFC DDE | Primary live-event/position candidate | Provider advertises Fight Details under one second, and Fight Stats/Actions around five seconds; these are provider descriptions, not our measurements. Its stats schema includes cumulative mount, guard, half-guard, back and side-control durations. Commercial entitlements, historical depth and price need a quote. [UFC feed descriptions](https://docs.sportradar.com/ufc), [stats schema](https://docs.sportradar.com/ufc/stream-endpoints-websocket/fight-stats) |
| SportsDataIO MMA | Alternative schedule, historical or round-oriented data source | Coverage documentation lists ten-second fight polling and describes round-level stats. Verify actual UFC entitlement and timestamp semantics before considering it for a short reaction race. Marketing "real time" alone is insufficient. [Coverage](https://sportsdata.io/developers/coverages/mma), [API](https://sportsdata.io/developers/api-documentation/mma) |
| The Odds API | Secondary sportsbook consensus and historical pre-fight benchmark | Covers live/upcoming MMA; current odds have a free plan, history is paid. Documented featured-market in-play updates are 40 seconds, exchange markets 10 seconds, and historical snapshots generally 5 minutes since September 2022. That is unsuitable for proving a 12-second physical-event race. [MMA coverage](https://the-odds-api.com/sports/mma-ufc-odds.html), [intervals](https://the-odds-api.com/sports-odds-data/update-intervals.html), [history](https://the-odds-api.com/liveapi/guides/v4/) |
| nlbair/ufc-data | Already acquired historical bouts and judge-round training data | CC BY 4.0 with full source provenance. No event-level occurrence timestamps or exact live mount/back-position exposure cohort. [Original repository](https://github.com/nlbair/ufc-data) |

No vendor was contacted and no paid account was opened. Published DDE pricing was not established, so no price is invented. Before buying, request UFC live and historical coverage, retained sequence replay, timestamp definitions/uncertainty, time-in-position history, API retention rights, model-training/paper-trading permissions and an actual sampled card. Confirm whether a trial is real, delayed or scrambled. A benchmark odds service cannot replace a physical fight-event feed.

## Executable licensed adapter

The optional adapter records vendor server frames before normalization, resumes at the last contiguous sequence, blocks credential echoes, and models cumulative TIP durations. Authentication packet shape is taken from the operator's actual contracted configuration, not guessed from incomplete public docs.

Set `UFC_DDE_ENABLED=1`, `UFC_DATA_LICENSE_ID`, `UFC_DDE_AUTH_PACKET_FILE` (private JSON outside the repo), and `UFC_DDE_FIGHT_IDS` (comma-separated provider fight IDs) on the recorder. `UFC_DDE_STREAMS` defaults to `details,livestats`. In the integrated runtime both collectors share the Kalshi journal/session and local receive clocks. For separate raw feed inspection:

```sh
python -m backend.sportradar --fight-id PROVIDER_FIGHT_ID --stream livestats --database data/dde-only.sqlite3
```

Do not run a separate writer against the market recorder's database. Standalone captures need explicit clock alignment before cross-source studies.

Optional `UFC_DDE_EVENT_MAP_FILE` is a local JSON object mapping verified raw `eventElementType` names to normalized types such as `knockdown`, `takedown`, `round_start` and `finish`. No undocumented event name is assumed. Confirm a market's bout ID as `sportradar:PROVIDER_FIGHT_ID` in the mapping review. The integrated callback records one study event per bout event, avoiding mirror-contract duplication. Without a verified map, raw details are retained for later normalization.

Fight Stats timestamps are packet-generation times. The details timestamp's physical semantics are left unverified. These recordings can estimate lead after feed arrival; they cannot automatically prove physical fight-event latency. Cumulative mount time does not identify the current position. The optional vendor adapter captures observations; the canonical fight-state bridge and trained in-fight model still need validation against actual licensed packets before any model promotion.
