"""
Historical Hacker News backfill into the same raw-batch format used by ingest.py.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

from news_dedup import deduplicate_articles, load_existing_article_ids, stable_article_id
from s3_utils import week_key_for, write_json


ALGOLIA_API = "https://hn.algolia.com/api/v1/search_by_date"
DEFAULT_QUERIES = ["llm", "openai", "anthropic", "deepseek", "gemini"]


def daterange(start: date, end: date) -> list[date]:
    current = start
    days = []
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def fetch_hn_hits(query: str, day: date) -> list[dict]:
    start_ts = int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp())
    page = 0
    hits = []

    while True:
        params = {
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>={start_ts},created_at_i<{end_ts}",
            "hitsPerPage": 100,
            "page": page,
        }
        url = f"{ALGOLIA_API}?{urlencode(params)}"
        with urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        hits.extend(payload.get("hits", []))
        if page >= payload.get("nbPages", 0) - 1:
            break
        page += 1

    return hits


def hit_to_article(hit: dict, query: str, fetched_at: str) -> dict | None:
    link = hit.get("url") or hit.get("story_url") or ""
    title = hit.get("title") or hit.get("story_title") or ""
    published = hit.get("created_at", "")
    if not title and not link:
        return None

    return {
        "id": stable_article_id(link, title, published),
        "title": title,
        "summary": hit.get("story_text", "") or "",
        "link": link,
        "source": f"Hacker News ({query})",
        "published": published,
        "fetched_at": fetched_at,
        "hn_object_id": str(hit.get("objectID", "")),
    }


def backfill(start: date, end: date, queries: list[str]) -> tuple[int, int, int]:
    existing_ids = load_existing_article_ids("raw")
    days_written = 0
    total_saved = 0
    total_skipped = 0

    for day in daterange(start, end):
        fetched_at = datetime.now(timezone.utc).isoformat()
        candidates = []

        for query in queries:
            try:
                for hit in fetch_hn_hits(query, day):
                    article = hit_to_article(hit, query, fetched_at)
                    if article:
                        candidates.append(article)
            except Exception as exc:
                print(f"  [WARN] HN backfill failed for '{query}' on {day.isoformat()}: {exc}")

        articles, skipped_batch, skipped_existing = deduplicate_articles(candidates, existing_ids=existing_ids)
        total_skipped += skipped_batch + skipped_existing

        if not articles:
            print(f"[BACKFILL] {day.isoformat()} -> no new unique articles")
            continue

        for article in articles:
            existing_ids.add(article["id"])

        batch_id = f"{day.isoformat()}_hn"
        week = week_key_for(day)
        write_json(
            f"raw/{week}",
            batch_id,
            {
                "batch_id": batch_id,
                "week": week,
                "article_count": len(articles),
                "articles": articles,
            },
        )
        days_written += 1
        total_saved += len(articles)
        print(
            f"[BACKFILL] {day.isoformat()} -> saved {len(articles)} unique articles "
            f"(skipped {skipped_batch + skipped_existing})"
        )

    return days_written, total_saved, total_skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical Hacker News stories into raw storage.")
    parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD format")
    parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD format")
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Repeatable Hacker News query term. Defaults to a small AI-news query set.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end must be on or after --start")

    queries = args.queries or DEFAULT_QUERIES
    print(f"[BACKFILL] start={start.isoformat()} end={end.isoformat()} queries={queries}")
    days_written, total_saved, total_skipped = backfill(start, end, queries)
    print(
        f"[BACKFILL] complete -> wrote {days_written} day-batches, "
        f"saved {total_saved} articles, skipped {total_skipped} duplicates"
    )


if __name__ == "__main__":
    main()
