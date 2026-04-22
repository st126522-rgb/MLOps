"""
Fetch AI news from RSS feeds and save raw articles to local storage or S3.
"""

from datetime import datetime, timezone

import feedparser

from config import RSS_FEEDS
from news_dedup import deduplicate_articles, load_existing_article_ids, stable_article_id
from s3_utils import week_key, write_json


def fetch_articles(existing_ids: set[str] | None = None) -> list[dict]:
    """Fetch articles from all RSS feeds and deduplicate them safely."""
    candidates = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:
                article_id = stable_article_id(
                    entry.get("link", ""),
                    entry.get("title", ""),
                    str(entry.get("published", "")),
                )

                candidates.append(
                    {
                        "id": article_id,
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "link": entry.get("link", ""),
                        "source": feed.feed.get("title", feed_url),
                        "published": str(entry.get("published", "")),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
        except Exception as exc:
            print(f"  Feed error ({feed_url[:40]}...): {exc}")

    articles, skipped_batch, skipped_existing = deduplicate_articles(candidates, existing_ids=existing_ids)
    print(f" Fetched {len(articles)} unique articles")
    if skipped_batch:
        print(f"  Skipped {skipped_batch} duplicates inside this fetch")
    if skipped_existing:
        print(f"  Skipped {skipped_existing} articles already present in raw storage")
    return articles


def run() -> None:
    now = datetime.now(timezone.utc)
    batch_id = now.strftime("%Y-%m-%d_%H")
    week = week_key()

    print(f"\n[INGEST] {now.isoformat()}")
    existing_ids = load_existing_article_ids("raw")
    articles = fetch_articles(existing_ids=existing_ids)

    if not articles:
        print("  No articles fetched - skipping")
        return

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


if __name__ == "__main__":
    run()
