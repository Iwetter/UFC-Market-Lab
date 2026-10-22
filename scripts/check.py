"""Portable milestone checks with workspace-local temporary files."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

root=Path(__file__).resolve().parents[1]
os.chdir(root)
temp=root/"work"/f"pytest-{time.time_ns()}"
temp.parent.mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable,"-m","pytest","--basetemp",str(temp),"-q"],check=True)
npm=shutil.which("npm")
if not npm: raise SystemExit("npm required for frontend checks")
subprocess.run([npm,"test"],cwd=root/"frontend",check=True)
subprocess.run([npm,"run","build"],cwd=root/"frontend",check=True)
