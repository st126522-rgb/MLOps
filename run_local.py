r"""
Run the pipeline locally without AWS.

Examples:
  python run_local.py
  python run_local.py --stage ingest
  python run_local.py --stage all --local-dir C:\path\to\data
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def run_stage(script: str, extra_args: list[str], env: dict[str, str]) -> None:
    script_path = BASE_DIR / script
    command = [sys.executable, str(script_path), *extra_args]
    print(f"\n[RUNNER] {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=BASE_DIR, env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI news pipeline locally.")
    parser.add_argument("--stage", choices=["all", "ingest", "ner", "drift", "graph", "eval"], default="all")
    parser.add_argument("--local-dir", default=str(BASE_DIR / "local_data"), help="Directory for local pipeline outputs.")
    parser.add_argument("--graph-open", action="store_true", help="Open generated graph HTML files in your browser.")
    args = parser.parse_args()

    env = os.environ.copy()
    env["LOCAL_MODE"] = "true"
    env["LOCAL_DIR"] = args.local_dir
    env["PYTHONUTF8"] = "1"

    stage_map = {
        "ingest": ("ingest.py", []),
        "ner": ("NER.py", []),
        "drift": ("drift.py", []),
        "graph": ("graph.py", ["--dir", args.local_dir]),
        "eval": ("eval.py", []),
    }

    if args.graph_open:
        stage_map["graph"][1].append("--open")

    stages = ["ingest", "ner", "drift", "graph", "eval"] if args.stage == "all" else [args.stage]
    print(f"[RUNNER] Local data directory: {args.local_dir}", flush=True)

    for stage in stages:
        script, extra_args = stage_map[stage]
        run_stage(script, extra_args, env)


if __name__ == "__main__":
    main()
