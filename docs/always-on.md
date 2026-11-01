# Always-on PAPER recorder

The selected architecture is a single persistent writer on a small Ubuntu VPS, with a private dashboard, read-only market credentials and offline research jobs. A reasonable initial host is a DigitalOcean Basic Droplet with 2 vCPUs, 4 GiB RAM and 80 GiB disk, listed at **$24/month** on October 29, 2026, before backups, extra storage and taxes. This is a sizing starting point; monitor actual disk growth and CPU. No server was purchased. [Official pricing](https://www.digitalocean.com/pricing/droplets)

Keep training away from the timing-critical recorder when possible. API analysis runs in a separate worker process, uses a bounded, consistent read-only journal snapshot and never performs model approval. The recorder's event loop remains dedicated to capture and paper state. The full raw stream, current/queue/receive clocks and lifecycle changes are retained. More samples do not by themselves establish a tradable edge.

## Deploy after a server account exists

With Docker Engine and Compose installed on the VPS:

```sh
git clone https://github.com/Iwetter/UFC-Market-Lab.git
cd UFC-Market-Lab
git checkout research/data-latency-2026-10-29
docker compose up -d --build
docker compose logs --tail=100 recorder
```

The service defaults to real Kalshi public data and **PAPER** execution. A named volume retains the database across container replacement. The image runs as a non-root user, its root filesystem is read-only, logs rotate, and no live order key/config is supplied. Only `127.0.0.1:8000` is published. Access it through SSH:

```sh
ssh -L 8000:127.0.0.1:8000 YOUR_USER@YOUR_SERVER
```

Then open `http://127.0.0.1:8000` locally. Do not expose the raw local API to the internet. The application's same-origin/Host protections and operator tokens remain active. An image build and actual cloud deployment require Docker/server access; Docker was unavailable in the development environment, so no container execution is claimed.

## Add the read-only book stream

Create a read-only Kalshi API key in your own account. Store its private key outside this repository; set `KALSHI_READ_KEY_ID` and `KALSHI_READ_KEY_FILE` in a private `.env` on the server, then use:

```sh
docker compose -f compose.yaml -f compose.read-key.example.yaml up -d --build
```

Ensure the configured non-root container UID can read the mounted secret without broadening host access. File-backed Compose secret ownership/mode behavior depends on the host; verify access using your deployment's secret manager. Never put private keys or order-key variables in tracked YAML, logs or a chat message. The public REST fallback still works without credentials, with its sampling uncertainty recorded.

The licensed DDE adapter is separately opt-in; see `live-data-options.md`. Its secret and license must be mounted explicitly, and provider IDs must be mapped. No server should acquire a trading key merely to record market data.

## Headless mode without Docker

After Python dependencies are installed:

```sh
python -m backend.recorder --source kalshi --database data/kalshi.sqlite3
```

It runs without a browser/frontend until stopped. SIGINT/SIGTERM cancel tasks and close the journal. For a bounded integration smoke test add `--run-seconds 15`. Use a service supervisor for direct Python deployment; configure restart on failure. The desktop launcher, headless recorder and container entrypoint acquire the same OS-released writer lease. Do not run more than one writer per database. Recorder transport reconnection is automatic; a database/disk failure stops the writer, reports failure and lets the container supervisor restart it.

## Health, clocks and backups

`data/collector-health.json` reports heartbeat, public transport freshness, clock state, sequence gaps and disk space every ten seconds. Docker checks both freshness and the `ok` flag. Transport health does not mean a fight feed/model/clock passed trading gates. A disconnected but retrying transport is visible as unhealthy; the reconnect loop remains active.

Backups run every six hours using SQLite's backup API through a separate read-only connection, then `quick_check`, then atomic publication from a unique `.partial`. Incomplete backups remain distinguishable. They are local copies, not disaster recovery: replicate verified backups to your own protected object storage using its credentials and retention policy. No automatic deletion of raw captures/backups is configured. Track growth and archive or expand storage before the one-GiB free-space guard stops recording. Copying a live main `.sqlite3` alone misses WAL data.

Run chrony/NTP on the host. Submit a measured offset to `/api/clock` every 60 seconds with a local operator token; offsets beyond 50 ms or missing observations block paper orders. Container wall time follows the host. A configured NTP daemon is not a measured accuracy result, and no zero-offset sample is fabricated in real mode.

For latency studies, use a single session and complete pre/post-event coverage. Record broadcast observations separately from physical timestamps. The current database has no licensed paired physical fight events proving a twelve-second delay. The dashboard reports that gap explicitly rather than substituting mock observations.

## Fatal collector worker supervision

Collector child-task exceptions or unexpected cancellation now fail the PAPER
engine closed before its next execution tick. The required Kalshi REST poll loop
also cannot return silently. Books become invalid, exchange eligibility is disabled,
pending PAPER orders are canceled and a generic collector fault reaches the main
service supervisor. Health reports identify only the worker and error class; raw
provider exception messages are omitted. The container process can then exit for
its ordinary restart policy. The persisted PAPER kill switch remains set after
restart until operator review; recording may recover independently of order eligibility.

Optional streams/time observers that return because they are disabled or access
is denied do not falsely kill the service. A normal parent cancellation still
joins workers and closes transport resources. This covers fatal task termination;
existing transport reconnect/backoff logic continues handling recoverable failures.
