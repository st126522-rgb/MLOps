"""
Streamlit operator dashboard for the AI news NER MVP.

Run locally or on EC2:
  streamlit run streamlit_dashboard.py --server.port 8501
"""

from __future__ import annotations

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
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(245, 158, 11, 0.10), transparent 28rem),
                radial-gradient(circle at 92% 6%, rgba(37, 99, 235, 0.14), transparent 24rem),
                linear-gradient(135deg, #f5efe5 0%, #ebf3f8 48%, #f6ecde 100%);
            color: #102033;
        }
        .block-container {
            max-width: 1580px;
            padding-top: 1rem;
            padding-bottom: 2rem;
        }
        .hero {
            background: rgba(255,255,255,0.86);
            border: 1px solid rgba(255,255,255,0.82);
            border-radius: 30px;
            padding: 1.6rem 1.7rem;
            box-shadow: 0 24px 70px rgba(30, 41, 59, 0.14);
            backdrop-filter: blur(12px);
            margin-bottom: 1rem;
        }
        .hero h1 {
            margin: 0;
            font-size: 3rem;
            line-height: 0.95;
            letter-spacing: -0.05em;
            color: #102033;
        }
        .hero p {
            margin: 0.85rem 0 0 0;
            color: #526276;
            font-size: 1rem;
            line-height: 1.6;
            max-width: 980px;
        }
        .metric-card {
            background: rgba(255,255,255,0.86);
            border: 1px solid rgba(220, 229, 239, 0.88);
            border-radius: 22px;
            padding: 1rem 1.1rem;
            box-shadow: 0 12px 34px rgba(46,63,86,0.09);
        }
        .metric-card .label {
            color: #62748a;
            text-transform: uppercase;
            letter-spacing: .08em;
            font-size: .76rem;
            font-weight: 700;
        }
        .metric-card .value {
            color: #102033;
            font-size: 2rem;
            font-weight: 800;
            margin-top: .25rem;
        }
        .topic-card {
            background: rgba(255,255,255,0.80);
            border: 1px solid #dbe5ef;
            border-radius: 18px;
            padding: .85rem 1rem;
            margin-bottom: .6rem;
            box-shadow: 0 8px 24px rgba(46,63,86,0.05);
        }
        .topic-title {
            color: #102033;
            font-weight: 800;
        }
        .topic-meta {
            color: #5f6f84;
            font-size: .88rem;
            margin-top: .2rem;
        }
        .analysis-note {
            background: rgba(255,255,255,0.82);
            border: 1px solid #dbe5ef;
            border-radius: 18px;
            padding: 0.9rem 1rem;
            color: #4a5b72;
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
            color: #0f4aa3 !important;
        }
        a:hover {
            color: #0b66d0 !important;
        }
        .stTabs [data-baseweb="tab"] {
            color: #31445c;
            font-weight: 700;
        }
        .stTabs [aria-selected="true"] {
            color: #102033 !important;
        }
        .stButton button, .stDownloadButton button {
            border-radius: 12px;
            border: 1px solid #cdd8e4;
            color: #102033;
            background: rgba(255,255,255,0.88);
            font-weight: 700;
        }
        .stButton button:hover, .stDownloadButton button:hover {
            border-color: #0b66d0;
            color: #0b66d0;
            background: #f7fbff;
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
    return min(52.0, 12.0 + (max(1, mentions) ** 0.5) * 6.0)


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

    edge_buckets: dict[tuple[str, bool], dict[str, list]] = defaultdict(lambda: {"x": [], "y": [], "text": []})
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

        bucket = edge_buckets[(status, highlighted)]
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
    for (status, _highlighted), points in edge_buckets.items():
        color, width = edge_styles[status]
        if not points["x"]:
            continue
        fig.add_trace(
            go.Scatter(
                x=points["x"],
                y=points["y"],
                text=points["text"],
                mode="lines",
                line=dict(color=color, width=width),
                hovertemplate="%{text}<extra>Connection</extra>",
                name=edge_names[status],
                showlegend=status in {"current", "persistent", "historical"},
            )
        )

    status_groups = {"new": [], "persistent": [], "historical": []}
    for node in combined_nodes.values():
        status_groups[node["status"]].append(node)

    for status, nodes in status_groups.items():
        if not nodes:
            continue
        marker_colors = []
        marker_lines = []
        marker_sizes = []
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
                line_color = "#0F172A"

            marker_colors.append(rgba(color, alpha))
            marker_lines.append(line_color)
            marker_sizes.append(node_size(node["mentions"]))
            text_labels.append(node["id"])
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
                textfont=dict(size=11, color="#102033"),
                marker=dict(
                    color=marker_colors,
                    size=marker_sizes,
                    line=dict(color=marker_lines, width=2.0),
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

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FCFDFE",
        margin=dict(l=8, r=8, t=18, b=8),
        height=720,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
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

    edge_x, edge_y, edge_text = [], [], []
    for edge in window_data.get("edges", []):
        if edge["source"] not in nodes or edge["target"] not in nodes:
            continue
        if int(edge["weight"]) < min_edge:
            continue
        source = nodes[edge["source"]]
        target = nodes[edge["target"]]
        color = "rgba(37,99,235,0.26)"
        width = 1.6
        if focus_entity and edge["source"] != focus_entity and edge["target"] != focus_entity:
            if edge["source"] not in focus_neighbors and edge["target"] not in focus_neighbors:
                color = "rgba(148,163,184,0.10)"
                width = 1.0
        edge_x += [source["x"], target["x"], None]
        edge_y += [source["y"], target["y"], None]
        edge_text += [f"{edge['source']} <-> {edge['target']}<br>{edge['weight']} shared article co-mention(s)"] * 3

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=edge_x,
            y=edge_y,
            text=edge_text,
            mode="lines",
            line=dict(color="rgba(83,102,128,0.24)", width=1.5),
            hovertemplate="%{text}<extra>Connection</extra>",
            name="Connections",
        )
    )

    for entity_type in active_types:
        bucket = [node for node in nodes.values() if node["type"] == entity_type]
        if not bucket:
            continue
        marker_colors = []
        line_colors = []
        for node in bucket:
            alpha = 0.92
            if focus_entity and node["id"] != focus_entity and node["id"] not in focus_neighbors:
                alpha = 0.18
            marker_colors.append(rgba(TYPE_COLORS.get(entity_type, "#64748B"), alpha))
            line_colors.append("#0F172A" if node["id"] == focus_entity else "#ffffff")

        fig.add_trace(
            go.Scatter(
                x=[node["x"] for node in bucket],
                y=[node["y"] for node in bucket],
                text=[node["id"] for node in bucket],
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
                textfont=dict(size=10, color="#172033"),
                marker=dict(
                    color=marker_colors,
                    size=[node_size(int(node["mentions"])) for node in bucket],
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

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FBFCFE",
        margin=dict(l=6, r=6, t=18, b=6),
        height=620,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
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

    header_cols = st.columns([1.1, 1.1, 1.2, 0.9])
    baseline_week = header_cols[0].selectbox("Baseline week", weeks, index=0)
    comparison_week = header_cols[1].selectbox("Comparison week", weeks, index=len(weeks) - 1)
    graph_mode = header_cols[2].radio("Graph view", ["Overlay compare", "Side-by-side"], horizontal=True)
    labels_on = header_cols[3].toggle("Display labels", value=True)

    if week_order[baseline_week] > week_order[comparison_week]:
        st.warning("Baseline week cannot be later than comparison week. I am using the comparison week for both views.")
        baseline_week = comparison_week

    baseline_weeks = weeks[: week_order[baseline_week] + 1]
    comparison_weeks = weeks[: week_order[comparison_week] + 1]

    baseline_window = build_window_for_weeks(df, edges, positions, baseline_weeks)
    comparison_window = build_window_for_weeks(df, edges, positions, comparison_weeks)

    max_mentions = max(1, int(df.groupby("entity").size().max())) if not df.empty else 1
    max_edge = max(1, int(edges["weight"].max())) if not edges.empty else 1

    filter_cols = st.columns([1.1, 1.1, 1.0, 1.0])
    active_types = filter_cols[0].multiselect("Entity types", list(TYPE_COLORS.keys()), default=list(TYPE_COLORS.keys()))
    first_seen_range = filter_cols[1].select_slider("First seen week range", options=weeks, value=(weeks[0], weeks[-1]))
    min_mentions = int(filter_cols[2].slider("Minimum node size", min_value=1, max_value=max_mentions, value=1))
    min_edge = int(filter_cols[3].slider("Minimum edge strength", min_value=1, max_value=max_edge, value=1))

    option_cols = st.columns([1, 1, 1, 1, 1, 1])
    node_cap = int(option_cols[0].slider("Node cap", min_value=20, max_value=max(20, min(240, len(comparison_window["nodes"]) or 20)), value=min(120, max(20, len(comparison_window["nodes"]) or 20))))
    show_new_only = option_cols[1].toggle("Only new nodes", value=False)
    show_dropped_nodes = option_cols[2].toggle("Show historical-only nodes", value=True)
    show_historical_edges = option_cols[3].toggle("Show historical-only edges", value=True)
    show_isolated_nodes = option_cols[4].toggle("Show isolated nodes", value=False)
    focus_neighbors_only = option_cols[5].toggle("Focus mode hides unrelated nodes", value=False)

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
    focus_entity = st.selectbox("Focus entity", [""] + focus_candidates, index=0, help="Use this to spotlight one node and its immediate neighborhood.")

    st.markdown(
        """
        <div class="analysis-note">
          Overlay mode is the main analysis view. Bright nodes are new in the comparison window, muted colored nodes persisted from the baseline,
          and gray nodes are historical-only. Use the focus entity control to isolate a neighborhood and make dense regions easier to read.
        </div>
        """,
        unsafe_allow_html=True,
    )

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

    delta_cols = st.columns(3)
    for col, title, items, extra in [
        (delta_cols[0], "New in comparison", sorted(new_items, key=lambda item: item["mentions"], reverse=True)[:10], lambda item: ""),
        (delta_cols[1], "Biggest risers", sorted(risers, key=lambda item: item["delta"], reverse=True)[:10], lambda item: f" - +{item['delta']}"),
        (delta_cols[2], "Dropped since baseline", sorted(dropped_items, key=lambda item: item["mentions"], reverse=True)[:10], lambda item: ""),
    ]:
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
    stats[0].metric("Queue rows", f"{len(df):,}")
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

    action_cols = st.columns([1, 1, 1, 2])
    if action_cols[0].button("Save review CSV", type="primary"):
        saved_df = overlay_review_frames(df, edited)
        saved_path = save_review_csv(base_dir, saved_df)
        st.success(f"Saved review file to {saved_path}")

    action_cols[1].download_button(
        "Download full review CSV",
        data=ensure_review_df(df).to_csv(index=False).encode("utf-8"),
        file_name="label_review.csv",
        mime="text/csv",
    )
    action_cols[2].download_button(
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
