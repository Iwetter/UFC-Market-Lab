"""Acquire reviewed CC-BY-4.0 UFC observations and fit research-only baselines.

Run from the repository root: python scripts/acquire_mma_data.py --train
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.historical import (acquire_source,read_csv,build_prefight_samples,build_round_samples,
                                PREFIGHT_FEATURES,ROUND_FEATURES,SOURCE_COMMIT)
from backend.historical_models import fit_research_baseline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,default=ROOT/"data/research/nlbair")
    parser.add_argument("--train",action="store_true")
    parser.add_argument("--artifact-output",type=Path,help="Optional directory for reviewable research-only fitted JSON artifacts")
    parser.add_argument("--report",type=Path,default=ROOT/"docs/data-training-report.json")
    parser.add_argument("--train-end",default="2022-12-31")
    parser.add_argument("--calibration-end",default="2023-12-31")
    args=parser.parse_args()
    manifest=acquire_source(args.directory)
    fights=read_csv(args.directory/"data/ufc_all_fights_wide.csv")
    rounds=read_csv(args.directory/"data/ufc_judge_rounds.csv")
    prefight,pf_summary=build_prefight_samples(fights)
    judge,j_summary=build_round_samples(rounds,fights)
    output=args.directory/"derived";output.mkdir(parents=True,exist_ok=True)
    (output/"prefight-samples.json").write_text(json.dumps(prefight),encoding="utf-8")
    (output/"round-scoring-samples.json").write_text(json.dumps(judge),encoding="utf-8")
    report={"source_repository":"https://github.com/nlbair/ufc-data","source_commit":SOURCE_COMMIT,
            "source_manifest":manifest,"prefight_samples":pf_summary,"round_scoring_samples":j_summary,
            "source_date_range":[min(r["event_date"] for r in fights),max(r["event_date"] for r in fights)],
            "not_a_trading_edge_result":True,"observed_market_fight_delay_seconds":None,
            "position_limitations":"Distance/clinch/ground describe strike contexts. Mount, guard, back-control occupancy and transition timestamps are absent; no probabilities for those positions are inferred."}
    if args.train:
        provenance={k:v for k,v in manifest.items() if k != "retrieved_utc"}
        for key,samples,features,target in (("prefight",prefight,PREFIGHT_FEATURES,"prefight_fight_winner"),
                                            ("round_scoring",judge,ROUND_FEATURES,"majority_judge_round_winner")):
            artifact,evaluation=fit_research_baseline(samples,features,target=target,train_end=args.train_end,
                                      calibration_end=args.calibration_end,source_manifest=provenance)
            path=output/f"{key}-research-model.json"
            path.write_text(json.dumps(artifact,indent=2),encoding="utf-8")
            if args.artifact_output:
                args.artifact_output.mkdir(parents=True,exist_ok=True)
                path=args.artifact_output/f"{key}.json"
                path.write_text(json.dumps(artifact,indent=2),encoding="utf-8")
                path=path.resolve()
            report[key+"_evaluation"]=evaluation
            report[key+"_artifact"]=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"source_fights":len(fights),"source_judge_rows":len(rounds),
                      "prefight_samples":len(prefight),"round_samples":len(judge),"trained":args.train,"report":str(args.report)},indent=2))


if __name__ == "__main__":main()
