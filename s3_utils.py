"""
pipeline/s3_utils.py
=====================
All S3 operations in one place.
Every pipeline stage reads/writes through these helpers.

WHY S3 ONLY (no DynamoDB):
  - Free tier: 5 GB S3 vs 25 GB DynamoDB — both fine for this scale
  - S3 JSON files are human-readable, debuggable, downloadable
  - No schema to define — just write dicts as JSON
  - Versioning gives us history for free
  - Simplest possible mental model: treat S3 like a filesystem
"""

import json
import boto3
import datetime
from pathlib import Path
from config import BUCKET, AWS_REGION

s3 = boto3.client("s3", region_name=AWS_REGION)


# ── Write helpers ──────────────────────────────────────────

def write_json(prefix: str, filename: str, data: dict) -> str:
    """Write a dict as JSON to s3://BUCKET/prefix/filename.json"""
    key = f"{prefix}/{filename}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(data, indent=2, default=str),
        ContentType="application/json"
    )
    print(f"  ✅ Saved → s3://{BUCKET}/{key}")
    return key


def write_bytes(prefix: str, filename: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Write raw bytes (e.g. PNG graph images) to S3"""
    key = f"{prefix}/{filename}"
    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type)
    print(f"  ✅ Saved → s3://{BUCKET}/{key}")
    return key


def write_text(prefix: str, filename: str, text: str) -> str:
    """Write plain text to S3"""
    key = f"{prefix}/{filename}"
    s3.put_object(Bucket=BUCKET, Key=key, Body=text.encode("utf-8"), ContentType="text/plain")
    return key


# ── Read helpers ───────────────────────────────────────────

def read_json(key: str) -> dict:
    """Read JSON from a full S3 key"""
    response = s3.get_object(Bucket=BUCKET, Key=key)
    return json.loads(response["Body"].read())


def list_keys(prefix: str) -> list[str]:
    """List all object keys under a prefix"""
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix + "/"):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".keep"):
                keys.append(obj["Key"])
    return keys


def read_all_json(prefix: str) -> list[dict]:
    """Read all JSON files under a prefix — returns list of dicts"""
    results = []
    for key in list_keys(prefix):
        if key.endswith(".json"):
            try:
                results.append(read_json(key))
            except Exception as e:
                print(f"  ⚠️  Could not read {key}: {e}")
    return results


def key_exists(key: str) -> bool:
    """Check if a key exists in S3"""
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except s3.exceptions.NoSuchKey:
        return False
    except Exception:
        return False


# ── Convenience: dated key helpers ────────────────────────

def today_key() -> str:
    return datetime.date.today().isoformat()   # e.g. "2025-03-11"

def week_key() -> str:
    d = datetime.date.today()
    return f"{d.year}-W{d.isocalendar()[1]:02d}"   # e.g. "2025-W10"


# ── Drift log: append confidence scores to S3 JSON ────────

def append_drift_log(batch_id: str, confidences: list[float], week: str) -> str:
    """
    Saves a drift log entry to s3://BUCKET/drift/YYYY-Www/batch_id.json
    Each entry has: batch_id, week, mean_confidence, flagged_count, all_scores
    """
    from config import CONFIDENCE_THRESH
    flagged = [c for c in confidences if c < CONFIDENCE_THRESH]

    entry = {
        "batch_id":         batch_id,
        "week":             week,
        "timestamp":        datetime.datetime.utcnow().isoformat(),
        "mean_confidence":  round(sum(confidences) / len(confidences), 4) if confidences else 0,
        "flagged_count":    len(flagged),
        "total_spans":      len(confidences),
        "flagged_pct":      round(len(flagged) / len(confidences), 4) if confidences else 0,
        "confidence_scores": confidences,
    }
    return write_json(f"drift/{week}", batch_id, entry)


def load_drift_history(n_batches: int = 20) -> list[dict]:
    """Load the N most recent drift log entries for rolling window calculation"""
    all_entries = read_all_json("drift")
    # Sort by timestamp descending
    all_entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return all_entries[:n_batches]
