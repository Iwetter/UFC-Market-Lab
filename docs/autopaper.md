# Connect a historical prediction to automatic PAPER orders

The **Live PAPER** dashboard tab runs a deterministic experimental pre-fight
strategy, separately from approved execution models and the isolated live CLI.
No live gateway is imported. Historical artifacts never acquire live approval.
This is an execution experiment, with no observed market advantage or returns.

From a fresh checkout, one command installs dependencies, verifies/downloads the
pinned historical source if absent, builds the dashboard and starts real Kalshi
public data:

```powershell
python start.py --source kalshi --prepare-data
```

Existing cached source files are reused; startup never trains models. Portable
fitted JSON and comparative scores are versioned. To reproduce the bounded
comparison once, use `python scripts/train_models.py`; matching runs use its
content/config/code/version cache. Do not force unchanged training.

In Workspace select a genuine bout-winner market, then open Live PAPER. Choose
the exact historical fighter IDs, the YES fighter, the shared UFC card ID and
an independently verified **earliest plausible start**, with its source. Read
both complete settlement-rule fields and confirm the binding. The server checks
the current rules hash, exact fighter-name identities and market status. It
generates a fitted historical prediction automatically and audits its features,
cutoff, model version and assumptions. Name candidates are suggestions only.
Futures, next-opponent, method/round and other propositions must not be bound as
equivalent fight-winner contracts. Draw/NC treatment and any unusual payout rule
must be reviewed; the binary historical model does not estimate a draw/NC rate.

Enable experimental PAPER automation after review. Default policy buys one
contract only when edge exceeds five cents **after** executable price, rounded
fees, slippage and a five-percentage-point probability buffer. The buffer is a
policy assumption, not a statistical confidence interval. Existing holdings are
sold reduce-only if conservative sale value exceeds holding value by two cents
after costs and buffer. NO-outcome SELL remains a sale of NO, never a YES buy.
There is no shorting, opposite holding or repeated averaging into an existing
position. Portfolio caps, daily spend/loss/drawdown gates, one pending order per
market, 60-second cooldown and five orders per market/day apply.

Signals are evaluated at most once per second, with no LLM calls. Simulated IOC
orders arrive after configured latency and use the then-current book, partial
depth participation, grid, fees and risk checks. Order IDs, decisions and cooldowns
are durable. SQLite savepoints keep strategy state and broker submission atomic.
Replay consumes recorded decisions/intent inputs without generating extra orders.

The strategy stops five minutes before the reviewed earliest start (at least a
one-minute guard is mandatory). A real fight-start event stops it sooner. It
cannot know about an early fight without a legitimate live event feed, so a
conservative schedule matters. It never marks a missing fight feed fresh or uses
a pre-fight estimate as live fight intelligence. Open holdings can remain after
the cutoff; they settle only on finalized exchange values. In-fight exits need a
separately validated in-fight model. Predictions refresh from the same verified
snapshot at most twice daily, not new external observations. The source snapshot
ends October 10, 2026; later history is absent until an explicit data update.

Every 30 seconds a background SNTP observer measures public `time.cloudflare.com`
in a worker thread. It checks packet echo, server state, delay, uncertainty and
wall/monotonic consistency. A valid fresh offset **plus uncertainty** must fit
within 50 ms. Slow/failed observations do not refresh the clock. Public SNTP is
unauthenticated and does not authorize live money. Set `UFC_NTP_HOST` to an
operator-controlled time source, or `off` to disable measurement and fail closed
until a genuine fresh clock observation is supplied. UDP/123 must be reachable.
Do not fake a zero offset to unlock orders. REST/WS capture continues while
clock/risk gates block execution.

Set `POLYMARKET_US_ENABLED=true` before startup for real US public quotes in the
same journal/dashboard. Authenticated market streaming needs its separate key;
the `/private` socket is unused. International capture stays explicitly opt-in.
Polymarket quotes are read-only and execution-ineligible: contract matching,
fees, live adapters and realistic venue-specific paper fills remain unfinished.
See [Polymarket contracts and verification](polymarket.md).

For the prepared Docker service, acquire cached history into the named volume
once, then restart so its prediction cache reloads:

```powershell
docker compose exec recorder python -c "from backend.historical import acquire_source; acquire_source('/app/data/research/nlbair')"
docker compose restart recorder
```

Docker execution has not been verified on this host. No cloud service, paid feed
or authenticated trading account was activated. The daily Codex continuation
develops code; normal recorder/strategy services run independently of it.

For the simpler old-fight user flow, open the default **Practice** screen instead. It has no real-market setup gates and uses a separately labeled synthetic market/account; see [Practice](PRACTICE.md). The advanced Workspace tab is now labeled **Manual orders**.
