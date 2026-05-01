"""
Single source of truth for execution mode, storage paths, and thresholds.
"""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOCAL_MODE = os.environ.get("LOCAL_MODE", "true").lower() == "true"
LOCAL_DIR = os.environ.get("LOCAL_DIR", str(BASE_DIR / "local_data"))

BUCKET = os.environ.get("S3_BUCKET", "ai-news-mlops-yourname-2025")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

PREFIX_RAW = "raw"
PREFIX_PROCESSED = "processed"
PREFIX_ENTITIES = "entities"
PREFIX_GRAPHS = "graphs"
PREFIX_DRIFT = "drift"
PREFIX_LABEL_QUEUE = "label-queue"
PREFIX_LABELED = "labeled"
PREFIX_MODELS = "models"
PREFIX_EVAL = "eval"

ENTITY_TYPES = ["ORG", "PER", "LOC", "MODEL", "MISC"]
NER_MODEL = "dslim/bert-base-NER"
LABEL_CONFIDENCE_THRESH = float(os.environ.get("LABEL_CONFIDENCE_THRESH", "0.85"))
DRIFT_LOW_CONFIDENCE_THRESH = float(os.environ.get("DRIFT_LOW_CONFIDENCE_THRESH", "0.70"))
CONFIDENCE_THRESH = LABEL_CONFIDENCE_THRESH
DRIFT_MEAN_THRESH = 0.72
DRIFT_FLAG_PCT = 0.30
DRIFT_WINDOW = 20

MIN_QUEUE_FOR_RETRAIN = 15
F1_IMPROVEMENT_MIN = 0.005

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+LLM&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=OpenAI+Anthropic+DeepSeek&hl=en&gl=US&ceid=US:en",
    "https://hnrss.org/newest?q=LLM",
]
