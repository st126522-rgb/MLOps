"""
Helpers for stable article IDs and cross-batch deduplication.
"""

from __future__ import annotations

import hashlib
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from s3_utils import list_keys, read_json


TRACKING_QUERY_PREFIXES = (
    "utm_",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ocid",
    "ref",
    "ref_src",
)


def normalize_url(url: str) -> str:
    """Normalize URLs so equivalent article links map to the same ID."""
    if not url:
        return ""

    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")

    kept_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered.startswith(TRACKING_QUERY_PREFIXES) or lowered in TRACKING_QUERY_PREFIXES:
            continue
        kept_query.append((key, value))
    kept_query.sort()

    return urlunsplit((scheme, netloc, path, urlencode(kept_query), ""))


def stable_article_id(link: str, title: str = "", published: str = "") -> str:
    """Create a stable article ID from the best available identity fields."""
    normalized = normalize_url(link)
    identity = normalized or f"{title.strip().lower()}|{published.strip().lower()}"
    return hashlib.md5(identity.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def deduplicate_articles(
    articles: Iterable[dict],
    existing_ids: set[str] | None = None,
) -> tuple[list[dict], int, int]:
    """Deduplicate articles within the candidate batch and against historical IDs."""
    existing_ids = existing_ids or set()
    batch_seen = set()
    deduped = []
    skipped_batch = 0
    skipped_existing = 0

    for article in articles:
        article_id = article.get("id", "")
        if not article_id:
            continue
        if article_id in batch_seen:
            skipped_batch += 1
            continue
        if article_id in existing_ids:
            skipped_existing += 1
            continue
        batch_seen.add(article_id)
        deduped.append(article)

    return deduped, skipped_batch, skipped_existing


def load_existing_article_ids(prefix: str = "raw") -> set[str]:
    """Read historical raw batches and collect every stored article ID."""
    article_ids: set[str] = set()

    for key in list_keys(prefix):
        if not key.endswith(".json"):
            continue
        try:
            batch = read_json(key)
        except Exception as exc:
            print(f"  [WARN] Could not read {key} while loading dedup index: {exc}")
            continue

        for article in batch.get("articles", []):
            article_id = article.get("id")
            if article_id:
                article_ids.add(article_id)
                continue

            fallback_id = stable_article_id(
                article.get("link", ""),
                article.get("title", ""),
                article.get("published", ""),
            )
            if fallback_id:
                article_ids.add(fallback_id)

    return article_ids
