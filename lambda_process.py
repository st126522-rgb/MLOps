"""
AWS Lambda entry point for event-driven EC2 processing.

Configure this Lambda on S3 ObjectCreated events for raw/*.json objects. The
handler starts the existing EC2 Docker pipeline through AWS Systems Manager Run
Command, so ingestion can remain lightweight while NER, drift metrics, graph
generation, and dashboard refresh stay on EC2.

Required Lambda environment:
  S3_BUCKET=<your-bucket>

Target selection, choose one:
  PROCESS_INSTANCE_ID=<ec2-instance-id>
  or
  PROCESS_TARGET_TAG_KEY=Role
  PROCESS_TARGET_TAG_VALUE=ai-news-mlops-processor

Optional:
  PROCESS_APP_DIR=/opt/ai-news-mlops/MLOps
  PROCESS_COMMAND="cd /opt/... && RUN_INGEST_IN_EC2=false bash scripts/run_cloud_pipeline_logged.sh"
  PROCESS_DOCUMENT_NAME=AWS-RunShellScript
  PROCESS_DRY_RUN=true
"""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any

import boto3


DEFAULT_APP_DIR = "/opt/ai-news-mlops/MLOps"
DEFAULT_DOCUMENT = "AWS-RunShellScript"


def raw_keys_from_event(event: dict[str, Any], bucket: str | None = None) -> list[str]:
    """Return raw JSON object keys from an S3 event payload."""
    keys: list[str] = []

    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue

        s3_info = record.get("s3", {})
        event_bucket = s3_info.get("bucket", {}).get("name")
        if bucket and event_bucket and event_bucket != bucket:
            continue

        raw_key = s3_info.get("object", {}).get("key", "")
        key = urllib.parse.unquote_plus(raw_key)
        if key.startswith("raw/") and key.endswith(".json"):
            keys.append(key)

    return keys


def build_pipeline_command() -> str:
    """Build the EC2 shell command that runs the downstream processing loop."""
    explicit = os.getenv("PROCESS_COMMAND")
    if explicit:
        return explicit

    app_dir = os.getenv("PROCESS_APP_DIR", DEFAULT_APP_DIR)
    return (
        f"cd {app_dir} && "
        "RUN_INGEST_IN_EC2=false /bin/bash scripts/run_cloud_pipeline_logged.sh"
    )


def build_ssm_target() -> dict[str, Any]:
    """Return SSM send_command target arguments from environment variables."""
    instance_id = os.getenv("PROCESS_INSTANCE_ID")
    if instance_id:
        return {"InstanceIds": [instance_id]}

    tag_key = os.getenv("PROCESS_TARGET_TAG_KEY", "Role")
    tag_value = os.getenv("PROCESS_TARGET_TAG_VALUE", "ai-news-mlops-processor")
    return {"Targets": [{"Key": f"tag:{tag_key}", "Values": [tag_value]}]}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    bucket = os.getenv("S3_BUCKET")
    manual_run = bool(event.get("manual_run"))
    keys = ["manual-run"] if manual_run else raw_keys_from_event(event, bucket=bucket)

    if not keys:
        return {
            "statusCode": 202,
            "body": json.dumps({"message": "no raw json objects to process"}),
        }

    command = build_pipeline_command()
    if os.getenv("PROCESS_DRY_RUN", "false").lower() == "true":
        return {
            "statusCode": 200,
            "body": json.dumps({"dry_run": True, "keys": keys, "command": command}),
        }

    ssm = boto3.client("ssm")
    response = ssm.send_command(
        **build_ssm_target(),
        DocumentName=os.getenv("PROCESS_DOCUMENT_NAME", DEFAULT_DOCUMENT),
        Parameters={"commands": [command]},
        Comment=f"AI News downstream processing for {len(keys)} raw batch(es)",
        TimeoutSeconds=3600,
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "processing command submitted",
                "keys": keys,
                "manual_run": manual_run,
                "command_id": response["Command"]["CommandId"],
            }
        ),
    }
