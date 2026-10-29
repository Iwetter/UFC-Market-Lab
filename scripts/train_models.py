"""Compare fixed small model candidates using cached pinned source bytes only."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.ml_pipeline import run_comparison


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Explicitly refit changed/reviewed experiment despite matching cache")
    args = parser.parse_args()
    report = run_comparison(ROOT, force=args.force)
    print(json.dumps({"selected": report["selected_candidate"], "holdout": report["selected_calibrated_holdout"],
                      "report": "docs/model-comparison.json", "eligible_for_live": False}, indent=2))
