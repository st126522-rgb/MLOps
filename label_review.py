"""
Create and import a human-review file for low-confidence entity spans.

Workflow:
  python label_review.py export --limit 200
  # Edit local_data/review/label_review.csv
  python label_review.py build-datasets
"""

import argparse
import csv
import hashlib
from pathlib import Path

from s3_utils import list_keys, read_json, storage_path, write_json


REVIEW_COLUMNS = [
    "span_id",
    "status",
    "split",
    "entity",
    "type",
    "corrected_entity",
    "corrected_type",
    "confidence",
    "context",
    "article_id",
    "week",
]


def stable_split(value: str) -> str:
    digest = hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()
    return "eval" if int(digest[:2], 16) % 5 == 0 else "train"


def export_review(limit: int | None = None, output: str | None = None) -> Path:
    rows = []
    seen = set()
    for key in sorted(list_keys("label-queue")):
        item = read_json(key)
        span_id = item.get("span_id") or Path(key).stem
        if span_id in seen:
            continue
        seen.add(span_id)
        rows.append(
            {
                "span_id": span_id,
                "status": "pending",
                "split": stable_split(span_id),
                "entity": item.get("entity", ""),
                "type": item.get("type", ""),
                "corrected_entity": "",
                "corrected_type": "",
                "confidence": item.get("confidence", ""),
                "context": item.get("context", ""),
                "article_id": item.get("article_id", ""),
                "week": item.get("week", ""),
            }
        )
        if limit and len(rows) >= limit:
            break

    review_dir = storage_path("review")
    review_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(output) if output else review_dir / "label_review.csv"

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    instructions = review_dir / "README.txt"
    instructions.write_text(
        "Edit label_review.csv before building datasets.\n"
        "Use status=accept to keep the suggested entity/type.\n"
        "Use status=correct and fill corrected_entity/corrected_type to fix a span.\n"
        "Use status=reject to ignore junk spans.\n"
        "Valid types are ORG, PER, LOC, and MISC.\n",
        encoding="utf-8",
    )
    print(f"[OK] Exported {len(rows)} review rows -> {output_path}")
    return output_path


def build_datasets(input_path: str | None = None) -> tuple[int, int]:
    review_path = Path(input_path) if input_path else storage_path("review/label_review.csv")
    if not review_path.exists():
        raise SystemExit(f"Review file not found: {review_path}. Run `python label_review.py export` first.")

    train_samples = []
    eval_samples = []

    with review_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            status = row.get("status", "").strip().lower()
            if status not in {"accept", "correct"}:
                continue

            entity = row.get("entity", "").strip()
            entity_type = row.get("type", "").strip()
            if status == "correct":
                entity = row.get("corrected_entity", "").strip()
                entity_type = row.get("corrected_type", "").strip()

            if not entity or entity_type not in {"ORG", "PER", "LOC", "MISC"}:
                continue

            sample = {
                "text": row.get("context", "").strip(),
                "entities": [{"entity": entity, "type": entity_type}],
                "source_span_id": row.get("span_id", ""),
            }
            if row.get("split", "").strip().lower() == "eval":
                eval_samples.append(sample)
            else:
                train_samples.append(sample)

    if not train_samples and not eval_samples:
        raise SystemExit("No accepted or corrected labels found. Mark rows as accept/correct in the review CSV first.")

    write_json("labeled", "train_set", train_samples)
    write_json("eval", "test_set", eval_samples or train_samples[: max(1, len(train_samples) // 5)])

    print(f"[OK] Built train samples: {len(train_samples)}")
    print(f"[OK] Built eval samples : {len(eval_samples) or max(1, len(train_samples) // 5)}")
    return len(train_samples), len(eval_samples)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export/import label review CSV files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--limit", type=int, default=None)
    export_parser.add_argument("--output", default=None)

    build_parser = subparsers.add_parser("build-datasets")
    build_parser.add_argument("--input", default=None)

    args = parser.parse_args()
    if args.command == "export":
        export_review(limit=args.limit, output=args.output)
    elif args.command == "build-datasets":
        build_datasets(input_path=args.input)


if __name__ == "__main__":
    main()
