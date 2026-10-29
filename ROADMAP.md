# Prioritized roadmap

1. **Capture correctness / measured timing:** validate real authenticated Kalshi WS over a full UFC card; record licensed events in the same clock/session. Requires a user-created read-only key and provider entitlement. Inspect disconnects, gaps, stale intervals, source/receive delay and book persistence. Never assume a 12-second lead.
2. **Operational verification:** build/run the prepared container on a real Docker host, exercise crash/restart/restore and storage growth, wire measured NTP/chrony offsets. No cloud provisioning or paid activation without separate approval. Keep dashboard private.
3. **Offline correctness when credentials unavailable:** expand journal/replay fault cases and latency-window diagnostics using existing cached public captures. Fix demonstrated bugs before adding speculative strategies; do not re-download/retrain unchanged data.
4. **Model validation:** acquire forward point-in-time features and timestamped sportsbook/Kalshi benchmarks; compare proper scoring/calibration on later untouched events. Historical artifacts stay research-only. Train a licensed in-fight checkpoint/hazard model only when suitable observations exist.
5. **Paper execution:** stress fills at 0/5/12/20-second additional feed delay, order latency, costs, adverse selection and settlement. Approve only a versioned forward-validated paper model; no real-money automation.
6. **Position evidence:** collect licensed full exposure or predeclared checkpoints with outcomes. Mount/back/guard/side win rates require independent bouts and uncertainty; finish-only data is insufficient.
7. **Frontend:** improve only observed reliability/usability issues, keeping actual provenance, missing data and study limits visible.

Each scheduled batch: at most three meaningful tasks, normally under 20 minutes, economical model choices and at most two scoped agents. Release the development lease and update PROGRESS before ending. If usage/access blocks one item, save it and choose an independent useful item; no repeated polling, duplicate automations or purchases.
