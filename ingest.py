"""
Fetch AI news from RSS feeds and save raw articles to local storage or S3.
"""

import datetime
import hashlib

import feedparser

from config import RSS_FEEDS
from s3_utils import week_key, write_json


def fetch_articles() -> list[dict]:
    """Fetch articles from all RSS feeds and deduplicate by URL hash."""
    seen_hashes = set()
    articles = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:
                url_hash = hashlib.md5(entry.get("link", "").encode(), usedforsecurity=False).hexdigest()[:12]
                if url_hash in seen_hashes:
                    continue
                seen_hashes.add(url_hash)

                articles.append(
                    {
                        "id": url_hash,
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "link": entry.get("link", ""),
                        "source": feed.feed.get("title", feed_url),
                        "published": str(entry.get("published", "")),
                        "fetched_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
        except Exception as exc:
            print(f"  Feed error ({feed_url[:40]}...): {exc}")

    print(f" Fetched {len(articles)} unique articles")
    return articles


def run() -> None:
    now = datetime.datetime.now(datetime.UTC)
    batch_id = now.strftime("%Y-%m-%d_%H")
    week = week_key()

    print(f"\n[INGEST] {now.isoformat()}")
    articles = fetch_articles()

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
