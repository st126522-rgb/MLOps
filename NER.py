"""
Load raw articles, run NER, and save entity results plus drift artifacts.
"""

import tempfile
from pathlib import Path

import boto3
from transformers import pipeline as hf_pipeline

from config import BUCKET, LABEL_CONFIDENCE_THRESH, LOCAL_DIR, LOCAL_MODE, NER_MODEL
from entity_postprocess import merge_model_entities
from s3_utils import append_drift_log, list_keys, read_json, week_key, write_json


_MODEL_TEMP_DIR = None


def resolve_inference_model() -> str:
    """Use a promoted current model when present, otherwise fall back to the base model."""
    local_current = Path(LOCAL_DIR) / "models" / "current"
    if LOCAL_MODE and (local_current / "config.json").exists():
        return str(local_current)

    if not LOCAL_MODE:
        model_keys = [
            key for key in list_keys("models/current")
            if not key.endswith((".keep", "/"))
        ]
        if not any(key.endswith("config.json") for key in model_keys):
            return NER_MODEL

        if model_keys:
            global _MODEL_TEMP_DIR
            _MODEL_TEMP_DIR = tempfile.TemporaryDirectory()
            s3 = boto3.client("s3")
            for key in model_keys:
                relative = key.replace("models/current/", "", 1)
                output = Path(_MODEL_TEMP_DIR.name) / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(BUCKET, key, str(output))
            return _MODEL_TEMP_DIR.name

    return NER_MODEL


MODEL_SOURCE = resolve_inference_model()
print(f"Loading NER model: {MODEL_SOURCE}")
ner = hf_pipeline("ner", model=MODEL_SOURCE, aggregation_strategy="simple")
print("Model loaded [OK]")


def is_valid_span(word: str) -> bool:
    if word.startswith("##"):
        return False
    if len(word.strip("#")) <= 1:
        return False
    if word.isupper() and len(word) <= 2:
        return False
    if len(word) <= 2:
        return False
    return True


def extract_entities(text: str) -> list[dict]:
    if not text or len(text.strip()) < 10:
        return []
    try:
        results = ner(text[:512])
        clean = []
        for result in results:
            if not is_valid_span(result["word"]):
                continue
            clean.append(
                {
                    "entity": result["word"],
                    "type": result["entity_group"],
                    "confidence": round(result["score"], 4),
                    "flagged": result["score"] < LABEL_CONFIDENCE_THRESH,
                    "label_threshold": LABEL_CONFIDENCE_THRESH,
                    "start": result["start"],
                    "end": result["end"],
                }
            )
        return merge_model_entities(text, clean)
    except Exception as exc:
        print(f"  [WARN] NER error: {exc}")
        return []


def process_batch(batch: dict) -> dict:
    results = []
    all_confidences = []
    flagged_spans = []

    for article in batch.get("articles", []):
        text = f"{article['title']} {article.get('summary', '')}"
        entities = extract_entities(text)

        for entity in entities:
            all_confidences.append(entity["confidence"])
            if entity["flagged"]:
                flagged_spans.append(
                    {
                        "span_id": f"{article['id']}_{entity['start']}",
                        "entity": entity["entity"],
                        "type": entity["type"],
                        "confidence": entity["confidence"],
                        "context": text[:200],
                        "article_id": article["id"],
                        "week": batch["week"],
                        "status": "pending_label",
                    }
                )

        results.append(
            {
                "article_id": article["id"],
                "title": article["title"],
                "entities": entities,
            }
        )

    return {
        "batch_id": batch["batch_id"],
        "week": batch["week"],
        "article_count": len(batch.get("articles", [])),
        "entity_results": results,
        "all_confidences": all_confidences,
        "flagged_spans": flagged_spans,
    }


def run(force: bool = False) -> None:
    week = week_key()
    print(f"\n[NER] Processing week {week} (force={force})")

    raw_keys = set(key.split("/")[-1] for key in list_keys(f"raw/{week}"))

    if force:
        pending = list(raw_keys)
    else:
        entity_keys = set(key.split("/")[-1] for key in list_keys(f"entities/{week}"))
        pending = [key for key in raw_keys if key not in entity_keys]

    for key_name in pending:
        batch_id = key_name.replace(".json", "")
        raw_data = read_json(f"raw/{week}/{key_name}")
        print(f"  Processing batch: {batch_id} ({len(raw_data.get('articles', []))} articles)")

        result = process_batch(raw_data)
        write_json(f"entities/{week}", batch_id, result)

        if result["all_confidences"]:
            append_drift_log(batch_id, result["all_confidences"], week)

        for span in result["flagged_spans"]:
            write_json(f"label-queue/{week}", span["span_id"], span)
            print(f"    [FLAGGED] '{span['entity']}' (conf: {span['confidence']})")

        print(f"  [OK] {len(result['entity_results'])} articles processed, {len(result['flagged_spans'])} spans flagged")


if __name__ == "__main__":
    run(force=True)
