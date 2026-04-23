"""
Streamlit operator dashboard for the AI news NER MVP.

Run locally or on EC2:
  streamlit run streamlit_dashboard.py --server.port 8501
"""

from __future__ import annotations

import datetime
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import LOCAL_DIR
from dashboard import (
    TYPE_COLORS,
    TYPE_LABELS,
    build_dashboard_payload,
    build_edges,
    build_trend_figure,
    build_type_figure,
    flatten_entities,
    load_batches,
)
from label_review import REVIEW_COLUMNS, stable_split
from s3_utils import write_bytes, write_json


REVIEW_BODY_COLUMNS = [column for column in REVIEW_COLUMNS if column != "span_id"]
STATUS_LABELS = {
    "new": "New in comparison",
    "persistent": "Persistent",
    "historical": "Historical only",
}


st.set_page_config(
    page_title="AI News Operator Console",
    page_icon="news",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink-strong: #eef2ff;
            --ink-soft: #aab6cf;
            --panel-bg: rgba(13, 18, 32, 0.86);
            --panel-border: rgba(71, 85, 105, 0.55);
            --panel-shadow: 0 24px 60px rgba(2, 6, 23, 0.48);
            --accent-blue: #60a5fa;
            --accent-cyan: #2dd4bf;
            --accent-rose: #fb7185;
            --surface-strong: rgba(8, 13, 25, 0.96);
            --surface-soft: rgba(18, 25, 42, 0.86);
            --surface-elevated: rgba(26, 35, 58, 0.9);
        }
        .stApp {
            background:
                radial-gradient(circle at 12% 10%, rgba(96, 165, 250, 0.18), transparent 24rem),
                radial-gradient(circle at 88% 14%, rgba(45, 212, 191, 0.14), transparent 22rem),
                radial-gradient(circle at 52% 100%, rgba(251, 113, 133, 0.10), transparent 28rem),
                linear-gradient(145deg, #040816 0%, #09101d 34%, #0d1627 66%, #070c17 100%);
            color: var(--ink-strong);
        }
        .block-container {
            max-width: 1580px;
            padding-top: 1rem;
            padding-bottom: 2rem;
        }
        .hero {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.94) 0%, rgba(20, 30, 52, 0.92) 100%);
            border: 1px solid rgba(96, 165, 250, 0.16);
            border-radius: 30px;
            padding: 1.6rem 1.7rem;
            box-shadow: 0 26px 80px rgba(2, 6, 23, 0.42);
            backdrop-filter: blur(12px);
            margin-bottom: 1rem;
        }
        .hero h1 {
            margin: 0;
            font-size: 3rem;
            line-height: 0.95;
            letter-spacing: -0.05em;
            color: var(--ink-strong);
        }
        .hero p {
            margin: 0.85rem 0 0 0;
            color: var(--ink-soft);
            font-size: 1rem;
            line-height: 1.6;
            max-width: 980px;
        }
        .metric-card {
            background: linear-gradient(180deg, rgba(18, 25, 42, 0.92) 0%, rgba(12, 18, 32, 0.94) 100%);
            border: 1px solid rgba(71, 85, 105, 0.5);
            border-radius: 22px;
            padding: 1rem 1.1rem;
            box-shadow: 0 14px 34px rgba(2, 6, 23, 0.35);
        }
        .metric-card .label {
            color: #7f8da8;
            text-transform: uppercase;
            letter-spacing: .08em;
            font-size: .76rem;
            font-weight: 700;
        }
        .metric-card .value {
            color: var(--ink-strong);
            font-size: 2rem;
            font-weight: 800;
            margin-top: .25rem;
        }
        .panel-card {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 24px;
            padding: 1rem 1.1rem;
            box-shadow: var(--panel-shadow);
            margin-bottom: 1rem;
        }
        .panel-card h3,
        .panel-card h4,
        .panel-card p,
        .panel-card span,
        .panel-card label {
            color: var(--ink-strong) !important;
        }
        .panel-kicker {
            color: #8da0bf;
            text-transform: uppercase;
            letter-spacing: .08em;
            font-size: .76rem;
            font-weight: 800;
            margin-bottom: .35rem;
        }
        .panel-title {
            color: var(--ink-strong);
            font-size: 1.25rem;
            font-weight: 800;
            margin: 0;
        }
        .panel-body {
            color: var(--ink-soft);
            font-size: .95rem;
            line-height: 1.55;
            margin-top: .45rem;
        }
        .viz-shell {
            background: linear-gradient(180deg, rgba(13, 18, 32, 0.92) 0%, rgba(10, 14, 25, 0.96) 100%);
            border: 1px solid rgba(71, 85, 105, 0.42);
            border-radius: 28px;
            padding: .9rem 1rem .5rem 1rem;
            box-shadow: 0 22px 54px rgba(2, 6, 23, 0.42);
            margin-bottom: 1rem;
        }
        .mini-note {
            color: var(--ink-soft);
            font-size: .88rem;
            line-height: 1.45;
            margin-top: .5rem;
        }
        .topic-card {
            background: linear-gradient(180deg, rgba(20, 28, 47, 0.92) 0%, rgba(13, 19, 35, 0.94) 100%);
            border: 1px solid rgba(71, 85, 105, 0.42);
            border-radius: 18px;
            padding: .85rem 1rem;
            margin-bottom: .6rem;
            box-shadow: 0 10px 26px rgba(2, 6, 23, 0.28);
        }
        .topic-title {
            color: var(--ink-strong);
            font-weight: 800;
        }
        .topic-meta {
            color: #92a4be;
            font-size: .88rem;
            margin-top: .2rem;
        }
        .analysis-note {
            background: rgba(17, 25, 43, 0.84);
            border: 1px solid rgba(71, 85, 105, 0.45);
            border-radius: 18px;
            padding: 0.9rem 1rem;
            color: #9db0c8;
            margin-bottom: 1rem;
        }
        .pill {
            display: inline-block;
            border-radius: 999px;
            padding: .2rem .5rem;
            color: white;
            font-size: .72rem;
            font-weight: 700;
            margin-right: .4rem;
        }
        a, a:visited {
            color: #8ec5ff !important;
        }
        a:hover {
            color: var(--accent-blue) !important;
        }
        .stTabs [data-baseweb="tab"] {
            color: #9eb0c8;
            font-weight: 700;
        }
        .stTabs [aria-selected="true"] {
            color: var(--ink-strong) !important;
        }
        .stMarkdown, .stCaption, .stText, .stMetric, .stMetric label, label, p, span, div {
            color: var(--ink-strong);
        }
        .stButton button, .stDownloadButton button {
            border-radius: 12px;
            border: 1px solid rgba(96, 165, 250, 0.24);
            color: var(--ink-strong);
            background: linear-gradient(180deg, rgba(26, 35, 58, 0.96) 0%, rgba(17, 24, 39, 0.96) 100%);
            font-weight: 700;
        }
        .stButton button:hover, .stDownloadButton button:hover {
            border-color: var(--accent-blue);
            color: #ffffff;
            background: linear-gradient(180deg, rgba(37, 99, 235, 0.92) 0%, rgba(29, 78, 216, 0.92) 100%);
        }
        .stDataFrame a, .stTable a {
            color: #8ec5ff !important;
        }
        .stDataFrame a:hover, .stTable a:hover {
            color: #dbeafe !important;
        }
        [data-testid="stWidgetLabel"] p,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] * {
            color: var(--ink-strong) !important;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(8, 13, 25, 0.98) 0%, rgba(11, 17, 31, 0.98) 100%);
            border-right: 1px solid rgba(71, 85, 105, 0.36);
        }
        [data-testid="stSelectbox"] label,
        [data-testid="stMultiSelect"] label,
        [data-testid="stSlider"] label,
        [data-testid="stToggle"] label,
        [data-testid="stRadio"] label {
            color: var(--ink-strong) !important;
            font-weight: 700 !important;
        }
        [data-baseweb="select"] > div,
        [data-baseweb="select"] input,
        [data-baseweb="tag"] {
            color: #f8fafc !important;
        }
        [data-baseweb="select"] > div {
            background: #151b2d !important;
            border-color: #334155 !important;
        }
        [data-baseweb="popover"],
        [data-baseweb="menu"],
        [role="listbox"] {
            background: #0f172a !important;
            color: #e5eefc !important;
        }
        [role="option"] {
            background: #0f172a !important;
            color: #e5eefc !important;
        }
        [role="option"]:hover {
            background: #1e293b !important;
            color: #ffffff !important;
        }
        [data-baseweb="tag"] {
            background: #ef4444 !important;
            border-radius: 10px !important;
            border: 1px solid rgba(255,255,255,0.15) !important;
        }
        [data-baseweb="tag"] span,
        [data-baseweb="tag"] svg {
            color: #ffffff !important;
            fill: #ffffff !important;
        }
        [data-testid="stMetricValue"],
        [data-testid="stMetricLabel"] {
            color: var(--ink-strong) !important;
        }
        .stMarkdown code, .stCode, code {
            color: #fda4af;
        }
        .streamlit-expanderHeader {
            color: var(--ink-strong) !important;
        }
        .js-plotly-plot .plotly .main-svg {
            border-radius: 18px;
        }
        [data-testid="stDataFrame"] div,
        [data-testid="stDataEditor"] div {
            color: #e5eefc !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: rgba(17, 24, 39, 0.96) !important;
            border: 1px dashed rgba(96, 165, 250, 0.35) !important;
        }
        [data-testid="stFileUploaderDropzone"] * {
            color: #dbeafe !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def pct(value: float) -> str:
    return f"{round(float(value) * 100)}%"


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def mix_colors(color_a: str, color_b: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    rgb_a = hex_to_rgb(color_a)
    rgb_b = hex_to_rgb(color_b)
    mixed = tuple(int(round((1 - ratio) * a + ratio * b)) for a, b in zip(rgb_a, rgb_b))
    return rgb_to_hex(mixed)


def rgba(color: str, alpha: float) -> str:
    red, green, blue = hex_to_rgb(color)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha)):.3f})"


def infer_source_from_title(title: str) -> str:
    if " - " not in title:
        return ""
    return title.rsplit(" - ", 1)[-1].strip()


def ensure_review_df(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS).astype("object")
    clean = df.reindex(columns=REVIEW_COLUMNS).fillna("").astype("object")
    clean["span_id"] = clean["span_id"].astype(str).str.strip()
    clean = clean[clean["span_id"].str.len() > 0]
    return clean


def overlay_review_frames(base_df: pd.DataFrame, incoming_df: pd.DataFrame) -> pd.DataFrame:
    base = ensure_review_df(base_df).set_index("span_id")
    incoming = ensure_review_df(incoming_df).set_index("span_id")
    all_ids = base.index.union(incoming.index)
    base = base.reindex(all_ids, fill_value="")
    incoming = incoming.reindex(all_ids, fill_value="")

    combined = pd.DataFrame(index=all_ids)
    for column in REVIEW_BODY_COLUMNS:
        base_col = base[column].astype(str)
        incoming_col = incoming[column].astype(str)
        combined[column] = incoming_col.where(incoming_col.str.len() > 0, base_col)

    return combined.reset_index().rename(columns={"index": "span_id"}).reindex(columns=REVIEW_COLUMNS).fillna("").astype("object")


def type_pill(entity_type: str) -> str:
    color = TYPE_COLORS.get(entity_type, "#64748B")
    return f"<span class='pill' style='background:{color}'>{entity_type}</span>"


def panel_header(kicker: str, title: str, body: str = "") -> None:
    body_html = f"<div class='panel-body'>{body}</div>" if body else ""
    st.markdown(
        f"""
        <div class="panel-card">
          <div class="panel-kicker">{kicker}</div>
          <div class="panel-title">{title}</div>
          {body_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def load_dashboard_state(base_dir: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    batches = load_batches(base_dir)
    if not batches:
        empty_payload = {"weeks": [], "windows": [], "trends": {"weeks": []}}
        return pd.DataFrame(), pd.DataFrame(columns=["source", "target", "week", "weight"]), empty_payload
    df = flatten_entities(batches)
    edges = build_edges(df)
    payload = build_dashboard_payload(df, edges)
    return df, edges, payload


@st.cache_data(show_spinner=False)
def load_article_metadata(base_dir: str) -> dict[str, dict]:
    metadata: dict[str, dict] = {}

    raw_dir = Path(base_dir) / "raw"
    for json_path in sorted(raw_dir.rglob("*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for article in payload.get("articles", []):
            article_id = str(article.get("id", "")).strip()
            if not article_id:
                continue
            metadata[article_id] = {
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "source": article.get("source", "") or infer_source_from_title(article.get("title", "")),
                "week": payload.get("week", ""),
            }

    entity_dir = Path(base_dir) / "entities"
    for json_path in sorted(entity_dir.rglob("*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for article in payload.get("entity_results", []):
            article_id = str(article.get("article_id", "")).strip()
            if not article_id or article_id in metadata:
                continue
            metadata[article_id] = {
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "source": article.get("source", "") or infer_source_from_title(article.get("title", "")),
                "week": payload.get("week", ""),
            }
    return metadata


def load_queue_records(base_dir: str, article_metadata: dict[str, dict]) -> pd.DataFrame:
    queue_dir = Path(base_dir) / "label-queue"
    rows = []
    for json_path in sorted(queue_dir.rglob("*.json")):
        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        article_id = item.get("article_id", "")
        meta = article_metadata.get(article_id, {})
        article_title = item.get("article_title", "") or meta.get("title", "")
        article_source = item.get("article_source", "") or meta.get("source", "") or infer_source_from_title(article_title)
        rows.append(
            {
                "span_id": item.get("span_id", json_path.stem),
                "status": "pending",
                "split": stable_split(item.get("span_id", json_path.stem)),
                "entity": item.get("entity", ""),
                "type": item.get("type", ""),
                "corrected_entity": "",
                "corrected_type": "",
                "confidence": item.get("confidence", ""),
                "context": item.get("context", ""),
                "article_id": article_id,
                "article_title": article_title,
                "article_source": article_source,
                "article_link": item.get("article_link", "") or meta.get("link", ""),
                "week": item.get("week", "") or meta.get("week", ""),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return ensure_review_df(df)
    return ensure_review_df(df.drop_duplicates(subset=["span_id"]))


def load_review_csv(base_dir: str) -> pd.DataFrame:
    review_path = Path(base_dir) / "review" / "label_review.csv"
    if not review_path.exists():
        return ensure_review_df(None)
    try:
        return ensure_review_df(pd.read_csv(review_path))
    except Exception:
        return ensure_review_df(None)


def save_review_csv(base_dir: str, df: pd.DataFrame) -> Path:
    review_dir = Path(base_dir) / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    output = review_dir / "label_review.csv"
    ensure_review_df(df).to_csv(output, index=False)
    return output


def publish_review_csv(df: pd.DataFrame) -> str:
    csv_bytes = ensure_review_df(df).to_csv(index=False).encode("utf-8")
    return write_bytes("review", "label_review.csv", csv_bytes, content_type="text/csv")


def create_retrain_request(df: pd.DataFrame) -> str:
    review_df = ensure_review_df(df)
    accepted = int(review_df["status"].astype(str).str.lower().eq("accept").sum())
    corrected = int(review_df["status"].astype(str).str.lower().eq("correct").sum())
    rejected = int(review_df["status"].astype(str).str.lower().eq("reject").sum())
    pending = int(review_df["status"].astype(str).str.lower().eq("pending").sum())
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    return write_json(
        "control/retrain_requests",
        f"retrain_request_{timestamp}",
        {
            "requested_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "source": "streamlit_dashboard",
            "review_file": "review/label_review.csv",
            "status_counts": {
                "accept": accepted,
                "correct": corrected,
                "reject": rejected,
                "pending": pending,
            },
            "ready_label_count": accepted + corrected,
        },
    )


def validate_review_rows(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    if df.empty:
        return errors

    valid_types = set(TYPE_LABELS.keys())
    for _, row in df.iterrows():
        span_id = str(row.get("span_id", "")).strip()
        status = str(row.get("status", "")).strip().lower()
        split = str(row.get("split", "")).strip().lower()
        corrected_entity = str(row.get("corrected_entity", "")).strip()
        corrected_type = str(row.get("corrected_type", "")).strip()
        predicted_type = str(row.get("type", "")).strip()

        if status not in {"pending", "accept", "correct", "reject"}:
            errors.append(f"{span_id}: invalid status '{status}'")
        if split not in {"train", "eval"}:
            errors.append(f"{span_id}: invalid split '{split}'")
        if predicted_type and predicted_type not in valid_types:
            errors.append(f"{span_id}: invalid predicted type '{predicted_type}'")
        if status == "correct":
            if not corrected_entity:
                errors.append(f"{span_id}: corrected rows need corrected_entity")
            if corrected_type not in valid_types:
                errors.append(f"{span_id}: corrected rows need a valid corrected_type")
        if status == "accept" and corrected_type and corrected_type not in valid_types:
            errors.append(f"{span_id}: accept row has invalid corrected_type '{corrected_type}'")

    return errors


def merge_queue_and_review(base_dir: str, article_metadata: dict[str, dict]) -> pd.DataFrame:
    queue_df = load_queue_records(base_dir, article_metadata)
    review_df = load_review_csv(base_dir)
    if queue_df.empty:
        return review_df
    if review_df.empty:
        return queue_df
    return overlay_review_frames(queue_df, review_df)


def collect_positions(payload: dict) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for window in payload.get("windows", []):
        for node in window.get("nodes", []):
            positions[node["id"]] = (float(node.get("x", 0.0)), float(node.get("y", 0.0)))
    return positions


def build_window_for_weeks(df: pd.DataFrame, edges: pd.DataFrame, positions: dict[str, tuple[float, float]], selected_weeks: list[str]) -> dict:
    if not selected_weeks:
        return {"nodes": [], "edges": [], "kpis": {}, "hot": [], "weeks": []}

    sub = df[df["week"].isin(selected_weeks)].copy()
    edge_sub = edges[edges["week"].isin(selected_weeks)].copy()
    if sub.empty:
        return {"nodes": [], "edges": [], "kpis": {}, "hot": [], "weeks": selected_weeks}

    grouped = (
        sub.groupby(["entity", "type"])
        .agg(
            mentions=("entity", "count"),
            confidence=("confidence", "mean"),
            flagged=("flagged", "mean"),
            first_week=("week", "min"),
            last_week=("week", "max"),
            articles=("article_id", "nunique"),
        )
        .reset_index()
    )

    entity_names = set(grouped["entity"])
    edge_counts: Counter[tuple[str, str]] = Counter()
    for _, row in edge_sub.iterrows():
        source, target = sorted((row["source"], row["target"]))
        if source in entity_names and target in entity_names:
            edge_counts[(source, target)] += int(row.get("weight", 1))

    nodes = []
    for _, row in grouped.iterrows():
        x_value, y_value = positions.get(row["entity"], (0.0, 0.0))
        nodes.append(
            {
                "id": row["entity"],
                "type": row["type"],
                "mentions": int(row["mentions"]),
                "confidence": round(float(row["confidence"]), 4),
                "flagged": round(float(row["flagged"]), 4),
                "first_week": row["first_week"],
                "last_week": row["last_week"],
                "articles": int(row["articles"]),
                "x": x_value,
                "y": y_value,
            }
        )

    edges_out = [
        {"source": source, "target": target, "weight": weight}
        for (source, target), weight in edge_counts.items()
        if weight > 0
    ]
    hot = sorted(nodes, key=lambda item: (item["mentions"], item["confidence"]), reverse=True)[:15]
    kpis = {
        "weeks": len(selected_weeks),
        "articles": int(sub["article_id"].nunique()),
        "entities": len(nodes),
        "mentions": int(sub.shape[0]),
        "flagged_pct": round(float(sub["flagged"].mean()) if not sub.empty else 0.0, 4),
        "mean_confidence": round(float(sub["confidence"].mean()) if not sub.empty else 0.0, 4),
    }
    return {"nodes": nodes, "edges": edges_out, "kpis": kpis, "hot": hot, "weeks": selected_weeks}


def node_size(mentions: int) -> float:
    return min(58.0, 14.0 + (max(1, mentions) ** 0.5) * 6.8)


def edge_width(weight: int, highlighted: bool = False) -> float:
    base = 0.9 + min(7.0, (max(1, weight) ** 0.72) * 0.85)
    return base + (1.25 if highlighted else 0.0)


def smart_label_set(nodes: list[dict], labels_on: bool, focus_entity: str, focus_neighbors: set[str]) -> set[str]:
    if not labels_on or not nodes:
        return set()

    if focus_entity:
        top_support = {
            node["id"]
            for node in sorted(nodes, key=lambda item: (item["mentions"], item["confidence"]), reverse=True)[:8]
        }
        return ({focus_entity} | set(focus_neighbors) | top_support) & {node["id"] for node in nodes}

    top_nodes = sorted(nodes, key=lambda item: (item["mentions"], item["confidence"]), reverse=True)[:12]
    return {node["id"] for node in top_nodes}


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0.0, min(float(len(ordered) - 1), fraction * (len(ordered) - 1)))
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = index - lower
    return ordered[lower] * (1 - remainder) + ordered[upper] * remainder


def compute_view_ranges(nodes: list[dict], focus_entity: str = "") -> tuple[list[float] | None, list[float] | None]:
    if not nodes:
        return None, None

    xs = [float(node["x"]) for node in nodes]
    ys = [float(node["y"]) for node in nodes]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    if len(nodes) > 8:
        core_x_min = percentile(xs, 0.08)
        core_x_max = percentile(xs, 0.92)
        core_y_min = percentile(ys, 0.08)
        core_y_max = percentile(ys, 0.92)

        full_x_span = max(0.001, x_max - x_min)
        full_y_span = max(0.001, y_max - y_min)
        core_x_span = max(0.001, core_x_max - core_x_min)
        core_y_span = max(0.001, core_y_max - core_y_min)

        if full_x_span > core_x_span * 1.9:
            x_min, x_max = core_x_min, core_x_max
        if full_y_span > core_y_span * 1.9:
            y_min, y_max = core_y_min, core_y_max

    if focus_entity:
        x_pad = max(0.18, (x_max - x_min) * 0.18)
        y_pad = max(0.18, (y_max - y_min) * 0.18)
    else:
        x_pad = max(0.22, (x_max - x_min) * 0.12)
        y_pad = max(0.22, (y_max - y_min) * 0.12)

    return [x_min - x_pad, x_max + x_pad], [y_min - y_pad, y_max + y_pad]


def build_overlay_graph_figure(
    baseline_window: dict,
    comparison_window: dict,
    active_types: list[str],
    min_mentions: int,
    min_edge: int,
    labels_on: bool,
    show_new_only: bool,
    show_historical_edges: bool,
    show_dropped_nodes: bool,
    show_isolated_nodes: bool,
    node_cap: int,
    first_seen_start: str,
    first_seen_end: str,
    week_order: dict[str, int],
    focus_entity: str,
    focus_neighbors_only: bool,
) -> tuple[go.Figure, dict]:
    baseline_nodes = {node["id"]: node for node in baseline_window.get("nodes", [])}
    comparison_nodes = {node["id"]: node for node in comparison_window.get("nodes", [])}

    combined_nodes = {}
    all_node_ids = baseline_nodes.keys() | comparison_nodes.keys()
    for node_id in all_node_ids:
        current = comparison_nodes.get(node_id)
        previous = baseline_nodes.get(node_id)
        source = current or previous
        if not source:
            continue
        status = "persistent"
        if current and not previous:
            status = "new"
        elif previous and not current:
            status = "historical"

        mentions = int((current or previous)["mentions"])
        if source["type"] not in active_types:
            continue
        if mentions < min_mentions:
            continue
        first_seen = source["first_week"]
        if week_order[first_seen] < week_order[first_seen_start] or week_order[first_seen] > week_order[first_seen_end]:
            continue
        if show_new_only and status != "new":
            continue
        if status == "historical" and not show_dropped_nodes:
            continue

        combined_nodes[node_id] = {
            "id": node_id,
            "type": source["type"],
            "status": status,
            "mentions": mentions,
            "confidence": float((current or previous)["confidence"]),
            "articles": int((current or previous)["articles"]),
            "flagged": float((current or previous)["flagged"]),
            "first_week": source["first_week"],
            "last_week": source["last_week"],
            "x": float(source.get("x", 0.0)),
            "y": float(source.get("y", 0.0)),
            "delta": int(current["mentions"] if current else 0) - int(previous["mentions"] if previous else 0),
        }

    baseline_edges = {
        tuple(sorted((edge["source"], edge["target"]))): int(edge["weight"])
        for edge in baseline_window.get("edges", [])
    }
    comparison_edges = {
        tuple(sorted((edge["source"], edge["target"]))): int(edge["weight"])
        for edge in comparison_window.get("edges", [])
    }

    adjacency: dict[str, set[str]] = defaultdict(set)
    for source, target in baseline_edges.keys() | comparison_edges.keys():
        adjacency[source].add(target)
        adjacency[target].add(source)

    if node_cap > 0 and len(combined_nodes) > node_cap:
        ranked_ids = [
            item["id"]
            for item in sorted(
                combined_nodes.values(),
                key=lambda item: (
                    item["status"] == "new",
                    item["delta"],
                    item["mentions"],
                    item["confidence"],
                ),
                reverse=True,
            )[:node_cap]
        ]
        combined_nodes = {node_id: combined_nodes[node_id] for node_id in ranked_ids}

    active_node_ids = set(combined_nodes)
    edge_union = baseline_edges.keys() | comparison_edges.keys()

    if not show_isolated_nodes:
        connected_ids = set()
        for source, target in edge_union:
            if source in active_node_ids and target in active_node_ids:
                connected_ids.add(source)
                connected_ids.add(target)
        combined_nodes = {node_id: node for node_id, node in combined_nodes.items() if node_id in connected_ids}
        active_node_ids = set(combined_nodes)

    focus_neighbors = set()
    if focus_entity and focus_entity in active_node_ids:
        focus_neighbors = adjacency.get(focus_entity, set()) & active_node_ids
        if focus_neighbors_only:
            keep_ids = {focus_entity} | focus_neighbors
            combined_nodes = {node_id: node for node_id, node in combined_nodes.items() if node_id in keep_ids}
            active_node_ids = set(combined_nodes)

    legend = {
        "Node size": "Mention volume in the selected comparison view",
        "Node color": "Entity type",
        "Status colors": "Bright = new, muted = persistent, gray = historical only",
        "Edges": "Article co-mentions between two entities",
    }

    edge_buckets: dict[tuple[str, bool, float], dict[str, list]] = defaultdict(lambda: {"x": [], "y": [], "text": []})
    for edge_key in edge_union:
        source, target = edge_key
        if source not in active_node_ids or target not in active_node_ids:
            continue

        baseline_weight = baseline_edges.get(edge_key, 0)
        comparison_weight = comparison_edges.get(edge_key, 0)
        if max(baseline_weight, comparison_weight) < min_edge:
            continue

        status = "current"
        if baseline_weight and comparison_weight:
            status = "persistent"
        elif baseline_weight and not comparison_weight:
            status = "historical"

        if status == "historical" and not show_historical_edges:
            continue

        highlighted = False
        if focus_entity:
            highlighted = source == focus_entity or target == focus_entity or (source in focus_neighbors and target == focus_entity) or (target in focus_neighbors and source == focus_entity)
            if not highlighted and source not in focus_neighbors and target not in focus_neighbors:
                status = f"{status}_dim"

        width = round(edge_width(max(baseline_weight, comparison_weight), highlighted=highlighted), 1)
        bucket = edge_buckets[(status, highlighted, width)]
        source_node = combined_nodes[source]
        target_node = combined_nodes[target]
        bucket["x"] += [source_node["x"], target_node["x"], None]
        bucket["y"] += [source_node["y"], target_node["y"], None]
        bucket["text"] += [
            (
                f"{source} <-> {target}<br>"
                f"Current weight: {comparison_weight}<br>"
                f"Historical weight: {baseline_weight}"
            )
        ] * 3

    fig = go.Figure()
    edge_styles = {
        "current": ("rgba(37,99,235,0.32)", 2.2),
        "persistent": ("rgba(15,118,110,0.30)", 1.8),
        "historical": ("rgba(148,163,184,0.22)", 1.2),
        "current_dim": ("rgba(148,163,184,0.10)", 1.0),
        "persistent_dim": ("rgba(148,163,184,0.10)", 1.0),
        "historical_dim": ("rgba(203,213,225,0.08)", 0.8),
    }
    edge_names = {
        "current": "Current-only edges",
        "persistent": "Persistent edges",
        "historical": "Historical-only edges",
        "current_dim": "Dimmed current edges",
        "persistent_dim": "Dimmed persistent edges",
        "historical_dim": "Dimmed historical edges",
    }
    for (status, _highlighted, dynamic_width), points in edge_buckets.items():
        color, _base_width = edge_styles[status]
        if not points["x"]:
            continue
        fig.add_trace(
            go.Scatter(
                x=points["x"],
                y=points["y"],
                text=points["text"],
                mode="lines",
                line=dict(color=color, width=dynamic_width),
                hovertemplate="%{text}<extra>Connection</extra>",
                name=edge_names[status],
                showlegend=status in {"current", "persistent", "historical"},
            )
        )

    status_groups = {"new": [], "persistent": [], "historical": []}
    for node in combined_nodes.values():
        status_groups[node["status"]].append(node)

    all_nodes = list(combined_nodes.values())
    label_nodes = smart_label_set(all_nodes, labels_on=labels_on, focus_entity=focus_entity, focus_neighbors=focus_neighbors)

    for status, nodes in status_groups.items():
        if not nodes:
            continue
        marker_colors = []
        marker_lines = []
        marker_sizes = []
        marker_symbols = []
        marker_line_widths = []
        text_labels = []
        customdata = []
        for node in nodes:
            base_color = TYPE_COLORS.get(node["type"], "#64748B")
            if status == "new":
                color = base_color
                line_color = "#fff7ed"
            elif status == "persistent":
                color = mix_colors(base_color, "#CBD5E1", 0.35)
                line_color = "#ffffff"
            else:
                color = "#CBD5E1"
                line_color = "#94A3B8"

            alpha = 0.95
            if focus_entity and node["id"] != focus_entity and node["id"] not in focus_neighbors:
                alpha = 0.18
            if focus_entity and node["id"] == focus_entity:
                color = "#F59E0B"
                line_color = "#0F172A"

            marker_colors.append(rgba(color, alpha))
            marker_lines.append(line_color)
            marker_sizes.append(node_size(node["mentions"]) * (1.35 if node["id"] == focus_entity else 1.0))
            marker_symbols.append("diamond" if node["id"] == focus_entity else "circle")
            marker_line_widths.append(3.6 if node["id"] == focus_entity else 2.0)
            text_labels.append(node["id"] if node["id"] in label_nodes else "")
            customdata.append(
                [
                    node["type"],
                    STATUS_LABELS[node["status"]],
                    node["mentions"],
                    node["confidence"],
                    node["articles"],
                    node["first_week"],
                    node["last_week"],
                    node["delta"],
                    node["flagged"],
                ]
            )

        fig.add_trace(
            go.Scatter(
                x=[node["x"] for node in nodes],
                y=[node["y"] for node in nodes],
                text=text_labels,
                customdata=customdata,
                mode="markers+text" if labels_on else "markers",
                textposition="top center",
                textfont=dict(size=12, color="#102033"),
                marker=dict(
                    color=marker_colors,
                    size=marker_sizes,
                    symbol=marker_symbols,
                    line=dict(color=marker_lines, width=marker_line_widths),
                ),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Type: %{customdata[0]}<br>"
                    "Status: %{customdata[1]}<br>"
                    "Mentions: %{customdata[2]}<br>"
                    "Confidence: %{customdata[3]:.2f}<br>"
                    "Articles: %{customdata[4]}<br>"
                    "First seen: %{customdata[5]}<br>"
                    "Last seen: %{customdata[6]}<br>"
                    "Mention delta: %{customdata[7]}<br>"
                    "Flagged ratio: %{customdata[8]:.0%}<extra></extra>"
                ),
                name=STATUS_LABELS[status],
            )
        )

    x_range, y_range = compute_view_ranges(list(combined_nodes.values()), focus_entity=focus_entity)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FCFDFE",
        margin=dict(l=8, r=8, t=18, b=8),
        height=780,
        xaxis=dict(visible=False, range=x_range),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1, range=y_range),
        legend=dict(orientation="h", y=1.08, x=0, bgcolor="rgba(255,255,255,0.75)"),
        dragmode="pan",
        hoverlabel=dict(bgcolor="#0F172A", font_color="white"),
    )

    summary = {
        "new_count": sum(1 for node in combined_nodes.values() if node["status"] == "new"),
        "persistent_count": sum(1 for node in combined_nodes.values() if node["status"] == "persistent"),
        "historical_count": sum(1 for node in combined_nodes.values() if node["status"] == "historical"),
        "focus_neighbors": len(focus_neighbors),
        "legend": legend,
        "nodes": combined_nodes,
        "adjacency": adjacency,
    }
    return fig, summary


def build_single_window_figure(
    window_data: dict,
    active_types: list[str],
    min_mentions: int,
    min_edge: int,
    labels_on: bool,
    node_cap: int,
    first_seen_start: str,
    first_seen_end: str,
    week_order: dict[str, int],
    focus_entity: str,
    focus_neighbors_only: bool,
) -> go.Figure:
    nodes = {}
    for node in window_data.get("nodes", []):
        if node["type"] not in active_types:
            continue
        if int(node["mentions"]) < min_mentions:
            continue
        if week_order[node["first_week"]] < week_order[first_seen_start] or week_order[node["first_week"]] > week_order[first_seen_end]:
            continue
        nodes[node["id"]] = dict(node)

    if node_cap > 0 and len(nodes) > node_cap:
        keep_ids = {
            item["id"]
            for item in sorted(nodes.values(), key=lambda item: (item["mentions"], item["confidence"]), reverse=True)[:node_cap]
        }
        nodes = {node_id: node for node_id, node in nodes.items() if node_id in keep_ids}

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in window_data.get("edges", []):
        if edge["source"] in nodes and edge["target"] in nodes:
            adjacency[edge["source"]].add(edge["target"])
            adjacency[edge["target"]].add(edge["source"])

    focus_neighbors = set()
    if focus_entity and focus_entity in nodes:
        focus_neighbors = adjacency.get(focus_entity, set()) & set(nodes)
        if focus_neighbors_only:
            keep_ids = {focus_entity} | focus_neighbors
            nodes = {node_id: node for node_id, node in nodes.items() if node_id in keep_ids}

    edge_buckets: dict[float, dict[str, list]] = defaultdict(lambda: {"x": [], "y": [], "text": []})
    for edge in window_data.get("edges", []):
        if edge["source"] not in nodes or edge["target"] not in nodes:
            continue
        if int(edge["weight"]) < min_edge:
            continue
        source = nodes[edge["source"]]
        target = nodes[edge["target"]]
        color = "rgba(37,99,235,0.26)"
        width = edge_width(int(edge["weight"]), highlighted=edge["source"] == focus_entity or edge["target"] == focus_entity)
        if focus_entity and edge["source"] != focus_entity and edge["target"] != focus_entity:
            if edge["source"] not in focus_neighbors and edge["target"] not in focus_neighbors:
                color = "rgba(148,163,184,0.10)"
                width = 0.9
        bucket = edge_buckets[round(width, 1)]
        bucket["x"] += [source["x"], target["x"], None]
        bucket["y"] += [source["y"], target["y"], None]
        bucket["text"] += [f"{edge['source']} <-> {edge['target']}<br>{edge['weight']} shared article co-mention(s)"] * 3

    fig = go.Figure()
    for width, bucket in edge_buckets.items():
        if not bucket["x"]:
            continue
        fig.add_trace(
            go.Scatter(
                x=bucket["x"],
                y=bucket["y"],
                text=bucket["text"],
                mode="lines",
                line=dict(color="rgba(83,102,128,0.24)", width=width),
                hovertemplate="%{text}<extra>Connection</extra>",
                name="Connections",
                showlegend=width == max(edge_buckets),
            )
        )

    label_nodes = smart_label_set(list(nodes.values()), labels_on=labels_on, focus_entity=focus_entity, focus_neighbors=focus_neighbors)
    for entity_type in active_types:
        bucket = [node for node in nodes.values() if node["type"] == entity_type]
        if not bucket:
            continue
        marker_colors = []
        line_colors = []
        marker_sizes = []
        marker_symbols = []
        text_labels = []
        for node in bucket:
            alpha = 0.92
            if focus_entity and node["id"] != focus_entity and node["id"] not in focus_neighbors:
                alpha = 0.18
            base_color = TYPE_COLORS.get(entity_type, "#64748B")
            if node["id"] == focus_entity:
                base_color = "#F59E0B"
            marker_colors.append(rgba(base_color, alpha))
            line_colors.append("#0F172A" if node["id"] == focus_entity else "#ffffff")
            marker_sizes.append(node_size(int(node["mentions"])) * (1.35 if node["id"] == focus_entity else 1.0))
            marker_symbols.append("diamond" if node["id"] == focus_entity else "circle")
            text_labels.append(node["id"] if node["id"] in label_nodes else "")

        fig.add_trace(
            go.Scatter(
                x=[node["x"] for node in bucket],
                y=[node["y"] for node in bucket],
                text=text_labels,
                customdata=[
                    [
                        node["type"],
                        node["mentions"],
                        node["confidence"],
                        node["articles"],
                        node["first_week"],
                        node["flagged"],
                    ]
                    for node in bucket
                ],
                mode="markers+text" if labels_on else "markers",
                textposition="top center",
                textfont=dict(size=11, color="#172033"),
                marker=dict(
                    color=marker_colors,
                    size=marker_sizes,
                    symbol=marker_symbols,
                    line=dict(color=line_colors, width=2.0),
                ),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Type: %{customdata[0]}<br>"
                    "Mentions: %{customdata[1]}<br>"
                    "Confidence: %{customdata[2]:.2f}<br>"
                    "Articles: %{customdata[3]}<br>"
                    "First seen: %{customdata[4]}<br>"
                    "Flagged ratio: %{customdata[5]:.0%}<extra></extra>"
                ),
                name=entity_type,
            )
        )

    x_range, y_range = compute_view_ranges(list(nodes.values()), focus_entity=focus_entity)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FBFCFE",
        margin=dict(l=6, r=6, t=18, b=6),
        height=700,
        xaxis=dict(visible=False, range=x_range),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1, range=y_range),
        legend=dict(orientation="h", y=1.08, x=0),
        dragmode="pan",
        hoverlabel=dict(bgcolor="#0F172A", font_color="white"),
    )
    return fig


def build_focus_panel(df: pd.DataFrame, baseline_weeks: list[str], comparison_weeks: list[str], focus_entity: str) -> None:
    if not focus_entity:
        st.info("Pick a focus entity to inspect its trajectory, linked entities, and evidence articles.")
        return

    baseline_df = df[(df["week"].isin(baseline_weeks)) & (df["entity"] == focus_entity)]
    comparison_df = df[(df["week"].isin(comparison_weeks)) & (df["entity"] == focus_entity)]
    if baseline_df.empty and comparison_df.empty:
        st.warning("That entity is not present in the selected windows.")
        return

    focus_cols = st.columns(4)
    baseline_mentions = int(baseline_df.shape[0])
    comparison_mentions = int(comparison_df.shape[0])
    baseline_articles = int(baseline_df["article_id"].nunique()) if not baseline_df.empty else 0
    comparison_articles = int(comparison_df["article_id"].nunique()) if not comparison_df.empty else 0
    confidence = float(comparison_df["confidence"].mean()) if not comparison_df.empty else float(baseline_df["confidence"].mean())
    focus_cols[0].metric("Baseline mentions", baseline_mentions)
    focus_cols[1].metric("Comparison mentions", comparison_mentions, delta=comparison_mentions - baseline_mentions)
    focus_cols[2].metric("Comparison articles", comparison_articles, delta=comparison_articles - baseline_articles)
    focus_cols[3].metric("Mean confidence", pct(confidence))

    related = (
        df[df["week"].isin(comparison_weeks) & (df["article_id"].isin(comparison_df["article_id"].unique())) & (df["entity"] != focus_entity)]
        .groupby(["entity", "type"])
        .agg(mentions=("entity", "count"), articles=("article_id", "nunique"))
        .reset_index()
        .sort_values(["articles", "mentions"], ascending=False)
    )
    article_rows = (
        comparison_df[["article", "article_source", "article_link", "week", "article_id"]]
        .drop_duplicates()
        .sort_values(["week", "article"], ascending=[False, True])
    )

    related_cols = st.columns([0.95, 1.05])
    with related_cols[0]:
        st.markdown(f"**{focus_entity}: strongest linked entities**")
        if related.empty:
            st.caption("No linked entities in the selected comparison window.")
        else:
            st.dataframe(related.head(12), use_container_width=True, hide_index=True)
    with related_cols[1]:
        st.markdown(f"**Evidence articles for {focus_entity}**")
        if article_rows.empty:
            st.caption("No supporting articles in the selected comparison window.")
        else:
            st.dataframe(
                article_rows.rename(columns={"article": "title", "article_source": "source", "article_link": "link"}),
                use_container_width=True,
                hide_index=True,
                column_config={"link": st.column_config.LinkColumn("link", display_text="open")},
            )


def render_overview_tab(df: pd.DataFrame, payload: dict) -> None:
    if df.empty:
        st.warning("No entity data found yet. Run the ingestion and NER pipeline first.")
        return

    latest_window = payload["windows"][-1]
    kpis = latest_window["kpis"]

    cols = st.columns(6)
    items = [
        ("Entities", f"{kpis['entities']:,}"),
        ("Mentions", f"{kpis['mentions']:,}"),
        ("Articles", f"{kpis['articles']:,}"),
        ("Weeks", f"{kpis['weeks']:,}"),
        ("Confidence", pct(kpis["mean_confidence"])),
        ("Flagged", pct(kpis["flagged_pct"])),
    ]
    for col, (label, value) in zip(cols, items):
        col.markdown(
            f"<div class='metric-card'><div class='label'>{label}</div><div class='value'>{value}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="analysis-note">
          Read this dashboard as an entity radar. Node size tracks how often an entity is mentioned, color tracks entity type,
          and edge weight tracks how often two entities appear together in the same article.
        </div>
        """,
        unsafe_allow_html=True,
    )

    chart_cols = st.columns([1.15, 0.85])
    with chart_cols[0]:
        st.plotly_chart(
            build_trend_figure(payload["trends"]),
            use_container_width=True,
            config={"displaylogo": False},
            key="overview_trend_chart",
        )
    with chart_cols[1]:
        st.plotly_chart(
            build_type_figure(df),
            use_container_width=True,
            config={"displaylogo": False},
            key="overview_type_mix_chart",
        )

    st.subheader("Hot Topics")
    topic_cols = st.columns(3)
    for index, item in enumerate(latest_window["hot"][:12]):
        topic_cols[index % 3].markdown(
            f"""
            <div class="topic-card">
              <div class="topic-title">{item['id']}</div>
              <div class="topic-meta">{type_pill(item['type'])} {item['mentions']} mentions across {item['articles']} articles</div>
              <div class="topic-meta">Confidence {pct(item['confidence'])} - Flagged {pct(item['flagged'])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_graph_tab(df: pd.DataFrame, edges: pd.DataFrame, payload: dict) -> None:
    weeks = payload.get("weeks", [])
    if not weeks:
        st.warning("No weekly windows found yet.")
        return

    positions = collect_positions(payload)
    week_order = {week: index for index, week in enumerate(weeks)}

    main_col, control_col = st.columns([7, 3], gap="large")

    with control_col:
        panel_header(
            "Control rail",
            "Tune the graph",
            "Use the right rail to filter noise, isolate neighborhoods, and compare earlier versus later entity structure.",
        )

        baseline_week = st.selectbox("Baseline week", weeks, index=0)
        comparison_week = st.selectbox("Comparison week", weeks, index=len(weeks) - 1)
        graph_mode = st.radio("Graph view", ["Overlay compare", "Side-by-side"], horizontal=False)
        labels_on = st.toggle("Display labels", value=True)

    if week_order[baseline_week] > week_order[comparison_week]:
        st.warning("Baseline week cannot be later than comparison week. I am using the comparison week for both views.")
        baseline_week = comparison_week

    baseline_weeks = weeks[: week_order[baseline_week] + 1]
    comparison_weeks = weeks[: week_order[comparison_week] + 1]

    baseline_window = build_window_for_weeks(df, edges, positions, baseline_weeks)
    comparison_window = build_window_for_weeks(df, edges, positions, comparison_weeks)

    max_mentions = max(1, int(df.groupby("entity").size().max())) if not df.empty else 1
    max_edge = max(1, int(edges["weight"].max())) if not edges.empty else 1

    with control_col:
        active_types = st.multiselect("Entity types", list(TYPE_COLORS.keys()), default=list(TYPE_COLORS.keys()))
        if len(weeks) <= 1:
            st.caption("First seen week range")
            st.info(f"Fixed at {weeks[0]}")
            first_seen_range = (weeks[0], weeks[0])
        else:
            first_seen_range = st.select_slider("First seen week range", options=weeks, value=(weeks[0], weeks[-1]))
        if max_mentions <= 1:
            st.caption("Minimum node size")
            st.info("Fixed at 1 for current data")
            min_mentions = 1
        else:
            min_mentions = int(st.slider("Minimum node size", min_value=1, max_value=max_mentions, value=1))

        if max_edge <= 1:
            st.caption("Minimum edge strength")
            st.info("Fixed at 1 for current data")
            min_edge = 1
        else:
            min_edge = int(st.slider("Minimum edge strength", min_value=1, max_value=max_edge, value=1))

        max_node_cap = max(20, min(240, len(comparison_window["nodes"]) or 20))
        default_node_cap = min(120, max_node_cap)
        if max_node_cap <= 20:
            st.caption("Node cap")
            st.info("Fixed at 20 for current data")
            node_cap = 20
        else:
            node_cap = int(st.slider("Node cap", min_value=20, max_value=max_node_cap, value=default_node_cap))
        show_new_only = st.toggle("Only new nodes", value=False)
        show_dropped_nodes = st.toggle("Show historical-only nodes", value=True)
        show_historical_edges = st.toggle("Show historical-only edges", value=True)
        show_isolated_nodes = st.toggle("Show isolated nodes", value=False)
        focus_neighbors_only = st.toggle("Focus mode hides unrelated nodes", value=False)

    focus_candidates = sorted(
        {
            node["id"]
            for node in comparison_window.get("nodes", [])
            if node["type"] in active_types
        }
        | {
            node["id"]
            for node in baseline_window.get("nodes", [])
            if node["type"] in active_types
        }
    )
    with control_col:
        focus_entity = st.selectbox("Focus entity", [""] + focus_candidates, index=0, help="Use this to spotlight one node and its immediate neighborhood.")
        st.markdown(
            """
            <div class="mini-note">
              Overlay mode is the primary analysis view. Bright nodes are new in the comparison window, muted colored nodes persisted from the baseline,
              and gray nodes are historical-only.
            </div>
            """,
            unsafe_allow_html=True,
        )

    with main_col:
        st.markdown("<div class='viz-shell'>", unsafe_allow_html=True)
        if graph_mode == "Overlay compare":
            figure, summary = build_overlay_graph_figure(
                baseline_window=baseline_window,
                comparison_window=comparison_window,
                active_types=active_types,
                min_mentions=min_mentions,
                min_edge=min_edge,
                labels_on=labels_on,
                show_new_only=show_new_only,
                show_historical_edges=show_historical_edges,
                show_dropped_nodes=show_dropped_nodes,
                show_isolated_nodes=show_isolated_nodes,
                node_cap=node_cap,
                first_seen_start=first_seen_range[0],
                first_seen_end=first_seen_range[1],
                week_order=week_order,
                focus_entity=focus_entity,
                focus_neighbors_only=focus_neighbors_only,
            )
            st.plotly_chart(
                figure,
                use_container_width=True,
                config={"displaylogo": False, "scrollZoom": True},
                key=f"overlay_graph_{baseline_week}_{comparison_week}_{min_mentions}_{min_edge}_{labels_on}_{focus_entity}_{show_new_only}",
            )

            summary_cols = st.columns(4)
            summary_cols[0].metric("New nodes", summary["new_count"])
            summary_cols[1].metric("Persistent nodes", summary["persistent_count"])
            summary_cols[2].metric("Historical-only nodes", summary["historical_count"])
            summary_cols[3].metric("Focused neighbors", summary["focus_neighbors"])

            with st.expander("Graph legend and reading guide", expanded=False):
                for key, value in summary["legend"].items():
                    st.markdown(f"- **{key}**: {value}")

        else:
            graph_cols = st.columns(2)
            with graph_cols[0]:
                st.caption(f"Up to {baseline_week}")
                st.plotly_chart(
                    build_single_window_figure(
                        baseline_window,
                        active_types=active_types,
                        min_mentions=min_mentions,
                        min_edge=min_edge,
                        labels_on=labels_on,
                        node_cap=node_cap,
                        first_seen_start=first_seen_range[0],
                        first_seen_end=first_seen_range[1],
                        week_order=week_order,
                        focus_entity=focus_entity,
                        focus_neighbors_only=focus_neighbors_only,
                    ),
                    use_container_width=True,
                    config={"displaylogo": False, "scrollZoom": True},
                    key=f"network_left_{baseline_week}_{min_mentions}_{min_edge}_{labels_on}_{focus_entity}",
                )
            with graph_cols[1]:
                st.caption(f"Up to {comparison_week}")
                st.plotly_chart(
                    build_single_window_figure(
                        comparison_window,
                        active_types=active_types,
                        min_mentions=min_mentions,
                        min_edge=min_edge,
                        labels_on=labels_on,
                        node_cap=node_cap,
                        first_seen_start=first_seen_range[0],
                        first_seen_end=first_seen_range[1],
                        week_order=week_order,
                        focus_entity=focus_entity,
                        focus_neighbors_only=focus_neighbors_only,
                    ),
                    use_container_width=True,
                    config={"displaylogo": False, "scrollZoom": True},
                    key=f"network_right_{comparison_week}_{min_mentions}_{min_edge}_{labels_on}_{focus_entity}",
                )
        st.markdown("</div>", unsafe_allow_html=True)

    left_nodes = {node["id"]: node for node in baseline_window["nodes"]}
    right_nodes = {node["id"]: node for node in comparison_window["nodes"]}
    new_items = [right_nodes[key] for key in right_nodes.keys() - left_nodes.keys()]
    dropped_items = [left_nodes[key] for key in left_nodes.keys() - right_nodes.keys()]
    risers = []
    for key in right_nodes.keys() & left_nodes.keys():
        delta = right_nodes[key]["mentions"] - left_nodes[key]["mentions"]
        if delta > 0:
            item = dict(right_nodes[key])
            item["delta"] = delta
            risers.append(item)

    with main_col:
        st.subheader("Comparison readout")
        delta_cols = st.columns(3)
        sections = [
            ("New in comparison", sorted(new_items, key=lambda item: item["mentions"], reverse=True)[:10], lambda item: ""),
            ("Biggest risers", sorted(risers, key=lambda item: item["delta"], reverse=True)[:10], lambda item: f" - +{item['delta']}"),
            ("Dropped since baseline", sorted(dropped_items, key=lambda item: item["mentions"], reverse=True)[:10], lambda item: ""),
        ]
        for col, (title, items, extra) in zip(delta_cols, sections):
            with col:
                st.markdown(f"**{title}**")
                if not items:
                    st.caption("No items.")
                for item in items:
                    st.markdown(
                        f"""
                        <div class="topic-card">
                          <div class="topic-title">{item['id']}</div>
                          <div class="topic-meta">{type_pill(item['type'])} {item['mentions']} mentions{extra(item)}</div>
                          <div class="topic-meta">Confidence {pct(item['confidence'])}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

        st.subheader("Focus analysis")
        build_focus_panel(df, baseline_weeks, comparison_weeks, focus_entity)


def render_review_tab(base_dir: str, article_metadata: dict[str, dict]) -> None:
    df = merge_queue_and_review(base_dir, article_metadata)
    if df.empty:
        st.info("No pending label-queue items found in local_data/label-queue.")
        return

    st.markdown(
        """
        <div class="analysis-note">
          Review queue workflow:
          1. Edit rows in the table and save them back to the review CSV, or
          2. Download the full CSV, edit it offline, upload it back here, then save.
          Save review CSV writes the working file the retrain script will consume. Download filtered CSV is only a convenience export of the current filtered view.
        </div>
        """,
        unsafe_allow_html=True,
    )

    stats = st.columns(5)
    stats[0].metric("Review rows total", f"{len(df):,}")
    stats[1].metric("Pending", f"{(df['status'] == 'pending').sum():,}")
    stats[2].metric("Accept", f"{(df['status'] == 'accept').sum():,}")
    stats[3].metric("Correct", f"{(df['status'] == 'correct').sum():,}")
    stats[4].metric("Reject", f"{(df['status'] == 'reject').sum():,}")

    control_cols = st.columns([1, 1, 1, 2])
    week_options = ["All"] + sorted([value for value in df["week"].astype(str).unique() if value and value != "nan"])
    type_options = ["All"] + sorted([value for value in df["type"].astype(str).unique() if value and value != "nan"])
    status_filter = control_cols[0].selectbox("Status filter", ["All", "pending", "accept", "correct", "reject"], index=1)
    week_filter = control_cols[1].selectbox("Week filter", week_options, index=0)
    type_filter = control_cols[2].selectbox("Type filter", type_options, index=0)
    min_conf = control_cols[3].slider("Maximum confidence to review", min_value=0.0, max_value=1.0, value=0.9, step=0.01)

    filtered = df.copy()
    filtered["confidence"] = pd.to_numeric(filtered["confidence"], errors="coerce").fillna(0.0)
    if status_filter != "All":
        filtered = filtered[filtered["status"] == status_filter]
    if week_filter != "All":
        filtered = filtered[filtered["week"] == week_filter]
    if type_filter != "All":
        filtered = filtered[filtered["type"] == type_filter]
    filtered = filtered[filtered["confidence"] <= min_conf]
    filtered = filtered.sort_values(["status", "confidence"], ascending=[True, True]).reset_index(drop=True)

    upload_cols = st.columns([1.35, 0.75])
    uploaded_file = upload_cols[0].file_uploader("Upload a reviewed CSV to replace the working review file", type=["csv"])
    if uploaded_file is not None:
        try:
            uploaded_df = pd.read_csv(uploaded_file)
            uploaded_df = ensure_review_df(uploaded_df)
            upload_cols[1].success(f"Loaded {len(uploaded_df)} rows")
            if st.button("Replace review CSV with uploaded file"):
                upload_errors = validate_review_rows(uploaded_df)
                if upload_errors:
                    st.error("Uploaded CSV has validation issues and was not saved.")
                    for message in upload_errors[:10]:
                        st.caption(f"- {message}")
                else:
                    output = save_review_csv(base_dir, uploaded_df)
                    st.success(f"Uploaded review file saved to {output}. Refresh the page to see the updated table.")
        except Exception as exc:
            upload_cols[1].error(f"Invalid CSV: {exc}")

    edited = st.data_editor(
        filtered,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_order=[
            "status",
            "split",
            "entity",
            "type",
            "corrected_entity",
            "corrected_type",
            "confidence",
            "article_title",
            "article_source",
            "article_link",
            "context",
            "week",
            "span_id",
        ],
        column_config={
            "status": st.column_config.SelectboxColumn("Status", options=["pending", "accept", "correct", "reject"], required=True),
            "split": st.column_config.SelectboxColumn("Split", options=["train", "eval"], required=True),
            "type": st.column_config.SelectboxColumn("Predicted type", options=list(TYPE_LABELS.keys())),
            "corrected_type": st.column_config.SelectboxColumn("Corrected type", options=[""] + list(TYPE_LABELS.keys())),
            "confidence": st.column_config.NumberColumn("Confidence", min_value=0.0, max_value=1.0, format="%.3f"),
            "article_link": st.column_config.LinkColumn("Article", display_text="open"),
            "context": st.column_config.TextColumn("Context", width="large"),
            "article_title": st.column_config.TextColumn("Topic", width="medium"),
            "article_source": st.column_config.TextColumn("Source", width="small"),
            "span_id": st.column_config.TextColumn("Span ID", width="medium"),
        },
        key="review_editor",
    )

    review_errors = validate_review_rows(edited)
    if review_errors:
        st.error("Review file has validation issues. Fix these before saving.")
        for message in review_errors[:12]:
            st.caption(f"- {message}")
        if len(review_errors) > 12:
            st.caption(f"...and {len(review_errors) - 12} more")
    else:
        st.success("Review table passed validation.")

    action_cols = st.columns([1.1, 1.1, 1.15, 1, 1.6])
    if action_cols[0].button("Save review CSV", type="primary"):
        if review_errors:
            st.error("Cannot save until validation errors are fixed.")
        else:
            saved_df = overlay_review_frames(df, edited)
            saved_path = save_review_csv(base_dir, saved_df)
            st.success(f"Saved review file to {saved_path}")

    if action_cols[1].button("Publish review CSV to S3"):
        if review_errors:
            st.error("Cannot publish until validation errors are fixed.")
        else:
            saved_df = overlay_review_frames(df, edited)
            save_review_csv(base_dir, saved_df)
            review_key = publish_review_csv(saved_df)
            st.success(f"Published reviewed CSV to {review_key}")

    if action_cols[2].button("Request retrain"):
        if review_errors:
            st.error("Cannot request retrain until validation errors are fixed.")
        else:
            saved_df = overlay_review_frames(df, edited)
            accepted_ready = int(saved_df["status"].astype(str).str.lower().isin(["accept", "correct"]).sum())
            if accepted_ready == 0:
                st.error("No accepted or corrected rows are ready for retraining yet.")
            else:
                save_review_csv(base_dir, saved_df)
                review_key = publish_review_csv(saved_df)
                request_key = create_retrain_request(saved_df)
                st.success(
                    f"Retrain requested. Review file published to {review_key} and trigger marker created at {request_key}."
                )

    action_cols[3].download_button(
        "Download full review CSV",
        data=ensure_review_df(df).to_csv(index=False).encode("utf-8"),
        file_name="label_review.csv",
        mime="text/csv",
    )
    action_cols[4].download_button(
        "Download filtered CSV",
        data=ensure_review_df(edited).to_csv(index=False).encode("utf-8"),
        file_name="label_review_filtered.csv",
        mime="text/csv",
    )

    missing_meta = df[
        (df["article_title"].astype(str).str.len() == 0)
        | (df["article_source"].astype(str).str.len() == 0)
        | (df["article_link"].astype(str).str.len() == 0)
    ]
    if not missing_meta.empty:
        st.caption(
            f"{len(missing_meta)} queue rows still lack some article metadata. Newer queue items should backfill after a fresh NER run."
        )


def main() -> None:
    inject_styles()
    st.markdown(
        """
        <div class="hero">
          <h1>AI News Operator Console</h1>
          <p>The graph is the product. This console is tuned around making the entity network easier to compare, filter, interrogate, and label so the MVP feels like an analysis surface rather than just a report page.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    base_dir = st.sidebar.text_input("Data directory", value=LOCAL_DIR)
    st.sidebar.caption("On EC2, point this at /opt/ai-news-mlops/MLOps/local_data after the pipeline syncs S3 content locally.")

    df, edges, payload = load_dashboard_state(base_dir)
    article_metadata = load_article_metadata(base_dir)
    tabs = st.tabs(["Overview", "Graph Console", "Review Queue"])

    with tabs[0]:
        render_overview_tab(df, payload)
    with tabs[1]:
        render_graph_tab(df, edges, payload)
    with tabs[2]:
        render_review_tab(base_dir, article_metadata)


if __name__ == "__main__":
    main()
