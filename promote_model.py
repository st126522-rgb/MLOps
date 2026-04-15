"""
Promote a passing local candidate model to local models/current.
"""

import argparse
import shutil

from config import F1_IMPROVEMENT_MIN
from s3_utils import key_exists, list_keys, read_json, storage_path, write_json


def get_current_f1() -> float:
    if key_exists("eval/current_result.json"):
        return read_json("eval/current_result.json").get("metrics", {}).get("f1", 0.0)

    current_results = sorted(
        [
            key
            for key in list_keys("eval")
            if "results_" in key and read_json(key).get("model_prefix") == "models/current"
        ],
        reverse=True,
    )
    if not current_results:
        return 0.0
    return read_json(current_results[0]).get("metrics", {}).get("f1", 0.0)


def promote(force: bool = False) -> None:
    candidate_result_key = "eval/candidate_result.json"
    if not key_exists(candidate_result_key):
        raise SystemExit("No candidate eval result found. Run `python eval.py --model-prefix models/candidate --candidate-result` first.")

    candidate_result = read_json(candidate_result_key)
    candidate_f1 = candidate_result["metrics"]["f1"]

    current_f1 = get_current_f1()

    if not force and candidate_f1 <= current_f1 + F1_IMPROVEMENT_MIN:
        raise SystemExit(
            f"Candidate did not clear the gate: candidate={candidate_f1:.4f}, "
            f"current={current_f1:.4f}, required_gain={F1_IMPROVEMENT_MIN:.4f}."
        )

    candidate_dir = storage_path("models/candidate")
    current_dir = storage_path("models/current")
    if not (candidate_dir / "config.json").exists():
        raise SystemExit(f"Candidate model not found: {candidate_dir}")

    if current_dir.exists():
        shutil.rmtree(current_dir)
    shutil.copytree(candidate_dir, current_dir)
    write_json("eval", "current_result", {**candidate_result, "model_prefix": "models/current"})
    print(f"[OK] Promoted candidate model -> {current_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote local candidate model to current.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    promote(force=args.force)


if __name__ == "__main__":
    main()
