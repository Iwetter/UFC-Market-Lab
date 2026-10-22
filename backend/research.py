"""CLI for honest training, frozen holdout evaluation, replay and warehouse export."""
import argparse
import json
from pathlib import Path

from .domain import Settings, canonical, digest
from .models import CalibratedPreFight, fit_model, metrics, walk_forward
from .replay import run_replay


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command",required=True)
    train = sub.add_parser("train")
    train.add_argument("input",help="JSON array of licensed point-in-time feature records")
    train.add_argument("output"); train.add_argument("--kind",choices=["prefight","infight"],default="prefight")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("model"); evaluate.add_argument("input"); evaluate.add_argument("output")
    walk=sub.add_parser("walkforward")
    walk.add_argument("input");walk.add_argument("output");walk.add_argument("--years",type=int,nargs='+',required=True)
    walk.add_argument("--kind",choices=["prefight","infight"],default="prefight")
    replay = sub.add_parser("replay")
    replay.add_argument("input"); replay.add_argument("output"); replay.add_argument("--source",choices=["mock","kalshi"],default="mock")
    replay.add_argument("--session"); replay.add_argument("--fight-delay-ms",type=int,default=0)
    replay.add_argument("--model");replay.add_argument("--infight-model")
    export = sub.add_parser("export")
    export.add_argument("input"); export.add_argument("output")
    args = parser.parse_args()
    if args.command == "train":
        artifact = fit_model(json.loads(Path(args.input).read_text()),args.kind)
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(json.dumps(artifact,indent=2))
        print("Fitted and calibrated artifact saved. No out-of-sample claim: evaluate a later lockbox separately.")
    elif args.command == "walkforward":
        if Path(args.output).exists(): raise ValueError("report already exists; use a new preregistered run")
        report=walk_forward(json.loads(Path(args.input).read_text()),args.years,args.kind)
        Path(args.output).write_text(json.dumps(report,indent=2));print(canonical(report))
    elif args.command == "evaluate":
        artifact = json.loads(Path(args.model).read_text())
        model = CalibratedPreFight(artifact,artifact["kind"])
        rows = json.loads(Path(args.input).read_text())
        if Path(args.output).exists(): raise ValueError("lockbox report already exists; do not overwrite/re-test")
        if not rows or any(r["decision_ms"] <= artifact["training_cutoff_ms"] or r["available_ms"] > r["decision_ms"] or r["label_available_ms"] <= r["decision_ms"] for r in rows):
            raise ValueError("lockbox must be strictly later than training and calibration")
        probabilities = [model.predict(r["market_probability"],{k:r[k] for k in model.features},as_of_ms=r["decision_ms"]) for r in rows]
        report = {"model":artifact["spec_sha256"],"input_sha256":digest(rows),"model_metrics":metrics(probabilities,[r["y"] for r in rows]),
                  "market_metrics":metrics([r["market_probability"] for r in rows],[r["y"] for r in rows]),
                  "claim":"Descriptive frozen-holdout metrics only; no evidence of trading edge."}
        Path(args.output).write_text(json.dumps(report,indent=2))
        print(canonical(report))
    elif args.command == "replay":
        cfg = Settings.model_validate_json(Path("config/default.json").read_text()).model_copy(update={"source":args.source})
        report = run_replay(args.input,args.output,cfg,{"licensed":args.fight_delay_ms,"mock":args.fight_delay_ms} if args.fight_delay_ms else {},args.session,args.model,args.infight_model)
        print(canonical({k:v for k,v in report.items() if k != "result"}))
    elif args.command == "export":
        import sqlite3,duckdb
        if Path(args.output).exists(): raise ValueError("warehouse output must be new")
        src = sqlite3.connect(f"file:{Path(args.input).resolve().as_posix()}?mode=ro",uri=True)
        out = duckdb.connect(args.output)
        for table in ["events","orders","fills","ledger","predictions","mappings","runs"]:
            out.execute(f"CREATE TABLE {table}(record JSON)")
            src.row_factory = sqlite3.Row
            rows = [(canonical(dict(r)),) for r in src.execute(f"SELECT * FROM {table}")]
            if rows: out.executemany(f"INSERT INTO {table} VALUES (?)",rows)
        out.close(); src.close()
        print("Immutable research snapshot exported to DuckDB JSON tables.")


if __name__ == "__main__": main()
