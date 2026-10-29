"""Analyze a journal or explicit event/quote capture; does not place orders."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.latency import StudyConfig,analyze_latency,read_journal,latency_stress_scenarios


def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument("--journal");source.add_argument("--input",help="JSON object with events and quotes arrays")
    p.add_argument("--output",required=True);p.add_argument("--session")
    p.add_argument("--max-records",type=int,help="Optional hard input/metadata row limit; exceeding it fails, never silently truncates")
    p.add_argument("--start-ms",type=int,help="Inclusive UTC receive-time lower bound in epoch milliseconds")
    p.add_argument("--end-ms",type=int,help="Inclusive UTC receive-time upper bound in epoch milliseconds")
    p.add_argument("--include-mocks",action="store_true",help="Explicit software demonstration; never evidence of physical latency")
    p.add_argument("--config",help="Optional StudyConfig JSON; hypotheses must be frozen before reviewing results")
    args=p.parse_args()
    config=json.loads(Path(args.config).read_text()) if args.config else {}
    if args.include_mocks:config["include_mocks"]=True
    if args.journal:events,quotes,warnings=read_journal(args.journal,args.session,max_records=args.max_records,start_ms=args.start_ms,end_ms=args.end_ms)
    else:
        payload=json.loads(Path(args.input).read_text());events=payload["events"];quotes=payload["quotes"];warnings=[]
    report=analyze_latency(events,quotes,StudyConfig.model_validate(config))
    report["warnings"].extend(warnings);report["latency_stresses"]=latency_stress_scenarios()
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(target),"events":report["events_supplied"],"quotes":report["quotes_supplied"],"hypothesis":report["hypothesis"],"broadcast_hypothesis":report["broadcast_hypothesis"],"status_counts":report["status_counts"]},indent=2))


if __name__=="__main__":main()
