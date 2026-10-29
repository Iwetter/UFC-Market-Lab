"""Read-only venue recorder; opt-in through documented POLYMARKET_* variables."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.operations import writer_lease
from backend.polymarket import PolymarketRecorder
from backend.store import Store


async def capture(args):
    store = Store(args.database)
    recorder = PolymarketRecorder(SimpleNamespace(store=store), max_markets=args.max_markets)
    task = None
    try:
        if not any(v["enabled"] for v in recorder.snapshot()["venues"].values()):
            raise ValueError("enable at least one venue using documented environment variables")
        if args.once:
            await recorder.poll_once()
        else:
            task = asyncio.create_task(recorder.run())
            await asyncio.sleep(args.run_seconds)
        print(json.dumps(recorder.snapshot(), indent=2))
        if not store.verify():
            raise RuntimeError("event journal integrity failure")
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for client in recorder._owned_clients:
            await client.aclose()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/polymarket.sqlite3")
    parser.add_argument("--once", action="store_true", help="one bounded discovery/books cycle")
    parser.add_argument("--run-seconds", type=float, default=300)
    parser.add_argument("--max-markets", type=int, default=10)
    args = parser.parse_args()
    if args.run_seconds <= 0 or not 1 <= args.max_markets <= 100:
        parser.error("positive duration and market bound 1..100 required")
    with writer_lease(args.database):
        asyncio.run(capture(args))


if __name__ == "__main__":
    main()
