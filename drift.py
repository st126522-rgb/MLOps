"""
pipeline/drift.py
==================
Read drift logs from S3 → compute rolling metrics → write drift report to S3.
Replaces CloudWatch in the simplified architecture.

Instead of CloudWatch alarms, we:
  - Write a drift_report.json to S3 on every run
  - If drift is detected, write a drift_alert.json to S3
  - GitHub Actions eval job checks for drift_alert.json and triggers retrain

S3 outputs: drift/reports/YYYY-Www_report.json
            drift/alerts/YYYY-Www_alert.json  (only if drift detected)
"""

import datetime
import statistics
from s3_utils import (
    load_drift_history, write_json, read_all_json,
    list_keys, week_key, key_exists
)
from config import (
    DRIFT_MEAN_THRESH, DRIFT_FLAG_PCT, DRIFT_WINDOW,
    MIN_QUEUE_FOR_RETRAIN
)


def compute_drift_metrics(history: list[dict]) -> dict:
    """Compute rolling drift metrics from N most recent drift log entries."""
    if not history:
        return {"mean_confidence": 1.0, "flagged_pct": 0.0, "total_spans": 0}

    all_scores  = []
    all_flagged = 0
    all_total   = 0

    for entry in history[:DRIFT_WINDOW]:
        all_scores.extend(entry.get("confidence_scores", []))
        all_flagged += entry.get("flagged_count", 0)
        all_total   += entry.get("total_spans", 0)

    return {
        "mean_confidence": round(statistics.mean(all_scores), 4) if all_scores else 1.0,
        "std_confidence":  round(statistics.stdev(all_scores), 4) if len(all_scores) > 1 else 0.0,
        "flagged_pct":     round(all_flagged / all_total, 4) if all_total > 0 else 0.0,
        "total_spans":     all_total,
        "batches_analyzed": min(len(history), DRIFT_WINDOW),
    }


def count_label_queue() -> int:
    """Count pending items in the label queue."""
    week = week_key()
    return len([k for k in list_keys("label-queue") if k.endswith(".json")])


def run() -> bool:
    """
    Returns True if drift is detected and retraining should be triggered.
    """
    week = week_key()
    now  = datetime.datetime.utcnow().isoformat()
    print(f"\n[DRIFT] Checking drift metrics for {week}")

    history = load_drift_history(n_batches=DRIFT_WINDOW)
    metrics = compute_drift_metrics(history)
    queue_size = count_label_queue()

    print(f"  📊 Mean confidence : {metrics['mean_confidence']:.4f}  (threshold: {DRIFT_MEAN_THRESH})")
    print(f"  ⚠️  Flagged spans   : {metrics['flagged_pct']*100:.1f}%  (threshold: {DRIFT_FLAG_PCT*100:.0f}%)")
    print(f"  🗂️  Label queue     : {queue_size} items  (min for retrain: {MIN_QUEUE_FOR_RETRAIN})")

    # ── Layer 1: Confidence score threshold ──────────────
    layer1_breach = metrics["mean_confidence"] < DRIFT_MEAN_THRESH
    # ── Layer 2: Flagged span percentage ─────────────────
    layer2_breach = metrics["flagged_pct"] > DRIFT_FLAG_PCT

    drift_detected = (layer1_breach or layer2_breach) and queue_size >= MIN_QUEUE_FOR_RETRAIN

    report = {
        "week":            week,
        "timestamp":       now,
        "metrics":         metrics,
        "queue_size":      queue_size,
        "layer1_breach":   layer1_breach,
        "layer2_breach":   layer2_breach,
        "drift_detected":  drift_detected,
        "thresholds": {
            "mean_confidence": DRIFT_MEAN_THRESH,
            "flagged_pct":     DRIFT_FLAG_PCT,
            "min_queue":       MIN_QUEUE_FOR_RETRAIN,
        }
    }

    # Always write the report
    write_json(f"drift/reports", f"{week}_report", report)

    if drift_detected:
        print(f"\n  🚨 DRIFT DETECTED — writing alert to S3")
        write_json("drift/alerts", f"{week}_alert", {
            **report,
            "action": "retrain_required",
            "trigger_reason": (
                f"layer1={'YES' if layer1_breach else 'no'}, "
                f"layer2={'YES' if layer2_breach else 'no'}, "
                f"queue={queue_size}"
            )
        })
    else:
        print(f"  ✅ No drift detected")

    return drift_detected


if __name__ == "__main__":
    drift = run()
    exit(0 if not drift else 1)   # exit code 1 = drift detected
