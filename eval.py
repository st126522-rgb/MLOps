"""
pipeline/eval.py
=================
Load model artifact from S3 → compute F1 on held-out eval set → write results to S3.
Used by GitHub Actions CI/CD as the deployment gate.

Usage:
  python eval.py --bucket my-bucket --upload-results
  python eval.py --bucket my-bucket --check-gate   # exits 1 if F1 regressed

S3 inputs:  models/current/          ← current production model
            models/candidate/        ← newly fine-tuned model (if exists)
            eval/test_set.json       ← held-out labeled articles
S3 outputs: eval/results_TIMESTAMP.json
"""

import json
import argparse
import datetime
import tempfile
import os
import boto3
import sys
from s3_utils import write_json, read_json, list_keys, key_exists
from config import BUCKET, F1_IMPROVEMENT_MIN


def download_model_from_s3(prefix: str, local_dir: str):
    """Download a model directory from S3 to a local temp directory."""
    s3 = boto3.client("s3")
    keys = [k for k in list_keys(prefix) if not k.endswith(".keep")]
    for key in keys:
        filename = key.split("/")[-1]
        local_path = os.path.join(local_dir, filename)
        s3.download_file(BUCKET, key, local_path)
    print(f"  Downloaded {len(keys)} files from s3://{BUCKET}/{prefix}")


def load_test_set() -> list[dict]:
    """
    Load held-out test set from S3.
    Format: list of {text, entities: [{entity, type}]}
    """
    test_key = "eval/test_set.json"
    if not key_exists(test_key):
        # Return mock test set if real one doesn't exist yet
        print("  ⚠️  No test set found — using mock data for CI")
        return [
            {"text": "OpenAI releases GPT-5", "entities": [{"entity": "OpenAI", "type": "ORG"}, {"entity": "GPT-5", "type": "MISC"}]},
            {"text": "Anthropic founded by Dario Amodei", "entities": [{"entity": "Anthropic", "type": "ORG"}, {"entity": "Dario Amodei", "type": "PER"}]},
            {"text": "DeepSeek R2 outperforms GPT-5", "entities": [{"entity": "DeepSeek R2", "type": "MISC"}]},
        ]
    return read_json(test_key)


def compute_f1(predictions: list[dict], ground_truth: list[dict]) -> dict:
    """
    Compute precision, recall, F1 for NER predictions vs. ground truth.
    Simplified span-level matching.
    """
    tp = fp = fn = 0
    per_class = {}

    for pred_item, gt_item in zip(predictions, ground_truth):
        pred_entities = {(e["entity"].lower(), e["type"]) for e in pred_item.get("entities", [])}
        gt_entities   = {(e["entity"].lower(), e["type"]) for e in gt_item.get("entities", [])}

        for ent in pred_entities:
            etype = ent[1]
            per_class.setdefault(etype, {"tp": 0, "fp": 0, "fn": 0})
            if ent in gt_entities:
                tp += 1
                per_class[etype]["tp"] += 1
            else:
                fp += 1
                per_class[etype]["fp"] += 1

        for ent in gt_entities:
            etype = ent[1]
            per_class.setdefault(etype, {"tp": 0, "fp": 0, "fn": 0})
            if ent not in pred_entities:
                fn += 1
                per_class[etype]["fn"] += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Per-class F1
    class_f1 = {}
    for cls, counts in per_class.items():
        p = counts["tp"] / (counts["tp"] + counts["fp"]) if (counts["tp"] + counts["fp"]) > 0 else 0.0
        r = counts["tp"] / (counts["tp"] + counts["fn"]) if (counts["tp"] + counts["fn"]) > 0 else 0.0
        class_f1[cls] = round(2 * p * r / (p + r), 4) if (p + r) > 0 else 0.0

    return {
        "f1":        round(f1, 4),
        "precision": round(precision, 4),
        "recall":    round(recall, 4),
        "per_class": class_f1,
    }


def get_current_production_f1() -> float:
    """Read the current production model's F1 from S3."""
    results_keys = sorted([k for k in list_keys("eval") if "results_" in k], reverse=True)
    if not results_keys:
        return 0.0  # no baseline yet — any model passes
    latest = read_json(results_keys[0])
    return latest.get("metrics", {}).get("f1", 0.0)


def run_eval(model_prefix: str, upload: bool = False) -> dict:
    """Run evaluation for a model stored in S3."""
    from transformers import pipeline as hf_pipeline

    print(f"  Loading model from s3://{BUCKET}/{model_prefix}")
    with tempfile.TemporaryDirectory() as tmpdir:
        download_model_from_s3(model_prefix, tmpdir)

        # If directory has model files, load from local; else use default
        if os.path.exists(os.path.join(tmpdir, "config.json")):
            ner = hf_pipeline("ner", model=tmpdir, aggregation_strategy="simple")
        else:
            from config import NER_MODEL
            print(f"  ⚠️  No model files found — using default {NER_MODEL}")
            ner = hf_pipeline("ner", model=NER_MODEL, aggregation_strategy="simple")

        test_set = load_test_set()
        predictions = []

        for item in test_set:
            try:
                raw = ner(item["text"][:512])
                entities = [{"entity": r["word"], "type": r["entity_group"], "score": r["score"]} for r in raw]
            except Exception:
                entities = []
            predictions.append({"text": item["text"], "entities": entities})

    metrics = compute_f1(predictions, test_set)

    result = {
        "timestamp":    datetime.datetime.utcnow().isoformat(),
        "model_prefix": model_prefix,
        "test_set_size": len(test_set),
        "metrics":      metrics,
    }

    print(f"  📊 F1: {metrics['f1']:.4f}  |  P: {metrics['precision']:.4f}  |  R: {metrics['recall']:.4f}")
    print(f"  Per class: {metrics['per_class']}")

    if upload:
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        write_json("eval", f"results_{ts}", result)
        # Save local copy for GitHub Actions artifact
        with open("eval_results.json", "w") as f:
            json.dump(result, f, indent=2)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket",         required=True)
    parser.add_argument("--upload-results", action="store_true")
    parser.add_argument("--check-gate",     action="store_true")
    parser.add_argument("--model-prefix",   default="models/current")
    args = parser.parse_args()

    os.environ["S3_BUCKET"] = args.bucket

    if args.check_gate:
        # Check if a candidate model exists and is better than current
        candidate_result = read_json("eval/candidate_result.json") if key_exists("eval/candidate_result.json") else None
        if not candidate_result:
            print("No candidate model result found — gate passes (no new model)")
            sys.exit(0)

        current_f1   = get_current_production_f1()
        candidate_f1 = candidate_result["metrics"]["f1"]

        print(f"  Current F1:   {current_f1:.4f}")
        print(f"  Candidate F1: {candidate_f1:.4f}")
        print(f"  Required gain: +{F1_IMPROVEMENT_MIN}")

        if candidate_f1 > current_f1 + F1_IMPROVEMENT_MIN:
            print("  ✅ GATE PASSED — candidate model promoted")
            sys.exit(0)
        else:
            print("  ❌ GATE FAILED — candidate does not improve F1")
            sys.exit(1)

    else:
        run_eval(args.model_prefix, upload=args.upload_results)


if __name__ == "__main__":
    main()
