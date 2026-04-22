"""
Shared storage helpers for local mode and S3.
"""

import datetime
import json
import pathlib
from pathlib import Path

import boto3

from config import AWS_REGION, BUCKET, LOCAL_DIR, LOCAL_MODE


s3 = None


def _s3():
    global s3
    if s3 is None:
        s3 = boto3.client("s3", region_name=AWS_REGION)
    return s3


def write_json(prefix: str, filename: str, data: dict) -> str:
    key = f"{prefix}/{filename}.json"

    if LOCAL_MODE:
        path = pathlib.Path(LOCAL_DIR) / prefix
        path.mkdir(parents=True, exist_ok=True)
        output = path / f"{filename}.json"
        with output.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, default=str)
        print(f"  [LOCAL] Saved -> {output}")
        return key

    _s3().put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(data, indent=2, default=str),
        ContentType="application/json",
    )
    print(f"  [S3] Saved -> s3://{BUCKET}/{key}")
    return key


def storage_path(prefix: str = "") -> pathlib.Path:
    """Return the local path for a storage prefix when LOCAL_MODE is enabled."""
    return pathlib.Path(LOCAL_DIR) / prefix


def write_bytes(prefix: str, filename: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    key = f"{prefix}/{filename}"

    if LOCAL_MODE:
        path = pathlib.Path(LOCAL_DIR) / prefix
        path.mkdir(parents=True, exist_ok=True)
        output = path / filename
        with output.open("wb") as handle:
            handle.write(data)
        print(f"  [LOCAL] Saved -> {output}")
        return key

    _s3().put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type)
    return key


def write_text(prefix: str, filename: str, text: str) -> str:
    key = f"{prefix}/{filename}"

    if LOCAL_MODE:
        path = pathlib.Path(LOCAL_DIR) / prefix
        path.mkdir(parents=True, exist_ok=True)
        output = path / filename
        output.write_text(text, encoding="utf-8")
        return key

    _s3().put_object(Bucket=BUCKET, Key=key, Body=text.encode("utf-8"), ContentType="text/plain")
    return key


def read_json(key: str) -> dict:
    if LOCAL_MODE:
        with (pathlib.Path(LOCAL_DIR) / key).open(encoding="utf-8") as handle:
            return json.load(handle)

    response = _s3().get_object(Bucket=BUCKET, Key=key)
    return json.loads(response["Body"].read())


def list_keys(prefix: str) -> list[str]:
    if LOCAL_MODE:
        base = pathlib.Path(LOCAL_DIR) / prefix
        if not base.exists():
            return []
        return [
            str(path.relative_to(LOCAL_DIR)).replace("\\", "/")
            for path in base.rglob("*.json")
        ]

    paginator = _s3().get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix + "/"):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".keep"):
                keys.append(obj["Key"])
    return keys


def key_exists(key: str) -> bool:
    if LOCAL_MODE:
        return (pathlib.Path(LOCAL_DIR) / key).exists()

    try:
        _s3().head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def read_all_json(prefix: str) -> list[dict]:
    results = []
    for key in list_keys(prefix):
        if key.endswith(".json"):
            try:
                results.append(read_json(key))
            except Exception as exc:
                print(f"  [WARN] Could not read {key}: {exc}")
    return results


def download_prefix(prefix: str, destination: str | Path) -> list[Path]:
    """Download an S3 prefix into a local directory when running in cloud mode."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    if LOCAL_MODE:
        return list(destination.rglob("*"))

    downloaded = []
    for key in list_keys(prefix):
        if key.endswith(".keep") or key.endswith("/"):
            continue
        relative = key.replace(prefix.rstrip("/") + "/", "", 1)
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        _s3().download_file(BUCKET, key, str(output))
        downloaded.append(output)
    return downloaded


def upload_directory(source_dir: str | Path, prefix: str) -> int:
    """Upload a local directory tree to S3 when running in cloud mode."""
    source_dir = Path(source_dir)
    if not source_dir.exists():
        return 0

    if LOCAL_MODE:
        return sum(1 for path in source_dir.rglob("*") if path.is_file())

    uploaded = 0
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        key = f"{prefix.rstrip('/')}/{path.relative_to(source_dir).as_posix()}"
        content_type = "application/octet-stream"
        if path.suffix == ".json":
            content_type = "application/json"
        _s3().upload_file(
            str(path),
            BUCKET,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        uploaded += 1
    return uploaded


def today_key() -> str:
    return datetime.date.today().isoformat()


def week_key() -> str:
    current = datetime.date.today()
    return f"{current.year}-W{current.isocalendar()[1]:02d}"


def week_key_for(value: datetime.date | datetime.datetime | str) -> str:
    """Return an ISO week key for a provided date-like value."""
    if isinstance(value, str):
        value = datetime.date.fromisoformat(value)
    elif isinstance(value, datetime.datetime):
        value = value.date()

    return f"{value.year}-W{value.isocalendar()[1]:02d}"


def week_sort_key(week: str) -> tuple[int, int]:
    """Return a sortable key for ISO week labels like 2026-W17."""
    year_text, week_text = week.split("-W", 1)
    return int(year_text), int(week_text)


def append_drift_log(batch_id: str, confidences: list[float], week: str) -> str:
    from config import DRIFT_LOW_CONFIDENCE_THRESH

    flagged = [score for score in confidences if score < DRIFT_LOW_CONFIDENCE_THRESH]
    entry = {
        "batch_id": batch_id,
        "week": week,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0,
        "flagged_count": len(flagged),
        "total_spans": len(confidences),
        "flagged_pct": round(len(flagged) / len(confidences), 4) if confidences else 0,
        "confidence_scores": confidences,
    }
    return write_json(f"drift/{week}", batch_id, entry)


def load_drift_history(n_batches: int = 20) -> list[dict]:
    all_entries = read_all_json("drift")
    all_entries.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return all_entries[:n_batches]
