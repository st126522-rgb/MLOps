"""
Load model artifacts, compute F1 on the held-out eval set, and write results.
"""

import argparse
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

import boto3

from config import BUCKET, F1_IMPROVEMENT_MIN, LOCAL_DIR, LOCAL_MODE
from s3_utils import key_exists, list_keys, read_json, storage_path, write_json


def download_model_from_s3(prefix: str, local_dir: str) -> None:
    if LOCAL_MODE:
        return

    s3 = boto3.client("s3")
    keys = [key for key in list_keys(prefix) if not key.endswith(".keep")]
    for key in keys:
        filename = key.split("/")[-1]
        local_path = os.path.join(local_dir, filename)
        s3.download_file(BUCKET, key, local_path)
    print(f"  Downloaded {len(keys)} files from s3://{BUCKET}/{prefix}")


def resolve_local_model_dir(prefix: str) -> Path | None:
    model_dir = storage_path(prefix)
    if (model_dir / "config.json").exists():
        return model_dir
    return None


def load_test_set() -> list[dict]:
    test_key = "eval/test_set.json"
    if not key_exists(test_key):
        print("  [WARN] No test set found - using mock data for CI")
        return [
            {"text": "OpenAI releases GPT-5", "entities": [{"entity": "OpenAI", "type": "ORG"}, {"entity": "GPT-5", "type": "MISC"}]},
            {"text": "Anthropic founded by Dario Amodei", "entities": [{"entity": "Anthropic", "type": "ORG"}, {"entity": "Dario Amodei", "type": "PER"}]},
            {"text": "DeepSeek R2 outperforms GPT-5", "entities": [{"entity": "DeepSeek R2", "type": "MISC"}]},
        ]
    return read_json(test_key)


def compute_f1(predictions: list[dict], ground_truth: list[dict]) -> dict:
    tp = fp = fn = 0
    per_class = {}

    for pred_item, gt_item in zip(predictions, ground_truth):
        pred_entities = {(entity["entity"].lower(), entity["type"]) for entity in pred_item.get("entities", [])}
        gt_entities = {(entity["entity"].lower(), entity["type"]) for entity in gt_item.get("entities", [])}

        for entity in pred_entities:
            entity_type = entity[1]
            per_class.setdefault(entity_type, {"tp": 0, "fp": 0, "fn": 0})
            if entity in gt_entities:
                tp += 1
                per_class[entity_type]["tp"] += 1
            else:
                fp += 1
                per_class[entity_type]["fp"] += 1

        for entity in gt_entities:
            entity_type = entity[1]
            per_class.setdefault(entity_type, {"tp": 0, "fp": 0, "fn": 0})
            if entity not in pred_entities:
                fn += 1
                per_class[entity_type]["fn"] += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    class_f1 = {}
    for entity_type, counts in per_class.items():
        class_precision = counts["tp"] / (counts["tp"] + counts["fp"]) if (counts["tp"] + counts["fp"]) > 0 else 0.0
        class_recall = counts["tp"] / (counts["tp"] + counts["fn"]) if (counts["tp"] + counts["fn"]) > 0 else 0.0
        class_f1[entity_type] = round(2 * class_precision * class_recall / (class_precision + class_recall), 4) if (class_precision + class_recall) > 0 else 0.0

    return {
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "per_class": class_f1,
    }


def get_current_production_f1() -> float:
    if key_exists("eval/current_result.json"):
        latest = read_json("eval/current_result.json")
        return latest.get("metrics", {}).get("f1", 0.0)

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
    latest = read_json(current_results[0])
    return latest.get("metrics", {}).get("f1", 0.0)


def build_pipeline(model_prefix: str):
    from transformers import pipeline as hf_pipeline

    storage_label = f"local://{Path(LOCAL_DIR) / model_prefix}" if LOCAL_MODE else f"s3://{BUCKET}/{model_prefix}"
    print(f"  Loading model from {storage_label}")

    local_model_dir = resolve_local_model_dir(model_prefix) if LOCAL_MODE else None
    if local_model_dir is not None:
        return hf_pipeline("ner", model=str(local_model_dir), aggregation_strategy="simple")

    if not LOCAL_MODE:
        with tempfile.TemporaryDirectory() as tmpdir:
            download_model_from_s3(model_prefix, tmpdir)
            if os.path.exists(os.path.join(tmpdir, "config.json")):
                return hf_pipeline("ner", model=tmpdir, aggregation_strategy="simple")

    from config import NER_MODEL

    print(f"  [WARN] No model files found - using default {NER_MODEL}")
    return hf_pipeline("ner", model=NER_MODEL, aggregation_strategy="simple")


def run_eval(model_prefix: str, upload: bool = False) -> dict:
    ner = build_pipeline(model_prefix)
    test_set = load_test_set()
    predictions = []

    for item in test_set:
        try:
            raw = ner(item["text"][:512])
            entities = [{"entity": result["word"], "type": result["entity_group"], "score": result["score"]} for result in raw]
        except Exception:
            entities = []
        predictions.append({"text": item["text"], "entities": entities})

    metrics = compute_f1(predictions, test_set)
    result = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "model_prefix": model_prefix,
        "test_set_size": len(test_set),
        "metrics": metrics,
    }

    print(f"  F1: {metrics['f1']:.4f}  |  P: {metrics['precision']:.4f}  |  R: {metrics['recall']:.4f}")
    print(f"  Per class: {metrics['per_class']}")

    if upload:
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        write_json("eval", f"results_{timestamp}", result)
        with open("eval_results.json", "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket")
    parser.add_argument("--upload-results", action="store_true")
    parser.add_argument("--current-result", action="store_true")
    parser.add_argument("--candidate-result", action="store_true")
    parser.add_argument("--check-gate", action="store_true")
    parser.add_argument("--model-prefix", default="models/current")
    args = parser.parse_args()

    if args.bucket:
        os.environ["S3_BUCKET"] = args.bucket
    elif not LOCAL_MODE:
        if BUCKET:
            os.environ["S3_BUCKET"] = BUCKET
        else:
            parser.error("--bucket is required when LOCAL_MODE=false")

    if args.check_gate:
        candidate_result = read_json("eval/candidate_result.json") if key_exists("eval/candidate_result.json") else None
        if not candidate_result:
            print("No candidate model result found - gate passes (no new model)")
            raise SystemExit(0)

        current_f1 = get_current_production_f1()
        candidate_f1 = candidate_result["metrics"]["f1"]

        print(f"  Current F1:   {current_f1:.4f}")
        print(f"  Candidate F1: {candidate_f1:.4f}")
        print(f"  Required gain: +{F1_IMPROVEMENT_MIN}")

        if candidate_f1 > current_f1 + F1_IMPROVEMENT_MIN:
            print("  [OK] Gate passed - candidate is eligible for promotion")
            raise SystemExit(0)

        print("  [FAIL] Gate failed - candidate does not improve F1")
        raise SystemExit(1)

    result = run_eval(args.model_prefix, upload=args.upload_results)
    if args.current_result:
        write_json("eval", "current_result", result)
    if args.candidate_result:
        write_json("eval", "candidate_result", result)


if __name__ == "__main__":
    main()
