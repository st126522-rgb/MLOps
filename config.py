"""
pipeline/config.py
==================
Single source of truth for S3 paths and thresholds.
Everything reads from environment variables — no hardcoded values.
"""

import os

# ── AWS ────────────────────────────────────────────────────
BUCKET         = os.environ.get("S3_BUCKET", "ai-news-mlops-yourname-2025")
AWS_REGION     = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# ── S3 key prefixes ────────────────────────────────────────
PREFIX_RAW         = "raw"
PREFIX_PROCESSED   = "processed"
PREFIX_ENTITIES    = "entities"
PREFIX_GRAPHS      = "graphs"
PREFIX_DRIFT       = "drift"
PREFIX_LABEL_QUEUE = "label-queue"
PREFIX_LABELED     = "labeled"
PREFIX_MODELS      = "models"
PREFIX_EVAL        = "eval"

# ── NER model ──────────────────────────────────────────────
NER_MODEL          = "dslim/bert-base-NER"
CONFIDENCE_THRESH  = 0.70   # below this → flagged span
DRIFT_MEAN_THRESH  = 0.72   # rolling mean below this → drift event
DRIFT_FLAG_PCT     = 0.30   # if >30% flagged → drift event
DRIFT_WINDOW       = 20     # rolling window size

# ── Retraining ─────────────────────────────────────────────
MIN_QUEUE_FOR_RETRAIN = 15  # minimum labeled spans before retraining
F1_IMPROVEMENT_MIN    = 0.005  # minimum F1 gain to deploy new model

# ── News sources ───────────────────────────────────────────
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+LLM&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=OpenAI+Anthropic+DeepSeek&hl=en&gl=US&ceid=US:en",
    "https://hnrss.org/newest?q=LLM+AI+language+model",
]
