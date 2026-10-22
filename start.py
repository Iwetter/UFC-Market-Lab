"""One-command local startup: python start.py [--source kalshi]."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import venv
import webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def run(command,cwd=ROOT):
    subprocess.run([str(c) for c in command],cwd=cwd,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",choices=["mock","kalshi"],default="mock")
    parser.add_argument("--port",type=int,default=8000)
    parser.add_argument("--database",help="Separate SQLite path; mock and real sources cannot share it")
    parser.add_argument("--model",help="Pre-fight JSON artifact from research train")
    parser.add_argument("--infight-model",help="Separate calibrated checkpoint JSON artifact")
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--no-install",action="store_true",help="Use current Python and existing frontend build")
    parser.add_argument("--prepared",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args()
    os.chdir(ROOT)
    if sys.version_info < (3,12): raise SystemExit("Python 3.12 or newer is required")
    if not args.no_install and not args.prepared:
        env_path=ROOT/".venv"
        python=env_path/("Scripts/python.exe" if os.name=="nt" else "bin/python")
        if not python.exists():
            print("Creating an isolated Python environment…",flush=True)
            venv.create(env_path,with_pip=True)
        requirements=ROOT/"requirements.txt"
        stamp=hashlib.sha256(requirements.read_bytes()).hexdigest()
        marker=env_path/".requirements.sha256"
        if not marker.exists() or marker.read_text()!=stamp:
            run([python,"-m","pip","install","-r",requirements]);marker.write_text(stamp)
        npm=shutil.which("npm")
        if not npm: raise SystemExit("Install Node.js 20.19+ (or 22.12+) and npm, then run python start.py again")
        frontend=ROOT/"frontend"
        lockstamp=hashlib.sha256((frontend/"package-lock.json").read_bytes()).hexdigest()
        npm_marker=frontend/"node_modules/.lock.sha256"
        if not npm_marker.exists() or npm_marker.read_text()!=lockstamp:
            run([npm,"ci","--no-audit","--no-fund"],frontend);npm_marker.write_text(lockstamp)
        run([npm,"run","build"],frontend)
        run([python,ROOT/"start.py",*sys.argv[1:],"--prepared"])
        return
    from backend.app import create_app
    from backend.domain import Settings
    import uvicorn
    cfg=Settings.model_validate_json((ROOT/"config/default.json").read_text())
    cfg=cfg.model_copy(update={"source":args.source,"database":args.database or f"data/{args.source}.sqlite3"})
    app=create_app(cfg,model_path=args.model,infight_model_path=args.infight_model)
    url=f"http://127.0.0.1:{args.port}"
    print(f"\nOctagon / UFC Market Lab\n{url}\nSource: {args.source.upper()} | Execution: PAPER | Live adapter: disabled\nDatabase: {cfg.database}\nCtrl+C stops the engine.\n",flush=True)
    if not args.no_browser:
        threading.Timer(1.5,lambda:webbrowser.open(url)).start()
    uvicorn.run(app,host="127.0.0.1",port=args.port,access_log=False)


if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: pass
