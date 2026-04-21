"""
Verify whether a promoted current model exists and can be loaded for inference.

Examples:
  python verify_current_model.py
  python verify_current_model.py --smoke-text "OpenAI launches a new Claude competitor"
"""

import argparse
from pathlib import Path

import boto3
from transformers import pipeline as hf_pipeline

from config import BUCKET, LOCAL_DIR, LOCAL_MODE, NER_MODEL
from s3_utils import list_keys


def resolve_model_source() -> tuple[str, str]:
    local_current = Path(LOCAL_DIR) / "models" / "current"
    if LOCAL_MODE and (local_current / "config.json").exists():
        return "promoted-local", str(local_current)

    if not LOCAL_MODE:
        model_keys = [
            key for key in list_keys("models/current")
            if not key.endswith((".keep", "/"))
        ]
        if any(key.endswith("config.json") for key in model_keys):
            return "promoted-s3", f"s3://{BUCKET}/models/current"

    return "base-model", NER_MODEL


def list_current_model_keys() -> list[str]:
    if LOCAL_MODE:
        current_dir = Path(LOCAL_DIR) / "models" / "current"
        if not current_dir.exists():
            return []
        return [str(path.relative_to(current_dir)).replace("\\", "/") for path in current_dir.rglob("*") if path.is_file()]

    return [
        key.replace("models/current/", "", 1)
        for key in list_keys("models/current")
        if not key.endswith((".keep", "/"))
    ]


def load_pipeline():
    source_kind, source_path = resolve_model_source()
    if source_kind == "promoted-local":
        return source_kind, source_path, hf_pipeline("ner", model=source_path, aggregation_strategy="simple")

    if source_kind == "promoted-s3":
        import tempfile

        tmpdir = tempfile.TemporaryDirectory()
        s3 = boto3.client("s3")
        for key in list_keys("models/current"):
            if key.endswith((".keep", "/")):
                continue
            relative = key.replace("models/current/", "", 1)
            output = Path(tmpdir.name) / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, key, str(output))
        return source_kind, source_path, hf_pipeline("ner", model=tmpdir.name, aggregation_strategy="simple")

    return source_kind, source_path, hf_pipeline("ner", model=NER_MODEL, aggregation_strategy="simple")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the current promoted model source.")
    parser.add_argument("--smoke-text", default="OpenAI launches a new Claude competitor in Thailand")
    args = parser.parse_args()

    source_kind, source_path = resolve_model_source()
    current_keys = list_current_model_keys()

    print(f"[VERIFY] Model source kind : {source_kind}")
    print(f"[VERIFY] Model source path : {source_path}")
    print(f"[VERIFY] models/current files: {len(current_keys)}")
    if current_keys:
        for key in current_keys[:10]:
            print(f"  - {key}")

    source_kind, source_path, ner = load_pipeline()
    print(f"[VERIFY] Loaded pipeline from: {source_path}")
    predictions = ner(args.smoke_text[:512])
    print(f"[VERIFY] Smoke text: {args.smoke_text}")
    if not predictions:
        print("[VERIFY] No entities returned in smoke test")
    else:
        for item in predictions:
            print(
                f"  - {item['word']} | {item['entity_group']} | {round(float(item['score']), 4)}"
            )


if __name__ == "__main__":
    main()
