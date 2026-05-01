"""
Generate a polished local dashboard for the AI news entity graph.

The dashboard is static HTML with embedded data and client-side controls:
  - hot topics
  - confidence and flagging trends
  - two timeframe sliders for graph comparison
  - graph-change summaries for new, dropped, and rising entities
"""

import argparse
import datetime
import glob
import html
import json
import os
import pathlib
import webbrowser
from collections import Counter, defaultdict

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
from plotly.io import to_html

from config import LOCAL_DIR


OUTPUT_DIR = pathlib.Path(LOCAL_DIR) / "graphs"
TYPE_COLORS = {
    "ORG": "#2563EB",
    "MODEL": "#DB2777",
    "MISC": "#16A34A",
    "PER": "#EA580C",
    "LOC": "#9333EA",
}
TYPE_LABELS = {
    "ORG": "Organizations",
    "MODEL": "AI Models / Products",
    "MISC": "Concepts / Miscellaneous",
    "PER": "People",
    "LOC": "Places",
}


def load_batches(base_dir: str) -> list[dict]:
    files = sorted(glob.glob(os.path.join(base_dir, "entities", "**", "*.json"), recursive=True))
    batches = []
    for file_path in files:
        with open(file_path, encoding="utf-8") as handle:
            batch = json.load(handle)
        batch["_file"] = file_path
        batch["_mtime"] = datetime.datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(timespec="seconds")
        batches.append(batch)
    return batches


def as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def flatten_entities(batches: list[dict]) -> pd.DataFrame:
    rows = []
    for batch in batches:
        week = batch.get("week", "unknown")
        batch_id = batch.get("batch_id", "unknown")
        for article in batch.get("entity_results", []):
            article_id = article.get("article_id", "")
            title = article.get("title", "")
            seen_in_article = set()
            for entity in article.get("entities", []):
                name = str(entity.get("entity", "")).strip()
                entity_type = str(entity.get("type", "MISC")).strip() or "MISC"
                if not name:
                    continue
                key = (name.lower(), entity_type, article_id)
                if key in seen_in_article:
                    continue
                seen_in_article.add(key)
                rows.append(
                    {
                        "entity": name,
                        "entity_key": name.lower(),
                        "type": entity_type,
                        "confidence": as_float(entity.get("confidence")),
                        "flagged": as_bool(entity.get("flagged")),
                        "article_id": article_id,
                        "article": title,
                        "batch_id": batch_id,
                        "week": week,
                    }
                )
    return pd.DataFrame(rows)


def build_edges(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if df.empty:
        return pd.DataFrame(columns=["source", "target", "week", "weight"])

    for (week, article_id), group in df.groupby(["week", "article_id"]):
        entities = sorted(set(group["entity"]))
        for index, source in enumerate(entities):
            for target in entities[index + 1 :]:
                rows.append({"source": source, "target": target, "week": week, "weight": 1})

    if not rows:
        return pd.DataFrame(columns=["source", "target", "week", "weight"])
    return pd.DataFrame(rows)


def summarize_window(df: pd.DataFrame, edges: pd.DataFrame, weeks: list[str], end_index: int) -> dict:
    selected_weeks = set(weeks[: end_index + 1])
    sub = df[df["week"].isin(selected_weeks)]
    edge_sub = edges[edges["week"].isin(selected_weeks)]

    if sub.empty:
        return {"nodes": [], "edges": [], "hot": [], "kpis": {}, "types": {}}

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

    type_counts = grouped.groupby("type")["entity"].count().to_dict()
    nodes = []
    for _, row in grouped.iterrows():
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
            }
        )

    edge_counts = Counter()
    for _, row in edge_sub.iterrows():
        source, target = sorted([row["source"], row["target"]])
        if source in grouped["entity"].values and target in grouped["entity"].values:
            edge_counts[(source, target)] += int(row.get("weight", 1))

    edges_out = [
        {"source": source, "target": target, "weight": weight}
        for (source, target), weight in edge_counts.items()
        if weight > 0
    ]

    hot = sorted(nodes, key=lambda item: (item["mentions"], item["confidence"]), reverse=True)[:15]
    kpis = {
        "weeks": len(selected_weeks),
        "batches": int(sub["batch_id"].nunique()),
        "articles": int(sub["article_id"].nunique()),
        "entities": len(nodes),
        "mentions": int(sub.shape[0]),
        "flagged_pct": round(float(sub["flagged"].mean()) if not sub.empty else 0.0, 4),
        "mean_confidence": round(float(sub["confidence"].mean()) if not sub.empty else 0.0, 4),
    }
    return {"nodes": nodes, "edges": edges_out, "hot": hot, "kpis": kpis, "types": type_counts}


def global_layout(windows: list[dict]) -> dict[str, tuple[float, float]]:
    graph = nx.Graph()
    for window in windows:
        for node in window["nodes"]:
            graph.add_node(node["id"], weight=node["mentions"])
        for edge in window["edges"]:
            graph.add_edge(edge["source"], edge["target"], weight=edge["weight"])

    if not graph.nodes:
        return {}

    positions = nx.spring_layout(graph, seed=7, k=1.6, iterations=150, weight="weight")
    return {node: [float(pos[0]), float(pos[1])] for node, pos in positions.items()}


def weekly_trends(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"weeks": [], "confidence": [], "flagged": [], "mentions": []}
    grouped = (
        df.groupby("week")
        .agg(
            confidence=("confidence", "mean"),
            flagged=("flagged", "mean"),
            mentions=("entity", "count"),
            unique_entities=("entity", "nunique"),
        )
        .reset_index()
        .sort_values("week")
    )
    return {
        "weeks": grouped["week"].tolist(),
        "confidence": [round(float(value), 4) for value in grouped["confidence"]],
        "flagged": [round(float(value), 4) for value in grouped["flagged"]],
        "mentions": [int(value) for value in grouped["mentions"]],
        "unique_entities": [int(value) for value in grouped["unique_entities"]],
    }


def build_trend_figure(trends: dict) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trends["weeks"],
            y=trends["confidence"],
            name="Mean confidence",
            mode="lines+markers",
            line=dict(color="#2563EB", width=3),
        )
    )
    fig.add_trace(
        go.Bar(
            x=trends["weeks"],
            y=trends["flagged"],
            name="Flagged %",
            marker_color="#EA580C",
            opacity=0.72,
            yaxis="y2",
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#0F172A"),
        margin=dict(l=24, r=24, t=16, b=28),
        height=310,
        legend=dict(orientation="h", y=1.12, x=0),
        yaxis=dict(title="Confidence", range=[0, 1], gridcolor="#D8E0EA"),
        yaxis2=dict(title="Flagged", overlaying="y", side="right", range=[0, 1], tickformat=".0%"),
        xaxis=dict(gridcolor="#E8EEF5"),
    )
    return fig


def build_type_figure(df: pd.DataFrame) -> go.Figure:
    counts = df.groupby("type")["entity"].count().reset_index().sort_values("entity", ascending=False)
    fig = go.Figure(
        go.Bar(
            x=counts["type"].tolist(),
            y=counts["entity"].tolist(),
            marker_color=[TYPE_COLORS.get(value, "#64748B") for value in counts["type"]],
            text=counts["entity"].tolist(),
            textposition="outside",
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#0F172A"),
        margin=dict(l=24, r=24, t=16, b=28),
        height=310,
        yaxis=dict(title="Mentions", gridcolor="#E8EEF5"),
        xaxis=dict(title="Entity type"),
    )
    return fig


def top_delta(all_windows: list[dict], start_index: int, end_index: int) -> dict:
    before = {node["id"]: node for node in all_windows[start_index]["nodes"]}
    after = {node["id"]: node for node in all_windows[end_index]["nodes"]}
    new = [after[key] for key in after.keys() - before.keys()]
    dropped = [before[key] for key in before.keys() - after.keys()]
    common = []
    for key in after.keys() & before.keys():
        delta = after[key]["mentions"] - before[key]["mentions"]
        if delta:
            item = dict(after[key])
            item["delta"] = delta
            common.append(item)
    return {
        "new": sorted(new, key=lambda item: item["mentions"], reverse=True)[:12],
        "dropped": sorted(dropped, key=lambda item: item["mentions"], reverse=True)[:12],
        "risers": sorted(common, key=lambda item: item["delta"], reverse=True)[:12],
    }


def build_dashboard_payload(df: pd.DataFrame, edges: pd.DataFrame) -> dict:
    weeks = sorted(df["week"].unique().tolist()) if not df.empty else []
    windows = [summarize_window(df, edges, weeks, index) for index in range(len(weeks))]
    positions = global_layout(windows)
    for window in windows:
        for node in window["nodes"]:
            node["x"], node["y"] = positions.get(node["id"], [0.0, 0.0])
    return {
        "weeks": weeks,
        "windows": windows,
        "trends": weekly_trends(df),
        "typeColors": TYPE_COLORS,
        "typeLabels": TYPE_LABELS,
        "defaultStart": 0,
        "defaultEnd": max(0, len(weeks) - 1),
    }


def render_dashboard(payload: dict, trend_html: str, type_html: str) -> str:
    payload_json = json.dumps(payload)
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI News Knowledge Graph</title>
  <style>
    :root {{
      --ink: #102033;
      --muted: #66758A;
      --line: #D9E2EC;
      --panel: rgba(255, 255, 255, 0.86);
      --blue: #2563EB;
      --green: #16A34A;
      --orange: #EA580C;
      --purple: #9333EA;
      --bg1: #F6F1E8;
      --bg2: #E6F1F7;
      --bg3: #F4E7D3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Aptos", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(234, 88, 12, 0.14), transparent 34rem),
        radial-gradient(circle at 85% 5%, rgba(37, 99, 235, 0.16), transparent 30rem),
        linear-gradient(135deg, var(--bg1), var(--bg2) 50%, var(--bg3));
      min-height: 100vh;
    }}
    .shell {{ max-width: 1440px; margin: 0 auto; padding: 28px; }}
    .hero {{
      display: grid;
      grid-template-columns: 1.3fr 0.7fr;
      gap: 18px;
      align-items: stretch;
      margin-bottom: 18px;
    }}
    .hero-card, .panel, .metric {{
      background: var(--panel);
      border: 1px solid rgba(255,255,255,0.72);
      border-radius: 28px;
      box-shadow: 0 24px 80px rgba(46, 63, 86, 0.16);
      backdrop-filter: blur(18px);
    }}
    .hero-card {{ padding: 30px; overflow: hidden; position: relative; }}
    .hero-card:after {{
      content: "";
      position: absolute;
      right: -80px;
      top: -100px;
      width: 280px;
      height: 280px;
      border-radius: 999px;
      background: conic-gradient(from 180deg, var(--blue), var(--green), var(--orange), var(--blue));
      opacity: 0.16;
    }}
    h1 {{ font-size: clamp(2rem, 4vw, 4.8rem); line-height: 0.94; margin: 0 0 16px; letter-spacing: -0.06em; }}
    h2 {{ margin: 0 0 14px; font-size: 1.2rem; letter-spacing: -0.02em; }}
    .eyebrow {{ color: var(--orange); font-weight: 800; text-transform: uppercase; letter-spacing: .16em; font-size: .75rem; }}
    .subhead {{ color: var(--muted); max-width: 780px; font-size: 1.02rem; line-height: 1.6; }}
    .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .metric {{ padding: 18px; }}
    .metric span {{ color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; }}
    .metric strong {{ display: block; font-size: 2rem; margin-top: 8px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 18px; }}
    .panel {{ padding: 20px; min-width: 0; }}
    .panel.wide {{ grid-column: 1 / -1; }}
    .controls {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 14px; }}
    .filter-grid {{ display: grid; grid-template-columns: 1fr 1fr 1.2fr; gap: 14px; margin-bottom: 14px; }}
    label {{ display: block; color: var(--muted); font-weight: 700; font-size: .84rem; margin-bottom: 8px; }}
    input[type=range] {{ width: 100%; accent-color: var(--blue); }}
    .range-card {{ border: 1px solid var(--line); border-radius: 18px; padding: 14px; background: rgba(255,255,255,0.62); }}
    .switches {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }}
    .switches label {{ margin: 0; display: inline-flex; align-items: center; gap: 8px; color: var(--ink); }}
    .type-filters {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .type-filter {{
      border: 1px solid var(--line);
      background: white;
      border-radius: 999px;
      padding: 7px 11px;
      font-weight: 800;
      color: var(--muted);
      cursor: pointer;
      transition: transform .15s ease, opacity .15s ease, box-shadow .15s ease;
    }}
    .type-filter.active {{ color: white; box-shadow: 0 8px 20px rgba(37, 99, 235, .18); }}
    .type-filter:hover {{ transform: translateY(-1px); }}
    .graphs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .graph-box {{ border: 1px solid var(--line); border-radius: 20px; overflow: hidden; background: #FBFCFE; }}
    .graph-title {{ padding: 12px 14px; border-bottom: 1px solid var(--line); font-weight: 800; display: flex; justify-content: space-between; }}
    .plot {{ height: 570px; }}
    .hot-list, .delta-list {{ display: grid; gap: 10px; }}
    .topic, .delta-item {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.68);
    }}
    .topic b, .delta-item b {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .topic small, .delta-item small {{ color: var(--muted); }}
    .pill {{ display: inline-flex; align-items:center; border-radius: 999px; padding: 4px 9px; color: white; font-size: .74rem; font-weight: 800; }}
    .delta-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .legend {{ display:flex; flex-wrap:wrap; gap: 10px; margin-top: 10px; }}
    .legend span {{ display:inline-flex; gap: 6px; align-items:center; color: var(--muted); font-size: .86rem; }}
    .swatch {{ width: 11px; height: 11px; border-radius: 999px; display:inline-block; }}
    .note {{ color: var(--muted); font-size: .9rem; line-height: 1.5; }}
    .edge-note {{
      background: rgba(16, 32, 51, .06);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 12px 14px;
      margin: 12px 0 14px;
      color: var(--muted);
      font-size: .9rem;
      line-height: 1.48;
    }}
    @media (max-width: 980px) {{
      .hero, .grid, .graphs, .controls, .filter-grid, .delta-grid {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: 1fr 1fr; }}
      .shell {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-card">
        <div class="eyebrow">AI News Entity Intelligence</div>
        <h1>See the topics moving through the AI news cycle.</h1>
        <p class="subhead">A local-first knowledge graph dashboard for hot topics, model confidence, and week-over-week entity movement. Generated {html.escape(generated_at)} from local pipeline outputs.</p>
        <div class="legend" id="legend"></div>
      </div>
      <div class="metrics" id="metrics"></div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Hot Topics</h2>
        <div class="hot-list" id="hotTopics"></div>
      </div>
      <div class="panel">
        <h2>Entity Type Mix</h2>
        {type_html}
      </div>
      <div class="panel wide">
        <h2>Confidence And Drift Trend</h2>
        {trend_html}
      </div>
    </section>

    <section class="panel wide">
      <h2>Graph Comparison</h2>
      <p class="note">Use the two sliders to compare cumulative graph state across time. Each network keeps stable node positions, so movement in density, new clusters, and missing topics is easier to spot. Scroll to zoom, drag to pan, and hover for entity details.</p>
      <div class="edge-note"><b>How to read connections:</b> every edge means two entities were mentioned in the same article. Thicker/darker edges mean repeated co-mentions across more articles in the selected timeframe. Use the edge-strength filter to hide weak one-off connections.</div>
      <div class="controls">
        <div class="range-card">
          <label for="leftRange">Baseline timeframe: <span id="leftLabel"></span></label>
          <input id="leftRange" type="range" min="0" max="0" value="0" />
        </div>
        <div class="range-card">
          <label for="rightRange">Comparison timeframe: <span id="rightLabel"></span></label>
          <input id="rightRange" type="range" min="0" max="0" value="0" />
        </div>
      </div>
      <div class="filter-grid">
        <div class="range-card">
          <label for="minMentions">Only show nodes with at least <span id="minMentionsLabel">1</span> mention(s)</label>
          <input id="minMentions" type="range" min="1" max="1" value="1" />
        </div>
        <div class="range-card">
          <label for="minEdgeWeight">Only show edges with at least <span id="minEdgeLabel">1</span> co-mention(s)</label>
          <input id="minEdgeWeight" type="range" min="1" max="1" value="1" />
        </div>
        <div class="range-card">
          <label>Graph interpretation controls</label>
          <div class="switches">
            <label><input id="labelToggle" type="checkbox" checked /> Display labels</label>
            <label><input id="newOnlyToggle" type="checkbox" /> Only newly added nodes</label>
          </div>
          <div class="type-filters" id="typeFilters" style="margin-top:12px;"></div>
        </div>
      </div>
      <div class="graphs">
        <div class="graph-box">
          <div class="graph-title"><span id="leftTitle"></span><span id="leftStats"></span></div>
          <div id="leftGraph" class="plot"></div>
        </div>
        <div class="graph-box">
          <div class="graph-title"><span id="rightTitle"></span><span id="rightStats"></span></div>
          <div id="rightGraph" class="plot"></div>
        </div>
      </div>
    </section>

    <section class="panel wide" style="margin-top:18px;">
      <h2>What Changed?</h2>
      <div class="delta-grid">
        <div><h2>New In Comparison</h2><div class="delta-list" id="newList"></div></div>
        <div><h2>Biggest Risers</h2><div class="delta-list" id="riserList"></div></div>
        <div><h2>Dropped Since Baseline</h2><div class="delta-list" id="dropList"></div></div>
      </div>
    </section>
  </div>

  <script>
    window.dashboardData = {payload_json};
  </script>
  <script>
    const data = window.dashboardData;
    const colors = data.typeColors;
    const weeks = data.weeks;
    const leftRange = document.getElementById("leftRange");
    const rightRange = document.getElementById("rightRange");
    const minMentions = document.getElementById("minMentions");
    const minEdgeWeight = document.getElementById("minEdgeWeight");
    const labelToggle = document.getElementById("labelToggle");
    const newOnlyToggle = document.getElementById("newOnlyToggle");
    const activeTypes = new Set(Object.keys(colors));

    function fmtPct(value) {{ return `${{Math.round((value || 0) * 100)}}%`; }}
    function fmt(value) {{ return Number(value || 0).toLocaleString(); }}
    function colorFor(type) {{ return colors[type] || "#64748B"; }}
    function pill(type) {{ return `<span class="pill" style="background:${{colorFor(type)}}">${{type}}</span>`; }}

    function setMetrics(index) {{
      const kpis = data.windows[index].kpis;
      const items = [
        ["Entities", fmt(kpis.entities)],
        ["Mentions", fmt(kpis.mentions)],
        ["Articles", fmt(kpis.articles)],
        ["Batches", fmt(kpis.batches)],
        ["Confidence", fmtPct(kpis.mean_confidence)],
        ["Flagged", fmtPct(kpis.flagged_pct)]
      ];
      document.getElementById("metrics").innerHTML = items.map(([label, value]) => `<div class="metric"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");
    }}

    function setLegend() {{
      document.getElementById("legend").innerHTML = Object.entries(data.typeLabels).map(([type, label]) => `<span><i class="swatch" style="background:${{colorFor(type)}}"></i>${{type}} - ${{label}}</span>`).join("");
    }}

    function setTypeFilters() {{
      const container = document.getElementById("typeFilters");
      container.innerHTML = Object.keys(colors).map(type => `<button class="type-filter active" data-type="${{type}}" style="background:${{colorFor(type)}};border-color:${{colorFor(type)}}">${{type}}</button>`).join("");
      container.querySelectorAll("button").forEach(button => {{
        button.addEventListener("click", () => {{
          const type = button.dataset.type;
          if (activeTypes.has(type)) {{
            activeTypes.delete(type);
            button.classList.remove("active");
            button.style.background = "white";
          }} else {{
            activeTypes.add(type);
            button.classList.add("active");
            button.style.background = colorFor(type);
          }}
          update();
        }});
      }});
    }}

    function maxMentionCount() {{
      return Math.max(1, ...data.windows.flatMap(window => window.nodes.map(node => node.mentions)));
    }}

    function maxEdgeCount() {{
      return Math.max(1, ...data.windows.flatMap(window => window.edges.map(edge => edge.weight)));
    }}

    function filteredNodes(windowData, index, baselineIndex) {{
      const baselineIds = new Set(data.windows[baselineIndex].nodes.map(node => node.id));
      return windowData.nodes.filter(node => {{
        if (!activeTypes.has(node.type)) return false;
        if (node.mentions < Number(minMentions.value)) return false;
        if (newOnlyToggle.checked && index !== baselineIndex && baselineIds.has(node.id)) return false;
        if (newOnlyToggle.checked && index === baselineIndex && node.first_week !== weeks[index]) return false;
        return true;
      }});
    }}

    function networkTraces(windowData, index, baselineIndex) {{
      const visibleNodes = filteredNodes(windowData, index, baselineIndex);
      const nodeById = Object.fromEntries(visibleNodes.map(node => [node.id, node]));
      const edgeX = [], edgeY = [], edgeText = [];
      windowData.edges.forEach(edge => {{
        if (edge.weight < Number(minEdgeWeight.value)) return;
        const source = nodeById[edge.source];
        const target = nodeById[edge.target];
        if (!source || !target) return;
        edgeX.push(source.x, target.x, null);
        edgeY.push(source.y, target.y, null);
        const text = `${{edge.source}} <-> ${{edge.target}}<br>${{edge.weight}} shared article co-mention(s)`;
        edgeText.push(text, text, text);
      }});
      const edgeTrace = {{
        x: edgeX,
        y: edgeY,
        text: edgeText,
        mode: "lines",
        line: {{ color: "rgba(83, 102, 128, 0.34)", width: 1.6 }},
        hovertemplate: "%{{text}}<extra>Connection</extra>",
        type: "scatter"
      }};
      const traces = [edgeTrace];
      Object.keys(colors).forEach(type => {{
        if (!activeTypes.has(type)) return;
        const nodes = visibleNodes.filter(node => node.type === type);
        if (!nodes.length) return;
        traces.push({{
          x: nodes.map(node => node.x),
          y: nodes.map(node => node.y),
          text: nodes.map(node => node.id),
          customdata: nodes.map(node => [node.type, node.mentions, node.confidence, node.articles, node.first_week, node.flagged]),
          mode: labelToggle.checked ? "markers+text" : "markers",
          type: "scatter",
          name: type,
          textposition: "top center",
          textfont: {{ size: 10, color: "#172033" }},
          marker: {{
            color: colorFor(type),
            size: nodes.map(node => Math.min(42, 10 + Math.sqrt(node.mentions) * 6)),
            line: {{ color: "white", width: 1.6 }},
            opacity: 0.88
          }},
          hovertemplate: "<b>%{{text}}</b><br>Type: %{{customdata[0]}}<br>Mentions: %{{customdata[1]}}<br>Confidence: %{{customdata[2]:.2f}}<br>Articles: %{{customdata[3]}}<br>First seen: %{{customdata[4]}}<br>Flagged: %{{customdata[5]:.0%}}<extra></extra>"
        }});
      }});
      return traces;
    }}

    function graphLayout() {{
      return {{
        margin: {{ l: 6, r: 6, t: 6, b: 6 }},
        paper_bgcolor: "#FBFCFE",
        plot_bgcolor: "#FBFCFE",
        showlegend: false,
        xaxis: {{ visible: false, fixedrange: false }},
        yaxis: {{ visible: false, fixedrange: false, scaleanchor: "x", scaleratio: 1 }},
        dragmode: "pan"
      }};
    }}

    function plotNetwork(target, index, baselineIndex) {{
      Plotly.react(target, networkTraces(data.windows[index], index, baselineIndex), graphLayout(), {{ responsive: true, scrollZoom: true, displaylogo: false }});
    }}

    function itemHtml(item, extra = "") {{
      return `<div class="delta-item"><div><b title="${{item.id}}">${{item.id}}</b><small>${{pill(item.type)}} ${{item.mentions}} mentions ${{extra}}</small></div><strong>${{fmtPct(item.confidence)}}</strong></div>`;
    }}

    function hotHtml(item) {{
      return `<div class="topic"><div><b title="${{item.id}}">${{item.id}}</b><small>${{pill(item.type)}} ${{item.mentions}} mentions across ${{item.articles}} articles</small></div><strong>${{fmtPct(item.confidence)}}</strong></div>`;
    }}

    function setHotTopics(index) {{
      document.getElementById("hotTopics").innerHTML = data.windows[index].hot.slice(0, 12).map(hotHtml).join("") || "<p class='note'>No topics found.</p>";
    }}

    function computeDelta(leftIndex, rightIndex) {{
      const left = Object.fromEntries(data.windows[leftIndex].nodes.map(node => [node.id, node]));
      const right = Object.fromEntries(data.windows[rightIndex].nodes.map(node => [node.id, node]));
      const leftKeys = new Set(Object.keys(left));
      const rightKeys = new Set(Object.keys(right));
      const newer = [...rightKeys].filter(key => !leftKeys.has(key)).map(key => right[key]).sort((a,b) => b.mentions - a.mentions).slice(0, 10);
      const dropped = [...leftKeys].filter(key => !rightKeys.has(key)).map(key => left[key]).sort((a,b) => b.mentions - a.mentions).slice(0, 10);
      const risers = [...rightKeys].filter(key => leftKeys.has(key)).map(key => ({{...right[key], delta: right[key].mentions - left[key].mentions}})).filter(item => item.delta > 0).sort((a,b) => b.delta - a.delta).slice(0, 10);
      document.getElementById("newList").innerHTML = newer.map(item => itemHtml(item)).join("") || "<p class='note'>No new entities.</p>";
      document.getElementById("dropList").innerHTML = dropped.map(item => itemHtml(item)).join("") || "<p class='note'>No dropped entities.</p>";
      document.getElementById("riserList").innerHTML = risers.map(item => itemHtml(item, `+${{item.delta}}`)).join("") || "<p class='note'>No risers.</p>";
    }}

    function update() {{
      const leftIndex = Number(leftRange.value);
      const rightIndex = Number(rightRange.value);
      document.getElementById("leftLabel").textContent = weeks[leftIndex];
      document.getElementById("rightLabel").textContent = weeks[rightIndex];
      document.getElementById("leftTitle").textContent = `Up to ${{weeks[leftIndex]}}`;
      document.getElementById("rightTitle").textContent = `Up to ${{weeks[rightIndex]}}`;
      document.getElementById("leftStats").textContent = `${{fmt(filteredNodes(data.windows[leftIndex], leftIndex, leftIndex).length)}} visible / ${{fmt(data.windows[leftIndex].kpis.entities)}} total`;
      document.getElementById("rightStats").textContent = `${{fmt(filteredNodes(data.windows[rightIndex], rightIndex, leftIndex).length)}} visible / ${{fmt(data.windows[rightIndex].kpis.entities)}} total`;
      document.getElementById("minMentionsLabel").textContent = minMentions.value;
      document.getElementById("minEdgeLabel").textContent = minEdgeWeight.value;
      plotNetwork("leftGraph", leftIndex, leftIndex);
      plotNetwork("rightGraph", rightIndex, leftIndex);
      setMetrics(rightIndex);
      setHotTopics(rightIndex);
      computeDelta(leftIndex, rightIndex);
    }}

    function init() {{
      setLegend();
      setTypeFilters();
      leftRange.max = Math.max(0, weeks.length - 1);
      rightRange.max = Math.max(0, weeks.length - 1);
      minMentions.max = maxMentionCount();
      minEdgeWeight.max = maxEdgeCount();
      leftRange.value = data.defaultStart;
      rightRange.value = data.defaultEnd;
      leftRange.addEventListener("input", update);
      rightRange.addEventListener("input", update);
      minMentions.addEventListener("input", update);
      minEdgeWeight.addEventListener("input", update);
      labelToggle.addEventListener("change", update);
      newOnlyToggle.addEventListener("change", update);
      update();
    }}

    init();
  </script>
</body>
</html>"""


def save_dashboard(base_dir: str, open_browser: bool = False) -> pathlib.Path:
    batches = load_batches(base_dir)
    if not batches:
        raise SystemExit(f"No entity batches found in {base_dir}/entities. Run NER.py first.")

    df = flatten_entities(batches)
    edges = build_edges(df)
    payload = build_dashboard_payload(df, edges)
    type_html = to_html(build_type_figure(df), include_plotlyjs=True, full_html=False, config={"displaylogo": False, "responsive": True})
    trend_html = to_html(build_trend_figure(payload["trends"]), include_plotlyjs=False, full_html=False, config={"displaylogo": False, "responsive": True})
    document = render_dashboard(payload, trend_html, type_html)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"dashboard_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(document, encoding="utf-8")
    print(f"[OK] Dashboard saved -> {path}")
    if open_browser:
        webbrowser.open(f"file:///{path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build polished local graph dashboard.")
    parser.add_argument("--dir", default=LOCAL_DIR, help="Local data directory")
    parser.add_argument("--open", action="store_true", help="Open the dashboard after saving")
    args = parser.parse_args()
    save_dashboard(args.dir, open_browser=args.open)


if __name__ == "__main__":
    main()
