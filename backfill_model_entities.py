"""
Backfill MODEL entities into existing local entity outputs.

This lets older entity JSON files benefit from the MODEL post-processor without
rerunning the transformer model.
"""

import argparse

from entity_postprocess import merge_model_entities
from s3_utils import list_keys, read_json, write_json


def raw_batch_lookup() -> dict[str, dict]:
    lookup = {}
    for key in list_keys("raw"):
        batch = read_json(key)
        batch_id = batch.get("batch_id")
        if batch_id:
            lookup[batch_id] = {article.get("id"): article for article in batch.get("articles", [])}
    return lookup


def backfill() -> tuple[int, int]:
    raw_lookup = raw_batch_lookup()
    files_updated = 0
    models_added = 0

    for key in list_keys("entities"):
        batch = read_json(key)
        batch_id = batch.get("batch_id")
        raw_articles = raw_lookup.get(batch_id, {})
        changed = False

        for article_result in batch.get("entity_results", []):
            article_id = article_result.get("article_id")
            raw_article = raw_articles.get(article_id, {})
            text = f"{article_result.get('title', '')} {raw_article.get('summary', '')}"
            before = len(article_result.get("entities", []))
            merged = merge_model_entities(text, article_result.get("entities", []))
            after = len(merged)
            if merged != article_result.get("entities", []):
                article_result["entities"] = merged
                changed = True
                models_added += max(0, after - before)

        if changed:
            parts = key.split("/")
            week = parts[-2]
            filename = parts[-1].replace(".json", "")
            write_json(f"entities/{week}", filename, batch)
            files_updated += 1

    print(f"[OK] Entity files updated: {files_updated}")
    print(f"[OK] MODEL entities added: {models_added}")
    return files_updated, models_added


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill MODEL entities into local entity outputs.")
    parser.parse_args()
    backfill()


if __name__ == "__main__":
    main()
