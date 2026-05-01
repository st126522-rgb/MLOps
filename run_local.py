"""Root entrypoint for the local AI news pipeline runner."""

from pathlib import Path
import runpy
import sys

PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))
runpy.run_path(str(PIPELINE_DIR / "run_local.py"), run_name="__main__")
