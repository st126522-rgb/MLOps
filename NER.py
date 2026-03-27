"""
pipeline/ner.py
================
Load raw articles from S3 → run NER → save entities + drift log to S3.

S3 inputs:  raw/YYYY-Www/batch_id.json
S3 outputs: entities/YYYY-Www/batch_id.json
            drift/YYYY-Www/batch_id.json
            label-queue/YYYY-Www/span_id.json  (low-confidence spans only)
"""

import datetime
from transformers import pipeline as hf_pipeline
from s3_utils import (
    read_all_json, write_json, append_drift_log, week_key, list_keys, read_json
)
from config import NER_MODEL, CONFIDENCE_THRESH

# Load model once at startup — not on every call
print(f"Loading NER model: {NER_MODEL}")
ner = hf_pipeline("ner", model=NER_MODEL, aggregation_strategy="simple")
print("Model loaded ✅")


def extract_entities(text: str) -> list[dict]:
    """Run NER on text, return list of entity dicts with confidence scores."""
    if not text or len(text.strip()) < 10:
        return []
    try:
        results = ner(text[:512])   # BERT max tokens
        return [
            {
                "entity":     r["word"],
                "type":       r["entity_group"],
                "confidence": round(r["score"], 4),
                "flagged":    r["score"] < CONFIDENCE_THRESH,
                "start":      r["start"],
                "end":        r["end"],
            }
            for r in results
        ]
    except Exception as e:
        print(f"  ⚠️  NER error: {e}")
        return []


def process_batch(batch: dict) -> dict:
    """Run NER on all articles in a batch, return entity results."""
    results = []
    all_confidences = []
    flagged_spans = []

    for article in batch.get("articles", []):
        text = f"{article['title']} {article.get('summary', '')}"
        entities = extract_entities(text)

        for ent in entities:
            all_confidences.append(ent["confidence"])
            if ent["flagged"]:
                flagged_spans.append({
                    "span_id":    f"{article['id']}_{ent['start']}",
                    "entity":     ent["entity"],
                    "type":       ent["type"],
                    "confidence": ent["confidence"],
                    "context":    text[:200],
                    "article_id": article["id"],
                    "week":       batch["week"],
                    "status":     "pending_label",
                })

        results.append({
            "article_id": article["id"],
            "title":      article["title"],
            "entities":   entities,
        })

    return {
        "batch_id":        batch["batch_id"],
        "week":            batch["week"],
        "article_count":   len(batch.get("articles", [])),
        "entity_results":  results,
        "all_confidences": all_confidences,
        "flagged_spans":   flagged_spans,
    }


def run():
    week = week_key()
    print(f"\n[NER] Processing week {week}")

    # Find unprocessed batches — raw/ items not yet in entities/
    raw_keys    = set(k.split("/")[-1] for k in list_keys(f"raw/{week}"))
    entity_keys = set(k.split("/")[-1] for k in list_keys(f"entities/{week}"))
    pending     = [k for k in raw_keys if k not in entity_keys]

    if not pending:
        print("  No new batches to process")
        return

    print(f"  {len(pending)} batches to process")

    for key_name in pending:
        batch_id = key_name.replace(".json", "")
        raw_data = read_json(f"raw/{week}/{key_name}")
        print(f"  Processing batch: {batch_id} ({len(raw_data.get('articles', []))} articles)")

        result = process_batch(raw_data)

        # Save entity results
        write_json(f"entities/{week}", batch_id, result)

        # Save drift log
        if result["all_confidences"]:
            append_drift_log(batch_id, result["all_confidences"], week)

        # Save each flagged span to label queue
        for span in result["flagged_spans"]:
            write_json(f"label-queue/{week}", span["span_id"], span)
            print(f"    ⚠️  Flagged: '{span['entity']}' (conf: {span['confidence']})")

        print(f"  ✅ {len(result['entity_results'])} articles processed, "
              f"{len(result['flagged_spans'])} spans flagged")


if __name__ == "__main__":
    run()
