"""
Streamlit operator dashboard for the AI news NER MVP.

Run locally or on EC2:
  streamlit run streamlit_dashboard.py --server.port 8501
"""

from __future__ import annotations

import json
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
                radial-gradient(circle at top left, rgba(234,88,12,0.12), transparent 28rem),
                radial-gradient(circle at 88% 8%, rgba(37,99,235,0.15), transparent 24rem),
                linear-gradient(135deg, #f6f1e8, #e6f1f7 52%, #f4e7d3);
        }
        .block-container {
            max-width: 1500px;
            padding-top: 1.2rem;
            padding-bottom: 2rem;
        }
        .hero {
            background: rgba(255,255,255,0.80);
            border: 1px solid rgba(255,255,255,0.78);
            border-radius: 28px;
            padding: 1.5rem 1.6rem;
            box-shadow: 0 24px 80px rgba(46, 63, 86, 0.14);
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
            margin: 0.75rem 0 0 0;
            color: #5f6f84;
            font-size: 1rem;
            line-height: 1.55;
            max-width: 900px;
        }
        .metric-card {
            background: rgba(255,255,255,0.84);
            border: 1px solid rgba(255,255,255,0.74);
            border-radius: 22px;
            padding: 1rem 1.1rem;
            box-shadow: 0 12px 34px rgba(46,63,86,0.10);
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
            background: rgba(255,255,255,0.72);
            border: 1px solid #d8e2ec;
            border-radius: 18px;
            padding: .85rem 1rem;
            margin-bottom: .6rem;
        }
        .topic-title {
            color: #102033;
            font-weight: 800;
        }
        .topic-meta {
            color: #66758a;
            font-size: .88rem;
            margin-top: .2rem;
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
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def load_dashboard_state(base_dir: str) -> tuple[pd.DataFrame, dict]:
    batches = load_batches(base_dir)
    if not batches:
        return pd.DataFrame(), {"weeks": [], "windows": [], "trends": {"weeks": []}}
    df = flatten_entities(batches)
    edges = build_edges(df)
    payload = build_dashboard_payload(df, edges)
    return df, payload


def load_queue_records(base_dir: str) -> pd.DataFrame:
    queue_dir = Path(base_dir) / "label-queue"
    rows = []
    for json_path in sorted(queue_dir.rglob("*.json")):
        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
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
                "article_id": item.get("article_id", ""),
                "article_title": item.get("article_title", ""),
                "article_source": item.get("article_source", ""),
                "article_link": item.get("article_link", ""),
                "week": item.get("week", ""),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    df = df.drop_duplicates(subset=["span_id"]).reindex(columns=REVIEW_COLUMNS).fillna("")
    return df.astype("object")


def load_review_csv(base_dir: str) -> pd.DataFrame:
    review_path = Path(base_dir) / "review" / "label_review.csv"
    if not review_path.exists():
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    try:
        df = pd.read_csv(review_path).reindex(columns=REVIEW_COLUMNS).fillna("")
        return df.astype("object")
    except Exception:
        return pd.DataFrame(columns=REVIEW_COLUMNS)


def save_review_csv(base_dir: str, df: pd.DataFrame) -> Path:
    review_dir = Path(base_dir) / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / "label_review.csv"
    df.reindex(columns=REVIEW_COLUMNS).to_csv(path, index=False)
    return path


def merge_queue_and_review(base_dir: str) -> pd.DataFrame:
    queue_df = load_queue_records(base_dir)
    review_df = load_review_csv(base_dir)
    if queue_df.empty:
        return review_df.reindex(columns=REVIEW_COLUMNS)
    if review_df.empty:
        return queue_df
    merged = queue_df.set_index("span_id").reindex(columns=[col for col in REVIEW_COLUMNS if col != "span_id"]).astype("object")
    review_df = review_df.set_index("span_id").reindex(columns=[col for col in REVIEW_COLUMNS if col != "span_id"]).astype("object")

    all_ids = merged.index.union(review_df.index)
    merged = merged.reindex(all_ids, fill_value="")
    review_df = review_df.reindex(all_ids, fill_value="")

    combined = pd.DataFrame(index=all_ids)
    for col in [col for col in REVIEW_COLUMNS if col != "span_id"]:
        queue_col = merged[col].where(merged[col].notna(), "")
        review_col = review_df[col].where(review_df[col].notna(), "")
        combined[col] = review_col.where(review_col.astype(str).str.len() > 0, queue_col)

    combined = combined.reset_index().rename(columns={"index": "span_id"})
    return combined.reindex(columns=REVIEW_COLUMNS).fillna("").astype("object")


def pct(value: float) -> str:
    return f"{round(value * 100)}%"


def type_pill(entity_type: str) -> str:
    color = TYPE_COLORS.get(entity_type, "#64748B")
    return f"<span class='pill' style='background:{color}'>{entity_type}</span>"


def build_network_figure(window_data: dict, baseline_ids: set[str], active_types: list[str], min_mentions: int, min_edge: int, labels_on: bool, new_only: bool) -> go.Figure:
    nodes = []
    for node in window_data.get("nodes", []):
        if node["type"] not in active_types:
            continue
        if int(node["mentions"]) < min_mentions:
            continue
        if new_only and node["id"] in baseline_ids:
            continue
        nodes.append(node)

    node_by_id = {node["id"]: node for node in nodes}
    edge_x, edge_y, edge_text = [], [], []
    for edge in window_data.get("edges", []):
        if int(edge["weight"]) < min_edge:
            continue
        source = node_by_id.get(edge["source"])
        target = node_by_id.get(edge["target"])
        if not source or not target:
            continue
        edge_x += [source["x"], target["x"], None]
        edge_y += [source["y"], target["y"], None]
        tip = f"{edge['source']} <-> {edge['target']}<br>{edge['weight']} shared article co-mention(s)"
        edge_text += [tip, tip, tip]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=edge_x,
            y=edge_y,
            text=edge_text,
            mode="lines",
            line=dict(color="rgba(83,102,128,0.28)", width=1.5),
            hovertemplate="%{text}<extra>Connection</extra>",
            name="Connections",
        )
    )

    for entity_type in active_types:
        bucket = [node for node in nodes if node["type"] == entity_type]
        if not bucket:
            continue
        fig.add_trace(
            go.Scatter(
                x=[node["x"] for node in bucket],
                y=[node["y"] for node in bucket],
                text=[node["id"] for node in bucket],
                customdata=[[node["type"], node["mentions"], node["confidence"], node["articles"], node["first_week"], node["flagged"]] for node in bucket],
                mode="markers+text" if labels_on else "markers",
                textposition="top center",
                textfont=dict(size=10, color="#172033"),
                marker=dict(
                    color=TYPE_COLORS.get(entity_type, "#64748B"),
                    size=[min(44, 10 + (float(node["mentions"]) ** 0.5) * 6) for node in bucket],
                    line=dict(color="white", width=1.5),
                    opacity=0.88,
                ),
                hovertemplate="<b>%{text}</b><br>Type: %{customdata[0]}<br>Mentions: %{customdata[1]}<br>Confidence: %{customdata[2]:.2f}<br>Articles: %{customdata[3]}<br>First seen: %{customdata[4]}<br>Flagged: %{customdata[5]:.0%}<extra></extra>",
                name=entity_type,
            )
        )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FBFCFE",
        margin=dict(l=6, r=6, t=18, b=6),
        height=580,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
        legend=dict(orientation="h", y=1.08, x=0),
        dragmode="pan",
    )
    return fig


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
        ("Batches", f"{kpis['batches']:,}"),
        ("Confidence", pct(kpis["mean_confidence"])),
        ("Flagged", pct(kpis["flagged_pct"])),
    ]
    for col, (label, value) in zip(cols, items):
        col.markdown(f"<div class='metric-card'><div class='label'>{label}</div><div class='value'>{value}</div></div>", unsafe_allow_html=True)

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
              <div class="topic-meta">Confidence {pct(item['confidence'])} · Flagged {pct(item['flagged'])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_graph_tab(payload: dict) -> None:
    weeks = payload.get("weeks", [])
    windows = payload.get("windows", [])
    if not weeks:
        st.warning("No weekly windows found yet.")
        return

    controls = st.columns([1.1, 1.1, 0.8, 0.8])
    left_week = controls[0].selectbox("Baseline week", weeks, index=0)
    right_week = controls[1].selectbox("Comparison week", weeks, index=len(weeks) - 1)
    min_mentions = int(controls[2].slider("Minimum mentions", min_value=1, max_value=max(1, max(node["mentions"] for window in windows for node in window["nodes"])), value=1))
    min_edge = int(controls[3].slider("Minimum edge strength", min_value=1, max_value=max(1, max(edge["weight"] for window in windows for edge in window["edges"]) if any(window["edges"] for window in windows) else 1), value=1))

    active_types = st.multiselect("Entity types", list(TYPE_COLORS.keys()), default=list(TYPE_COLORS.keys()))
    switch_cols = st.columns(2)
    labels_on = switch_cols[0].toggle("Display labels", value=True)
    new_only = switch_cols[1].toggle("Only show newly added nodes in comparison view", value=False)

    left_index = weeks.index(left_week)
    right_index = weeks.index(right_week)
    baseline_ids = {node["id"] for node in windows[left_index]["nodes"]}

    graph_cols = st.columns(2)
    with graph_cols[0]:
        st.caption(f"Up to {left_week}")
        st.plotly_chart(
            build_network_figure(
                windows[left_index],
                baseline_ids=set(),
                active_types=active_types,
                min_mentions=min_mentions,
                min_edge=min_edge,
                labels_on=labels_on,
                new_only=False,
            ),
            use_container_width=True,
            config={"displaylogo": False, "scrollZoom": True},
            key=f"network_left_{left_week}_{min_mentions}_{min_edge}_{labels_on}",
        )
    with graph_cols[1]:
        st.caption(f"Up to {right_week}")
        st.plotly_chart(
            build_network_figure(
                windows[right_index],
                baseline_ids=baseline_ids,
                active_types=active_types,
                min_mentions=min_mentions,
                min_edge=min_edge,
                labels_on=labels_on,
                new_only=new_only,
            ),
            use_container_width=True,
            config={"displaylogo": False, "scrollZoom": True},
            key=f"network_right_{right_week}_{left_week}_{min_mentions}_{min_edge}_{labels_on}_{new_only}",
        )

    left_nodes = {node["id"]: node for node in windows[left_index]["nodes"]}
    right_nodes = {node["id"]: node for node in windows[right_index]["nodes"]}
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
        (delta_cols[0], "New in comparison", sorted(new_items, key=lambda x: x["mentions"], reverse=True)[:10], lambda item: ""),
        (delta_cols[1], "Biggest risers", sorted(risers, key=lambda x: x["delta"], reverse=True)[:10], lambda item: f" · +{item['delta']}"),
        (delta_cols[2], "Dropped since baseline", sorted(dropped_items, key=lambda x: x["mentions"], reverse=True)[:10], lambda item: ""),
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


def render_review_tab(base_dir: str) -> None:
    df = merge_queue_and_review(base_dir)
    if df.empty:
        st.info("No pending label-queue items found in local_data/label-queue.")
        return

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

    st.caption("Edit statuses directly in the table. Accept keeps the suggestion, Correct uses corrected fields, Reject drops the span. The saved CSV stays compatible with the retrain script.")

    edited = st.data_editor(
        filtered,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
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
    )

    action_cols = st.columns([1, 1, 2])
    if action_cols[0].button("Save review CSV", type="primary"):
        merged = df.set_index("span_id")
        merged.update(edited.set_index("span_id"))
        saved_path = save_review_csv(base_dir, merged.reset_index())
        st.success(f"Saved review file to {saved_path}")
    if action_cols[1].button("Export filtered CSV"):
        csv_bytes = edited.to_csv(index=False).encode("utf-8")
        st.download_button("Download filtered review CSV", data=csv_bytes, file_name="label_review_filtered.csv", mime="text/csv")


def main() -> None:
    inject_styles()
    st.markdown(
        """
        <div class="hero">
          <h1>AI News Operator Console</h1>
          <p>One operator surface for hot topics, weekly graph comparison, drift-friendly entity monitoring, and the human review queue. This replaces the awkward CSV hop with a review workflow that stays close to the data and the dashboard.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    base_dir = st.sidebar.text_input("Data directory", value=LOCAL_DIR)
    st.sidebar.caption("On EC2, point this at /opt/ai-news-mlops/MLOps/local_data after the pipeline syncs S3 content locally.")

    df, payload = load_dashboard_state(base_dir)
    tabs = st.tabs(["Overview", "Weekly Compare", "Review Queue"])

    with tabs[0]:
        render_overview_tab(df, payload)
    with tabs[1]:
        render_graph_tab(payload)
    with tabs[2]:
        render_review_tab(base_dir)


if __name__ == "__main__":
    main()
