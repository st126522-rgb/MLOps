"""
Publish the latest drift report to CloudWatch custom metrics.

This script is intentionally small so it can run at the end of the EC2 Docker
pipeline. CloudWatch alarms can then notify SNS when drift requires review.
"""

import argparse
import datetime
import sys

import boto3

from config import AWS_REGION, LABEL_QUEUE_ALERT_SIZE, LOCAL_MODE
from s3_utils import list_keys, read_json


DEFAULT_NAMESPACE = "AI/NewsNER"


def latest_report_key() -> str | None:
    report_keys = [key for key in list_keys("drift/reports") if key.endswith(".json")]
    if not report_keys:
        return None
    report_keys.sort()
    return report_keys[-1]


def metric_data_from_report(report: dict) -> list[dict]:
    metrics = report.get("metrics", {})
    timestamp = datetime.datetime.now(datetime.UTC)

    return [
        {
            "MetricName": "MeanConfidence",
            "Timestamp": timestamp,
            "Value": float(metrics.get("mean_confidence", 1.0)),
            "Unit": "None",
        },
        {
            "MetricName": "FlaggedSpanPercentage",
            "Timestamp": timestamp,
            "Value": float(metrics.get("flagged_pct", 0.0)) * 100,
            "Unit": "Percent",
        },
        {
            "MetricName": "LabelQueueSize",
            "Timestamp": timestamp,
            "Value": int(report.get("queue_size", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "DriftDetected",
            "Timestamp": timestamp,
            "Value": 1 if report.get("drift_detected") else 0,
            "Unit": "Count",
        },
        {
            "MetricName": "LabelQueueReady",
            "Timestamp": timestamp,
            "Value": 1 if int(report.get("queue_size", 0)) >= LABEL_QUEUE_ALERT_SIZE else 0,
            "Unit": "Count",
        },
    ]


def publish(namespace: str, dry_run: bool = False) -> bool:
    key = latest_report_key()
    if not key:
        print("[METRICS] No drift report found; skipping CloudWatch publish")
        return False

    report = read_json(key)
    metric_data = metric_data_from_report(report)

    print(f"[METRICS] Latest report: {key}")
    for item in metric_data:
        print(f"  {item['MetricName']}: {item['Value']} {item['Unit']}")

    if dry_run or LOCAL_MODE:
        print("[METRICS] Dry run/local mode; not publishing to CloudWatch")
        return True

    cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
    cloudwatch.put_metric_data(Namespace=namespace, MetricData=metric_data)
    print(f"[METRICS] Published to CloudWatch namespace: {namespace}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish latest drift report to CloudWatch.")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    try:
        publish(namespace=args.namespace, dry_run=args.dry_run)
    except Exception as exc:
        print(f"[METRICS][WARN] Could not publish CloudWatch metrics: {exc}", file=sys.stderr)
        if args.fail_on_error:
            raise


if __name__ == "__main__":
    main()
