"""
Read drift logs, compute rolling metrics, and write drift reports.
"""

import datetime
import statistics

from config import DRIFT_FLAG_PCT, DRIFT_MEAN_THRESH, DRIFT_WINDOW, MIN_QUEUE_FOR_RETRAIN
from s3_utils import list_keys, load_drift_history, week_key, write_json


def _safe_float(value) -> float | None:
    """Return a float for numeric drift log values and skip malformed artifacts."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_drift_metrics(history: list[dict]) -> dict:
    """Compute rolling drift metrics from the most recent entries."""
    if not history:
        return {"mean_confidence": 1.0, "flagged_pct": 0.0, "total_spans": 0}

    all_scores = []
    all_flagged = 0
    all_total = 0

    for entry in history[:DRIFT_WINDOW]:
        all_scores.extend(
            score
            for score in (_safe_float(value) for value in entry.get("confidence_scores", []))
            if score is not None
        )
        all_flagged += entry.get("flagged_count", 0)
        all_total += entry.get("total_spans", 0)

    return {
        "mean_confidence": round(statistics.mean(all_scores), 4) if all_scores else 1.0,
        "std_confidence": round(statistics.stdev(all_scores), 4) if len(all_scores) > 1 else 0.0,
        "flagged_pct": round(all_flagged / all_total, 4) if all_total > 0 else 0.0,
        "total_spans": all_total,
        "batches_analyzed": min(len(history), DRIFT_WINDOW),
    }


def count_label_queue() -> int:
    return len([key for key in list_keys("label-queue") if key.endswith(".json")])


def run() -> bool:
    week = week_key()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    print(f"\n[DRIFT] Checking drift metrics for {week}")

    history = load_drift_history(n_batches=DRIFT_WINDOW)
    metrics = compute_drift_metrics(history)
    queue_size = count_label_queue()

    print(f"  Mean confidence : {metrics['mean_confidence']:.4f}  (threshold: {DRIFT_MEAN_THRESH})")
    print(f"  Flagged spans   : {metrics['flagged_pct'] * 100:.1f}%  (threshold: {DRIFT_FLAG_PCT * 100:.0f}%)")
    print(f"  Label queue     : {queue_size} items  (min for retrain: {MIN_QUEUE_FOR_RETRAIN})")

    layer1_breach = metrics["mean_confidence"] < DRIFT_MEAN_THRESH
    layer2_breach = metrics["flagged_pct"] > DRIFT_FLAG_PCT
    drift_detected = (layer1_breach or layer2_breach) and queue_size >= MIN_QUEUE_FOR_RETRAIN

    report = {
        "week": week,
        "timestamp": now,
        "metrics": metrics,
        "queue_size": queue_size,
        "layer1_breach": layer1_breach,
        "layer2_breach": layer2_breach,
        "drift_detected": drift_detected,
        "thresholds": {
            "mean_confidence": DRIFT_MEAN_THRESH,
            "flagged_pct": DRIFT_FLAG_PCT,
            "min_queue": MIN_QUEUE_FOR_RETRAIN,
        },
    }

    write_json("drift/reports", f"{week}_report", report)

    if drift_detected:
        print("\n  [ALERT] Drift detected - writing alert")
        write_json(
            "drift/alerts",
            f"{week}_alert",
            {
                **report,
                "action": "retrain_required",
                "trigger_reason": (
                    f"layer1={'YES' if layer1_breach else 'no'}, "
                    f"layer2={'YES' if layer2_breach else 'no'}, "
                    f"queue={queue_size}"
                ),
            },
        )
    else:
        print("  [OK] No drift detected")

    return drift_detected


if __name__ == "__main__":
    detected = run()
    raise SystemExit(1 if detected else 0)
