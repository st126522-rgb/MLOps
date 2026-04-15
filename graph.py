"""
graph.py
=========
Build and save knowledge graph from local entity outputs.
Run after ner.py has written to local_data/entities/

Usage:
    python graph.py
    python graph.py --week 2026-W14
    python graph.py --open   # opens in browser automatically
"""

import os
import sys
import json
import glob
import argparse
import datetime
import pathlib
import webbrowser

import numpy as np
import pandas as pd
import networkx as nx
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Config ────────────────────────────────────────────────────────────
LOCAL_DIR  = os.environ.get("LOCAL_DIR",
             r"C:\Users\gaurav\OneDrive\Desktop\MLOps\local_data")
OUTPUT_DIR = os.path.join(LOCAL_DIR, "graphs")
pathlib.Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

TYPE_COLORS = {
    "ORG":  "#1565c0",
    "MISC": "#2e7d32",
    "PER":  "#e65100",
    "LOC":  "#6a1b9a",
}

EDGE_COLORS = {
    "INTRODUCES":   "#00e676",
    "OUTPERFORMS":  "#ff1744",
    "COMPETES":     "#ff6d00",
    "PARTNERS":     "#2979ff",
    "FUNDS":        "#ffd600",
    "BUILT_BY":     "#d500f9",
    "CO_OCCURS":    "#64748b",
}


# ── Data loading ──────────────────────────────────────────────────────

def load_entity_batches(base_dir: str, week_filter: str = None) -> list:
    pattern = os.path.join(base_dir, "entities", "**", "*.json")
    files   = sorted(glob.glob(pattern, recursive=True))
    batches = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if week_filter and data.get("week", "") > week_filter:
            continue
        data["_mtime"] = datetime.datetime.fromtimestamp(
            os.path.getmtime(f))
        batches.append(data)
    return batches


def flatten_to_df(batches: list) -> pd.DataFrame:
    rows = []
    for batch in batches:
        week     = batch.get("week", "unknown")
        batch_id = batch.get("batch_id", "unknown")
        mtime    = batch.get("_mtime")
        for article in batch.get("entity_results", []):
            title = article.get("title", "")
            for ent in article.get("entities", []):
                rows.append({
                    "entity":     ent["entity"],
                    "type":       ent["type"],
                    "confidence": ent["confidence"],
                    "flagged":    ent.get("flagged", float(ent["confidence"]) < 0.70),
                    "article":    title,
                    "batch_id":   batch_id,
                    "week":       week,
                    "logged_at":  mtime,
                })
    if not rows:
        return pd.DataFrame(columns=[
            "entity","type","confidence","flagged",
            "article","batch_id","week","logged_at"])
    return pd.DataFrame(rows)


def build_co_occurrence_edges(batches: list) -> pd.DataFrame:
    """
    Until relation extraction is built, use entity co-occurrence
    within the same article as edges.
    """
    rows = []
    for batch in batches:
        week = batch.get("week", "unknown")
        for article in batch.get("entity_results", []):
            entities = [e["entity"] for e in article.get("entities", [])]
            # All pairs that appear in the same article
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    rows.append({
                        "source":   entities[i],
                        "target":   entities[j],
                        "relation": "CO_OCCURS",
                        "week":     week,
                    })
    if not rows:
        return pd.DataFrame(columns=["source","target","relation","week"])
    return pd.DataFrame(rows)


# ── Graph builder ─────────────────────────────────────────────────────

def build_nx_graph(df: pd.DataFrame,
                   edges: pd.DataFrame) -> nx.MultiDiGraph:
    node_stats = (df.groupby(["entity", "type"])
                  .agg(mentions   = ("entity",     "count"),
                       mean_conf  = ("confidence", "mean"),
                       first_week = ("week",       "min"),
                       last_week  = ("week",       "max"))
                  .reset_index())

    G = nx.MultiDiGraph()

    for _, row in node_stats.iterrows():
        G.add_node(row["entity"],
                   etype      = row["type"],
                   mentions   = int(row["mentions"]),
                   conf       = round(row["mean_conf"], 3),
                   first_week = row["first_week"],
                   last_week  = row["last_week"])

    # Deduplicate edges — count weight
    if not edges.empty:
        edge_counts = (edges.groupby(["source","target","relation"])
                       .size().reset_index(name="weight"))
        for _, row in edge_counts.iterrows():
            if row["source"] in G.nodes and row["target"] in G.nodes:
                G.add_edge(row["source"], row["target"],
                           relation=row["relation"],
                           weight=int(row["weight"]))
    return G


def get_3d_positions(G: nx.MultiDiGraph) -> dict:
    pos2d = nx.spring_layout(G.to_undirected(), seed=42, k=1.8)
    try:
        cent = nx.eigenvector_centrality_numpy(G.to_undirected())
    except Exception:
        cent = {n: 0.5 for n in G.nodes()}
    return {
        node: np.array([x, y, cent.get(node, 0.5) * 2 - 1])
        for node, (x, y) in pos2d.items()
    }


# ── Plotly 3D graph ───────────────────────────────────────────────────

def build_3d_figure(G: nx.MultiDiGraph,
                    week_filter: str = None) -> go.Figure:

    if len(G.nodes()) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No entity data found",
                           x=0.5, y=0.5, showarrow=False,
                           font=dict(size=20, color="white"))
        fig.update_layout(paper_bgcolor="#0f172a")
        return fig

    pos          = get_3d_positions(G)
    max_mentions = max(
        (d["mentions"] for _, d in G.nodes(data=True)), default=1)

    # ── Edge traces ───────────────────────────────────────────────────
    edge_buckets = {}
    for src, tgt, attrs in G.edges(data=True):
        rel   = attrs.get("relation", "CO_OCCURS")
        color = EDGE_COLORS.get(rel, "#64748b")
        if src not in pos or tgt not in pos:
            continue
        x0, y0, z0 = pos[src]
        x1, y1, z1 = pos[tgt]
        mx = x0 + 0.85 * (x1 - x0)
        my = y0 + 0.85 * (y1 - y0)
        mz = z0 + 0.85 * (z1 - z0)
        if rel not in edge_buckets:
            edge_buckets[rel] = {"x":[], "y":[], "z":[], "color": color}
        edge_buckets[rel]["x"] += [x0, mx, None]
        edge_buckets[rel]["y"] += [y0, my, None]
        edge_buckets[rel]["z"] += [z0, mz, None]

    edge_traces = [
        go.Scatter3d(
            x=d["x"], y=d["y"], z=d["z"],
            mode="lines",
            name=rel,
            line=dict(color=d["color"], width=2),
            hoverinfo="none",
            legendgroup=f"edge_{rel}",
            legendgrouptitle_text="Relations" if i == 0 else None,
        )
        for i, (rel, d) in enumerate(edge_buckets.items())
    ]

    # ── Node traces (one per entity type) ────────────────────────────
    type_buckets = {}
    for node, attrs in G.nodes(data=True):
        etype      = attrs.get("etype", "ORG")
        conf       = attrs.get("conf", 0.9)
        mentions   = attrs.get("mentions", 1)
        first_week = attrs.get("first_week", "")
        if node not in pos:
            continue
        x, y, z  = pos[node]
        size      = 6 + (mentions / max_mentions) * 24
        is_new    = (week_filter is not None and first_week == week_filter)
        opacity   = max(0.25, conf)
        tooltip   = (f"<b>{node}</b><br>"
                     f"Type: {etype}<br>"
                     f"Confidence: {conf:.3f}<br>"
                     f"Mentions: {mentions}<br>"
                     f"First seen: {first_week}<br>"
                     f"{'⚠️ NEW this week' if is_new else ''}")
        if etype not in type_buckets:
            type_buckets[etype] = {
                "x":[], "y":[], "z":[], "sizes":[], "conf":[], 
                "texts":[], "labels":[], "borders":[],
                "color": TYPE_COLORS.get(etype, "#546e7a"),
            }
        b = type_buckets[etype]
        b["x"].append(x);  b["y"].append(y);  b["z"].append(z)
        b["sizes"].append(size)
        b["conf"].append(conf)
        b["texts"].append(tooltip)
        b["labels"].append(node)
        b["borders"].append("#ffd600" if is_new else "#ffffff")

    node_traces = [
        go.Scatter3d(
            x=d["x"], y=d["y"], z=d["z"],
            mode="markers+text",
            name=etype,
            text=d["labels"],
            textposition="top center",
            textfont=dict(size=9, color="white"),
            hovertext=d["texts"],
            hoverinfo="text",
            marker=dict(
            size=d["sizes"],
            color=d["conf"],              # ✅ confidence drives color
            colorscale="Viridis",
            cmin=0,
            cmax=1,
            opacity=0.9,
            colorbar=dict(title="Confidence"),
            line=dict(color=d["borders"], width=2),
            ),
            legendgroup=f"type_{etype}",
            legendgrouptitle_text="Entity Types" if i == 0 else None,
        )
        for i, (etype, d) in enumerate(type_buckets.items())
    ]

    # ── Layout ────────────────────────────────────────────────────────
    title = (f"AI Entity Knowledge Graph"
             f"{' — up to ' + week_filter if week_filter else ' — All Data'}")

    fig = go.Figure(
        data=edge_traces + node_traces,
        layout=go.Layout(
            title=dict(text=title,
                       font=dict(size=18, color="white"), x=0.5),
            paper_bgcolor="#0f172a",
            scene=dict(
                bgcolor="#0f172a",
                xaxis=dict(showgrid=False, zeroline=False,
                           showticklabels=False, showbackground=False),
                yaxis=dict(showgrid=False, zeroline=False,
                           showticklabels=False, showbackground=False),
                zaxis=dict(showgrid=False, zeroline=False,
                           showticklabels=False, showbackground=False),
                camera=dict(eye=dict(x=1.5, y=1.5, z=0.8)),
            ),
            legend=dict(
                font=dict(color="white", size=11),
                bgcolor="#1e293b",
                bordercolor="#334155",
                borderwidth=1,
            ),
            margin=dict(l=0, r=0, t=60, b=0),
            height=750,
            annotations=[dict(
                text="🖱️  Left drag: rotate  |  Scroll: zoom  |  Right drag: pan  |  Hover: details",
                x=0.5, y=0.01, xref="paper", yref="paper",
                showarrow=False,
                font=dict(size=10, color="#64748b"),
            )],
        )
    )
    return fig


# ── Confidence timeline ───────────────────────────────────────────────

def build_timeline_figure(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return go.Figure()

    weekly = (df.groupby(["week", "type"])
              .agg(mean_conf  = ("confidence", "mean"),
                   flagged_pct= ("flagged",    "mean"))
              .reset_index())

    flag_weekly = (df.groupby("week")
                   .agg(flagged_pct=("flagged", "mean"))
                   .reset_index())

    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Mean Confidence by Entity Type",
                        "Flagged Span % — Drift Signal"),
        shared_xaxes=True,
        vertical_spacing=0.14,
    )

    for etype, color in TYPE_COLORS.items():
        sub = weekly[weekly["type"] == etype]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["week"], y=sub["mean_conf"],
            name=etype, line=dict(color=color, width=2),
            mode="lines+markers", marker=dict(size=7),
        ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=flag_weekly["week"], y=flag_weekly["flagged_pct"],
        name="Flagged %", marker_color="#e65100", opacity=0.7,
    ), row=2, col=1)

    fig.add_hline(y=0.72, row=1, col=1,
                  line=dict(color="red", dash="dash", width=1.5),
                  annotation_text="Drift threshold 0.72")
    fig.add_hline(y=0.30, row=2, col=1,
                  line=dict(color="red", dash="dash", width=1.5),
                  annotation_text="Flag threshold 30%")

    fig.update_layout(
        height=550,
        paper_bgcolor="#0f172a",
        plot_bgcolor="#1e293b",
        font_color="white",
        title=dict(text="Confidence Trend — Drift Monitor",
                   font=dict(size=16, color="white"), x=0.5),
        legend=dict(font=dict(color="white")),
    )
    fig.update_yaxes(range=[0, 1.05], row=1, col=1,
                     gridcolor="#334155")
    fig.update_yaxes(range=[0, 1.05], tickformat=".0%",
                     row=2, col=1, gridcolor="#334155")
    fig.update_xaxes(gridcolor="#334155")
    return fig


# ── Confidence table ──────────────────────────────────────────────────

def build_table_figure(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return go.Figure()

    agg = (df.groupby(["entity", "type"])
           .agg(mean_conf  = ("confidence", "mean"),
                mentions   = ("entity",     "count"),
                first_seen = ("week",       "min"),
                last_seen  = ("week",       "max"),
                flagged_n  = ("flagged",    "sum"),
                last_logged= ("logged_at",  "max"))
           .reset_index()
           .sort_values("mentions", ascending=False))

    agg["mean_conf"]  = agg["mean_conf"].round(3)
    agg["status"]     = agg["mean_conf"].apply(
        lambda c: "🔴 LOW" if c < 0.70 else
                  ("🟡 MED" if c < 0.85 else "🟢 OK"))
    agg["last_logged"] = pd.to_datetime(
        agg["last_logged"]).dt.strftime("%Y-%m-%d %H:%M").fillna("—")
    agg["flagged_str"] = (agg["flagged_n"].astype(int).astype(str)
                          + " / " + agg["mentions"].astype(str))

    fig = go.Figure(data=[go.Table(
        columnwidth=[150, 60, 80, 70, 80, 80, 90, 130],
        header=dict(
            values=["<b>Entity</b>","<b>Type</b>","<b>Conf</b>",
                    "<b>Mentions</b>","<b>First Seen</b>","<b>Last Seen</b>",
                    "<b>Flagged</b>","<b>Last Logged</b>"],
            fill_color="#1565c0",
            font=dict(color="white", size=12),
            align="left",
        ),
        cells=dict(
            values=[agg["entity"], agg["type"], agg["mean_conf"],
                    agg["mentions"], agg["first_seen"], agg["last_seen"],
                    agg["flagged_str"], agg["last_logged"]],
            fill_color=[["#1e293b" if i % 2 == 0 else "#0f172a"
                         for i in range(len(agg))]],
            font=dict(color="white", size=11),
            align="left",
        )
    )])
    fig.update_layout(
        title=dict(text="Entity Confidence Table",
                   font=dict(size=16, color="white"), x=0.5),
        paper_bgcolor="#0f172a",
        font_color="white",
        height=max(300, len(agg) * 28 + 100),
        margin=dict(l=10, r=10, t=60, b=10),
    )
    return fig


# ── Save all to HTML ──────────────────────────────────────────────────

def save_dashboard(df, batches, edges, week_filter=None):
    G   = build_nx_graph(df, edges)
    ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    week_tag = week_filter or "all"

    files = {}

    # 3D graph
    fig_graph = build_3d_figure(G, week_filter)
    path_graph = os.path.join(OUTPUT_DIR, f"graph_{week_tag}_{ts}.html")
    fig_graph.write_html(path_graph)
    files["graph"] = path_graph

    # Timeline
    fig_time = build_timeline_figure(df)
    path_time = os.path.join(OUTPUT_DIR, f"timeline_{week_tag}_{ts}.html")
    fig_time.write_html(path_time)
    files["timeline"] = path_time

    # Table
    fig_table = build_table_figure(df)
    path_table = os.path.join(OUTPUT_DIR, f"table_{week_tag}_{ts}.html")
    fig_table.write_html(path_table)
    files["table"] = path_table

    return files


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build knowledge graph")
    parser.add_argument("--week",  default=None,
                        help="Only include data up to this week e.g. 2026-W14")
    parser.add_argument("--open",  action="store_true",
                        help="Open graphs in browser after saving")
    parser.add_argument("--dir",   default=None,
                        help="Override LOCAL_DIR")
    args = parser.parse_args()

    base = args.dir or LOCAL_DIR
    print(f"\n[GRAPH] Loading entity data from {base}")

    batches = load_entity_batches(base, week_filter=args.week)
    if not batches:
        print("  [WARN] No entity batches found.")
        print(f"  Expected files in: {base}/entities/")
        print("  Run ner.py first.")
        sys.exit(1)

    df    = flatten_to_df(batches)
    
    df["confidence"] = (
    df["confidence"]
    .astype(str)                              # ensure string
    .str.extract(r"(\d+\.\d+)")[0]             # pull first valid float
    .astype(float)
    )
    
    df["flagged"] = (
    df["flagged"]
    .astype(str)
    .str.lower()
    .map({"true": 1, "false": 0})
    )

    # fallback for anything weird
    df["flagged"] = df["flagged"].fillna(0).astype(int)
    
    edges = build_co_occurrence_edges(batches)

    print(f"  Loaded {len(batches)} batches")
    print(f"  {len(df)} entity records across {df['week'].nunique()} weeks")
    print(f"  {len(edges)} co-occurrence edges")
    print(f"  Last logged: {df['logged_at'].max()}")

    files = save_dashboard(df, batches, edges, week_filter=args.week)

    print("\n  [OK] Saved:")
    for name, path in files.items():
        print(f"     {name:10s} -> {path}")

    if args.open:
        for path in files.values():
            webbrowser.open(f"file:///{path}")


if __name__ == "__main__":
    main()
