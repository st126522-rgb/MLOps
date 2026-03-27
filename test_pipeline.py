"""
pipeline/tests/test_pipeline.py
=================================
Unit tests — run by GitHub Actions on every push.
These tests do NOT require AWS credentials or internet.
They test the logic of each pipeline component in isolation.
"""

import json
import sys
import os
import pytest

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Patch boto3 before importing anything ──────────────────
import unittest.mock as mock

# Mock s3 client so tests don't need AWS
mock_s3 = mock.MagicMock()
mock_boto3 = mock.MagicMock()
mock_boto3.client.return_value = mock_s3
sys.modules['boto3'] = mock_boto3


# ─────────────────────────────────────────────────────────
# Test: s3_utils helpers
# ─────────────────────────────────────────────────────────

def test_today_key_format():
    from s3_utils import today_key
    key = today_key()
    assert len(key) == 10                  # YYYY-MM-DD
    assert key[4] == "-" and key[7] == "-"


def test_week_key_format():
    from s3_utils import week_key
    key = week_key()
    assert "W" in key                      # YYYY-Www
    assert len(key) == 8


# ─────────────────────────────────────────────────────────
# Test: drift metrics computation
# ─────────────────────────────────────────────────────────

def test_compute_drift_metrics_no_drift():
    from drift import compute_drift_metrics
    history = [
        {"confidence_scores": [0.95, 0.93, 0.91, 0.94], "flagged_count": 0, "total_spans": 4},
        {"confidence_scores": [0.92, 0.90, 0.94, 0.96], "flagged_count": 0, "total_spans": 4},
    ]
    metrics = compute_drift_metrics(history)
    assert metrics["mean_confidence"] > 0.72
    assert metrics["flagged_pct"] == 0.0


def test_compute_drift_metrics_with_drift():
    from drift import compute_drift_metrics
    # Simulate new OOV entities causing low confidence
    history = [
        {"confidence_scores": [0.45, 0.38, 0.91, 0.42, 0.55], "flagged_count": 3, "total_spans": 5},
        {"confidence_scores": [0.40, 0.35, 0.90, 0.38, 0.50], "flagged_count": 4, "total_spans": 5},
    ]
    metrics = compute_drift_metrics(history)
    assert metrics["mean_confidence"] < 0.72
    assert metrics["flagged_pct"] > 0.30


def test_drift_layer1_triggers():
    """Mean confidence below threshold should trigger drift."""
    from drift import compute_drift_metrics
    from config import DRIFT_MEAN_THRESH
    history = [{"confidence_scores": [0.60, 0.65, 0.62], "flagged_count": 3, "total_spans": 3}]
    metrics = compute_drift_metrics(history)
    assert metrics["mean_confidence"] < DRIFT_MEAN_THRESH


def test_drift_layer2_triggers():
    """High flagged percentage should trigger drift."""
    from drift import compute_drift_metrics
    from config import DRIFT_FLAG_PCT
    history = [{"confidence_scores": [0.45, 0.91, 0.40, 0.35, 0.88], "flagged_count": 3, "total_spans": 5}]
    metrics = compute_drift_metrics(history)
    assert metrics["flagged_pct"] > DRIFT_FLAG_PCT


# ─────────────────────────────────────────────────────────
# Test: F1 computation
# ─────────────────────────────────────────────────────────

def test_f1_perfect_match():
    from eval import compute_f1
    preds = [{"text": "OpenAI", "entities": [{"entity": "OpenAI", "type": "ORG"}]}]
    truth = [{"text": "OpenAI", "entities": [{"entity": "OpenAI", "type": "ORG"}]}]
    metrics = compute_f1(preds, truth)
    assert metrics["f1"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_f1_no_match():
    from eval import compute_f1
    preds = [{"text": "OpenAI", "entities": [{"entity": "OpenAI", "type": "ORG"}]}]
    truth = [{"text": "OpenAI", "entities": [{"entity": "GPT-5", "type": "MISC"}]}]
    metrics = compute_f1(preds, truth)
    assert metrics["f1"] == 0.0
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0


def test_f1_partial_match():
    from eval import compute_f1
    preds = [{"text": "x", "entities": [
        {"entity": "OpenAI", "type": "ORG"},
        {"entity": "GPT-5", "type": "MISC"}
    ]}]
    truth = [{"text": "x", "entities": [
        {"entity": "OpenAI", "type": "ORG"},
        {"entity": "Anthropic", "type": "ORG"}   # different entity
    ]}]
    metrics = compute_f1(preds, truth)
    assert 0.0 < metrics["f1"] < 1.0


def test_f1_per_class():
    from eval import compute_f1
    preds = [{"text": "x", "entities": [
        {"entity": "OpenAI", "type": "ORG"},
        {"entity": "GPT-5", "type": "MISC"},
    ]}]
    truth = [{"text": "x", "entities": [
        {"entity": "OpenAI", "type": "ORG"},
        {"entity": "GPT-5", "type": "MISC"},
    ]}]
    metrics = compute_f1(preds, truth)
    assert "ORG" in metrics["per_class"]
    assert "MISC" in metrics["per_class"]
    assert metrics["per_class"]["ORG"] == 1.0


# ─────────────────────────────────────────────────────────
# Test: config values are sensible
# ─────────────────────────────────────────────────────────

def test_config_thresholds_sensible():
    from config import (
        CONFIDENCE_THRESH, DRIFT_MEAN_THRESH, DRIFT_FLAG_PCT,
        DRIFT_WINDOW, MIN_QUEUE_FOR_RETRAIN, F1_IMPROVEMENT_MIN
    )
    assert 0.5 < CONFIDENCE_THRESH < 1.0
    assert 0.5 < DRIFT_MEAN_THRESH < 1.0
    assert 0.0 < DRIFT_FLAG_PCT < 1.0
    assert DRIFT_WINDOW > 0
    assert MIN_QUEUE_FOR_RETRAIN > 0
    assert 0.0 < F1_IMPROVEMENT_MIN < 0.1
