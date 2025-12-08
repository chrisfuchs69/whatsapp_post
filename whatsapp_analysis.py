#!/usr/bin/env python3
"""
whatsapp_report_modular.py

Modular WhatsApp interactive report generator (Plotly)
Saves a single file: whatsapp_report.html by default.

Usage:
    python whatsapp_report_modular.py --input chat_complete.txt --output whatsapp_report.html
    python whatsapp_report_modular.py --input chat_complete.txt --plots total_emojis,emoji_heatmap
"""

from __future__ import annotations
import re
import argparse
import emoji
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import networkx as nx
from wordcloud import WordCloud, STOPWORDS
from io import BytesIO
from PIL import Image
import base64
import plotly.io as pio
from typing import Dict, List, Optional, Tuple

# ---------- Defaults / Config ----------
DEFAULT_INPUT = "chat_complete.txt"
DEFAULT_OUTPUT = "whatsapp_report.html"
TOP_EMOJI_COUNT = 20

# ---------- Parsing helpers ----------
LINE_RE = re.compile(r'^\[(\d{2}\.\d{2}\.\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+): (.+)$')

def extract_emojis(text: str) -> List[str]:
    """Return list of emojis in text using python-emoji (handles multi-codepoint)."""
    try:
        return [e["emoji"] for e in emoji.emoji_list(text)]
    except Exception:
        return []

def hour_fraction(dt: datetime) -> float:
    return dt.hour + dt.minute/60.0 + dt.second/3600.0

def apply_font_sizes(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        title_font=dict(size=20),
        xaxis_title_font=dict(size=16),
        yaxis_title_font=dict(size=16),
        xaxis_tickfont=dict(size=14),
        yaxis_tickfont=dict(size=14),
        legend=dict(font=dict(size=14))
    )
    return fig

# ---------- I/O & parsing ----------
def parse_chat_file(path: str) -> pd.DataFrame:
    """
    Parse WhatsApp export file with expected line format:
    [23.11.23, 06:53:39] Name: Message
    Returns a DataFrame with columns: datetime, user, message
    """
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = LINE_RE.match(line)
            if not m:
                # skip non-matching lines (continuation lines are ignored)
                continue
            date_s, time_s, user, msg = m.groups()
            # parse year with two-digit year
            dt = datetime.strptime(f"{date_s} {time_s}", "%d.%m.%y %H:%M:%S")
            rows.append({"datetime": dt, "user": user.strip(), "message": msg})
    if not rows:
        raise SystemExit(f"No messages parsed from {path} — check file and format.")
    df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
    return df

# ---------- Data enrichment ----------
def enrich_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Add columns used by plots."""
    df = df.copy()
    df["date"] = df["datetime"].dt.date
    df["hour"] = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday  # Monday=0
    df["msg_length"] = df["message"].str.len()
    df["emojis"] = df["message"].apply(extract_emojis)
    df["emoji_count"] = df["emojis"].apply(len)
    return df

# ---------- Aggregations ----------
def compute_emoji_counts(df: pd.DataFrame, top_n: int = TOP_EMOJI_COUNT):
    users = df["user"].unique().tolist()

    # emoji occurrences exploded
    emoji_all = df.explode("emojis").dropna(subset=["emojis"])

    # total emoji counts per user
    total_emoji_counts = df.groupby("user")["emoji_count"].sum().reindex(users).fillna(0).astype(int)

    # top emojis overall
    if not emoji_all.empty:
        top_emojis = emoji_all["emojis"].value_counts().nlargest(top_n)
    else:
        top_emojis = pd.Series(dtype=int)

    # user -> Counter of emojis
    emoji_per_user = emoji_all.groupby("user")["emojis"].value_counts()
    user_emojis = {}
    for user in users:
        try:
            s = emoji_per_user.loc[user]
            user_emojis[user] = Counter(s.to_dict())
        except Exception:
            user_emojis[user] = Counter()

    # emoji by day per user
    emoji_by_day_user = (df.explode("emojis")
                           .dropna(subset=["emojis"])
                           .groupby(["user","date"])
                           .size()
                           .reset_index(name="emoji_count_per_day"))
    return {
        "users": users,
        "emoji_all": emoji_all,
        "total_emoji_counts": total_emoji_counts,
        "top_emojis": top_emojis,
        "user_emojis": user_emojis,
        "emoji_by_day_user": emoji_by_day_user
    }

def compute_time_series_and_heatmaps(df: pd.DataFrame):
    # emoji time rows
    emoji_time_rows = []
    for idx, row in df.iterrows():
        if row["emoji_count"] == 0:
            continue
        hf = hour_fraction(row["datetime"])
        for em in row["emojis"]:
            emoji_time_rows.append({"user": row["user"], "emoji": em, "hour_frac": hf})
    df_emoji_time = pd.DataFrame(emoji_time_rows)

    # heatmap: emojis by hour and weekday
    heat = df.explode("emojis").dropna(subset=["emojis"])
    heat_table = heat.groupby(["hour","weekday"]).size().unstack(fill_value=0)

    # message activity heatmap
    msg_heat_table = df.groupby(["hour","weekday"]).size().unstack(fill_value=0)

    # message and emoji per day
    daily_counts = df.groupby("date").agg(
        messages=("message", "count"),
        emojis=("emoji_count", "sum")
    ).reset_index().sort_values("date")
    daily_counts['messages_trend'] = daily_counts['messages'].rolling(window=7, min_periods=1, center=True).mean()
    daily_counts['emojis_trend'] = daily_counts['emojis'].rolling(window=7, min_periods=1, center=True).mean()

    return {
        "df_emoji_time": df_emoji_time,
        "heat_table": heat_table,
        "msg_heat_table": msg_heat_table,
        "daily_counts": daily_counts
    }

def compute_response_times(df: pd.DataFrame) -> Dict[str, List[float]]:
    response_times = defaultdict(list)
    last_ts_per_user = {}
    for idx, row in df.iterrows():
        user = row["user"]
        ts = row["datetime"]
        if user in last_ts_per_user:
            diff_min = (ts - last_ts_per_user[user]).total_seconds() / 60.0
            if 0 < diff_min < 60*24:
                response_times[user].append(diff_min)
        last_ts_per_user[user] = ts
    avg_response_time = {user: (np.mean(response_times[user]) if response_times[user] else np.nan)
                         for user in df["user"].unique().tolist()}
    return response_times, avg_response_time

def build_emoji_cooccurrence_graph(df: pd.DataFrame):
    cooccurrence = Counter()
    for _, row in df.iterrows():
        ems = list(dict.fromkeys(row["emojis"]))  # unique emojis preserving order
        if len(ems) > 1:
            for a,b in combinations(sorted(ems), 2):
                cooccurrence[(a,b)] += 1
    G = nx.Graph()
    for (a,b), w in cooccurrence.items():
        G.add_edge(a,b,weight=w)
    emoji_all = df.explode("emojis").dropna(subset=["emojis"])
    node_freq = emoji_all["emojis"].value_counts().to_dict()
    for n in G.nodes():
        G.nodes[n]["freq"] = node_freq.get(n, 1)
    pos = {}
    if len(G) > 0:
        pos = nx.spring_layout(G, k=0.5, iterations=100, seed=42)
    return G, pos, node_freq

def generate_wordcloud_images(df: pd.DataFrame) -> Dict[str, str]:
    wordcloud_images = {}
    users = df["user"].unique().tolist()
    for user in users:
        text = " ".join(df.loc[df["user"]==user, "message"].tolist())
        if not text.strip():
            continue
        stopwords = set(STOPWORDS)
        wc = WordCloud(width=800, height=400, background_color="white",
                       stopwords=stopwords, collocations=False).generate(text)
        img = wc.to_image()
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        wordcloud_images[user] = f"data:image/png;base64,{b64}"
    return wordcloud_images

# ---------- Plot functions (each returns a Plotly Figure or None) ----------
def plot_total_emojis_per_user(total_emoji_counts: pd.Series) -> Optional[go.Figure]:
    fig = px.bar(
        x=total_emoji_counts.index.astype(str),
        y=total_emoji_counts.values,
        labels={'x':'User','y':'Total Emojis'},
        title='Du natürlich :)'
    )
    return fig

def plot_emoji_time_violin(df_emoji_time: pd.DataFrame) -> Optional[go.Figure]:
    if df_emoji_time.empty:
        return None
    violin_df = df_emoji_time.copy()
    fig = px.violin(
        violin_df, x="user", y="hour_frac", points="all",
        labels={'hour_frac':'Hour of day','user':'User'},
        title='Emoji Usage Time of Day (Violin plot)'
    )
    fig.update_yaxes(range=[0,24])
    return fig

def plot_emoji_usage_over_time(emoji_by_day_user: pd.DataFrame) -> Optional[go.Figure]:
    if emoji_by_day_user.empty:
        return None
    pivot = emoji_by_day_user.pivot(index="date", columns="user", values="emoji_count_per_day").fillna(0)
    fig = go.Figure()
    for user in pivot.columns:
        fig.add_trace(go.Scatter(x=pivot.index, y=pivot[user], mode='lines+markers', name=user))
    fig.update_layout(title="Emoji Usage Over Time (per day)", xaxis_title="Date", yaxis_title="Emoji count")
    fig.update_yaxes(type="log")
    return fig

def plot_top_emojis(top_emojis: pd.Series) -> Optional[go.Figure]:
    if top_emojis.empty:
        return None
    labels = top_emojis.index.astype(str)
    values = top_emojis.values
    fig = px.bar(
        x=values[::-1],
        text=labels[::-1],
        orientation='h',
        labels={'x':'Count','y':'Emoji'},
        title=f"Top {len(labels)} Emojis Overall"
    )
    fig.update_traces(textfont_size=26, textposition="outside")
    return fig

def plot_emoji_heatmap(heat_table: pd.DataFrame) -> go.Figure:
    heat_mat = heat_table.reindex(index=range(24), columns=range(7), fill_value=0)
    fig = go.Figure(data=go.Heatmap(
        z=heat_mat.values,
        x=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        y=[f"{h}:00" for h in heat_mat.index],
        colorscale="YlGnBu",
        colorbar=dict(title="Emoji count")
    ))
    fig.update_layout(title="Emoji Heatmap by Hour and Day of Week")
    fig.update_yaxes(autorange='reversed')
    return fig

def plot_message_heatmap(msg_heat_table: pd.DataFrame) -> go.Figure:
    msg_mat = msg_heat_table.reindex(index=range(24), columns=range(7), fill_value=0)
    fig = go.Figure(data=go.Heatmap(
        z=msg_mat.values,
        x=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        y=[f"{h}:00" for h in msg_mat.index],
        colorscale="Plasma",
        colorbar=dict(title="Messages")
    ))
    fig.update_layout(title="Message Activity Heatmap by Hour and Day of Week")
    fig.update_yaxes(autorange='reversed')
    return fig

def plot_message_length(df: pd.DataFrame) -> go.Figure:
    users = df["user"].unique().tolist()
    fig = go.Figure()
    for user in users:
        lengths = df.loc[df['user']==user,'msg_length']
        if len(lengths) == 0:
            continue
        fig.add_trace(go.Violin(x=[user]*len(lengths), y=lengths, name=user, box_visible=True, meanline_visible=True))
    fig.update_layout(title="Message Length Distribution per User", yaxis_title="Characters")
    fig.update_yaxes(type="log")
    return fig

def plot_response_time_hist(response_times: Dict[str, List[float]]) -> go.Figure:
    fig = go.Figure()
    for user, vals in response_times.items():
        if not vals:
            continue
        fig.add_trace(go.Histogram(x=vals, name=user, opacity=0.6))
    fig.update_layout(barmode='overlay', title="Response Time Distribution per User (minutes)", xaxis_title="Minutes")
    return fig

def plot_avg_response_time(avg_response_time: Dict[str, float]) -> go.Figure:
    avg_df = pd.DataFrame({"user": list(avg_response_time.keys()), "avg_min": list(avg_response_time.values())})
    fig = px.bar(avg_df.sort_values("avg_min"), x="user", y="avg_min",
                 labels={"avg_min":"Avg response (min)","user":"User"},
                 title="Average Response Time per User (minutes)")
    return fig

def plot_emoji_network(G: nx.Graph, pos: dict) -> Optional[go.Figure]:
    if len(G) == 0:
        return None
    edge_x = []
    edge_y = []
    for u,v,data in G.edges(data=True):
        x0,y0 = pos[u]
        x1,y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    node_x = []
    node_y = []
    node_text = []
    node_size = []
    for n in G.nodes():
        x,y = pos[n]
        node_x.append(x)
        node_y.append(y)
        node_text.append(f"{n} (freq {G.nodes[n].get('freq', 1)})")
        node_size.append(max(20, G.nodes[n].get('freq', 1) * 6))

    edge_trace = go.Scatter(x=edge_x, y=edge_y, mode='lines', line=dict(width=3, color='#888'), hoverinfo='none')
    node_trace = go.Scatter(x=node_x, y=node_y, mode='markers+text',
                            text=[n for n in G.nodes()],
                            hovertext=node_text,
                            textposition="top center",
                            marker=dict(size=node_size, color='orange', line=dict(width=1)))
    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(title="Emoji Co-occurrence Network", showlegend=False)
    return fig

def plot_emoji_love_map(G: nx.Graph, node_freq: dict) -> Optional[go.Figure]:
    if len(G) == 0:
        return None
    TOP_N = 25
    top_emoji_nodes = sorted(node_freq.items(), key=lambda x: x[1], reverse=True)[:TOP_N]
    top_nodes = set([em for em, freq in top_emoji_nodes])
    subG = G.subgraph(top_nodes).copy()
    if len(subG) == 0:
        return None
    pos_sub = nx.spring_layout(subG, k=0.5, iterations=100, seed=42)

    edge_x = []
    edge_y = []
    for u, v, d in subG.edges(data=True):
        x0, y0 = pos_sub[u]
        x1, y1 = pos_sub[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        mode='lines',
        line=dict(color='pink', width=1),
        hoverinfo='none'
    )

    node_x = []
    node_y = []
    node_text = []
    node_size = []
    for n in subG.nodes():
        x, y = pos_sub[n]
        node_x.append(x)
        node_y.append(y)
        freq = node_freq.get(n, 1)
        node_text.append(f"{n} (freq {freq})")
        node_size.append(max(20, freq*1))

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode='markers+text',
        text=[n for n in subG.nodes()],
        textposition="middle center",
        textfont=dict(size=30),
        hovertext=node_text,
        hoverinfo='text',
        marker=dict(size=node_size, color='lightcoral', line=dict(width=2, color='darkred')),
    )

    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        title="Emoji Pairing 'Love' Map",
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        margin=dict(t=40, b=0, l=0, r=0),
        height=600,
    )
    return fig

def plot_emoji_sentiment_board(all_emoji_counts: Counter) -> go.Figure:
    # Minimal emoji sentiment dictionary, expand if you like
    emoji_sentiment = {
        '😀': 'Positive', '😃': 'Positive', '😂': 'Positive', '❤️': 'Positive', '👍': 'Positive',
        '😞': 'Negative', '😡': 'Negative', '😢': 'Negative', '👎': 'Negative',
        '😐': 'Neutral', '🤔': 'Neutral', '😶': 'Neutral'
    }
    sentiment_groups = {'Positive': [], 'Negative': [], 'Neutral': []}
    for em, cnt in all_emoji_counts.items():
        sentiment = emoji_sentiment.get(em, 'Neutral')
        sentiment_groups[sentiment].append((em, cnt))

    fig = go.Figure()
    x_offsets = {'Positive': -1, 'Neutral': 0, 'Negative': 1}
    y_base = 0
    for sentiment, emojis_list in sentiment_groups.items():
        for i, (em, cnt) in enumerate(emojis_list):
            fig.add_trace(go.Scatter(
                x=[x_offsets[sentiment] + i*0.05],
                y=[y_base],
                mode='text',
                text=[em],
                textfont=dict(size=10 + cnt*1),
                hoverinfo='text',
                hovertext=f"{em}: {cnt} times ({sentiment})"
            ))
        # Add sentiment label above
        fig.add_trace(go.Scatter(
            x=[x_offsets[sentiment]],
            y=[y_base + 0.3],
            mode='text',
            text=[sentiment],
            textfont=dict(size=20, family="Arial Black", color="black"),
            hoverinfo='skip'
        ))

    fig.update_layout(
        title="Emoji Sentiment Mood Board",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-2, 2]),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-0.5, 0.5]),
        height=300,
        margin=dict(t=40, b=20, l=20, r=20)
    )
    return fig

def plot_chat_timeline_snapshot(daily_counts: pd.DataFrame) -> go.Figure:
    fig = go.Figure(data=[
        go.Bar(name='Messages', x=daily_counts['date'], y=daily_counts['messages'], marker_color='blue'),
        go.Bar(name='Emojis', x=daily_counts['date'], y=daily_counts['emojis'], marker_color='orange'),
    ])
    fig.add_trace(go.Scatter(
        x=daily_counts['date'],
        y=daily_counts['messages_trend'],
        mode='lines',
        line=dict(color='blue', width=3, dash='dash'),
        name='Messages Trend'
    ))
    fig.add_trace(go.Scatter(
        x=daily_counts['date'],
        y=daily_counts['emojis_trend'],
        mode='lines',
        line=dict(color='orange', width=3, dash='dash'),
        name='Emojis Trend'
    ))
    fig.update_layout(
        barmode='group',
        title="Chat Timeline Snapshot: Messages & Emojis per Day",
        xaxis_title="Date",
        yaxis_title="Count",
        xaxis_tickformat='%d %b %Y',
        xaxis_tickangle=-45,
        legend_title_text="",
        template="simple_white"
    )
    return fig

# ---------- HTML assembly ----------
HTML_TEMPLATE_HEAD = """
<html>
<head>
<meta charset='utf-8'>
<title>WhatsApp Emoji Report</title>
<style>
@media print {
    .pagebreak {
        page-break-after: always;
    }
}
</style>
</head>
<body>
"""

def fig_to_html_div(fig):
    if fig is None:
        return "<p><i>No data for this plot.</i></p>"
    return pio.to_html(fig, full_html=False, include_plotlyjs='cdn')

def build_html_report(output_path: str,
                      df: pd.DataFrame,
                      users: List[str],
                      figs_map: Dict[str, Optional[go.Figure]],
                      wordcloud_images: Dict[str,str],
                      extra_info: dict):
    html_parts = []
    html_parts.append(HTML_TEMPLATE_HEAD)
    html_parts.append(f"<h1>WhatsApp Emoji & Chat Report</h1>")
    html_parts.append(f"<p>Messages parsed: {len(df)} · Users: {len(users)} · Date range: {df['datetime'].min()} — {df['datetime'].max()}</p>")

    # default ordered sections with keys mapping to figs_map
    sections = [
        ("Fangen wir künstlerisch an, wenn unser Chat jeweils ein Kunstwerk wäre", None),
        ("Also unser Verkehr hat über die Zeit zugenommen, aber was war denn am 26.10.2024 los gewesen - 234 Nachrichten?", "chat_timeline_snapshot"),
        ("Zu welcher Tageszeit in der Woche waren wir denn besonders fleißig am Texten?", 'message_heatmap'),
        ("...und am Emojis verschicken?", 'emoji_heatmap'),
        (f"Dabei waren unsere Top {TOP_EMOJI_COUNT} Emojis diese", 'top_emojis'),
        ("Aber wie lange musste ich denn immer so warten auf Antwort von dir? Ist da mehr orange als blau?", 'response_time_hist'),
        ("Jetzt schwarz auf weiß - eine ganz knappe Kiste, 10,1 Sekunden warte ich länger auf deine Antwort...", 'avg_response_time'),
        ("À propos länger, das scheint dir zu gefallen, thats what the statistics says", 'message_length'),
        ("Ich lenke ab, zurück zu den Emojis: Lecker ist gemeinsam mit Tims Sonnenbrille bei uns ziemlich isoliert von den anderen", "emoji_love_map"),
        ("Unser Spirit Animal ist auf jeden Fall Tim '😂'", "emoji_sentiment_board"),
        ("An den Tagen kann man es nicht mehr ablesen wer nun führt bzgl. der Emojis", 'emoji_usage_over_time'),
        ("Hier mal auf die Tageszeit im gemittelt, also vor 8 ist bei mir mit keinem Emoji zu rechnen", 'emoji_time_violin'),
        ("Hier jetzt schwarz auf weiß, wer hat denn nun die meisten Emojis versendet?", 'total_emojis_per_user'),
    ]

    first = True
    for title, key in sections:
        if not first:
            html_parts.append("<div class='pagebreak'></div>")
        first = False

        html_parts.append(f"<hr><h2>{title}</h2>")

        if key is None:
            # wordclouds
            for user, img_b64 in wordcloud_images.items():
                html_parts.append(f"<h3>{user}</h3>")
                html_parts.append(
                    f"<img src='{img_b64}' alt='Wordcloud for {user}' "
                    "style='max-width:100%;height:auto;"
                    "border:1px solid #ddd;padding:4px;margin-bottom:10px;'>"
                )
        else:
            fig_html = fig_to_html_div(figs_map.get(key))
            html_parts.append(fig_html)

    html_parts.append("</body></html>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))
    print(f"Report written to {output_path}. Open this file in a browser to explore the interactive plots.")

# ---------- Plot registry ----------
def build_plot_registry(prepared_data: dict) -> Dict[str, callable]:
    """
    prepared_data contains keys produced by compute_* functions and others.
    The registry maps string keys to callables that produce figures when invoked.
    """
    registry = {
        "total_emojis_per_user": lambda: plot_total_emojis_per_user(prepared_data["compute_emoji"]["total_emoji_counts"]),
        "emoji_time_violin": lambda: plot_emoji_time_violin(prepared_data["compute_time"]["df_emoji_time"]),
        "emoji_usage_over_time": lambda: plot_emoji_usage_over_time(prepared_data["compute_emoji"]["emoji_by_day_user"]),
        "top_emojis": lambda: plot_top_emojis(prepared_data["compute_emoji"]["top_emojis"]),
        "emoji_heatmap": lambda: plot_emoji_heatmap(prepared_data["compute_time"]["heat_table"]),
        "message_heatmap": lambda: plot_message_heatmap(prepared_data["compute_time"]["msg_heat_table"]),
        "message_length": lambda: plot_message_length(prepared_data["df"]),
        "response_time_hist": lambda: plot_response_time_hist(prepared_data["response_times"]),
        "avg_response_time": lambda: plot_avg_response_time(prepared_data["avg_response_time"]),
        "emoji_network": lambda: plot_emoji_network(prepared_data["G"], prepared_data["pos"]),
        "emoji_love_map": lambda: plot_emoji_love_map(prepared_data["G"], prepared_data["node_freq"]),
        "emoji_sentiment_board": lambda: plot_emoji_sentiment_board(prepared_data["all_emoji_counts"]),
        "chat_timeline_snapshot": lambda: plot_chat_timeline_snapshot(prepared_data["compute_time"]["daily_counts"]),
        # add more keys as needed
    }
    return registry

# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="WhatsApp interactive report generator (Plotly) — modular")
    parser.add_argument("--input", "-i", default=DEFAULT_INPUT, help="Path to Whatsapp export text file")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Path to output HTML file")
    parser.add_argument("--plots", "-p", default="", help="Comma-separated list of plots to generate (keys). If empty, generate all.")
    args = parser.parse_args()

    df = parse_chat_file(args.input)
    df = enrich_dataframe(df)

    # compute aggregates
    compute_emoji = compute_emoji_counts(df)
    compute_time = compute_time_series_and_heatmaps(df)
    response_times, avg_response_time = compute_response_times(df)
    G, pos, node_freq = build_emoji_cooccurrence_graph(df)
    wordcloud_images = generate_wordcloud_images(df)

    # all emoji counts combined
    all_emoji_counts = Counter()
    for user_counts in compute_emoji["user_emojis"].values():
        all_emoji_counts.update(user_counts)

    prepared_data = {
        "df": df,
        "users": df["user"].unique().tolist(),
        "compute_emoji": compute_emoji,
        "compute_time": compute_time,
        "response_times": response_times,
        "avg_response_time": avg_response_time,
        "G": G,
        "pos": pos,
        "node_freq": node_freq,
        "wordcloud_images": wordcloud_images,
        "all_emoji_counts": all_emoji_counts
    }

    # build registry and produce selected plots
    registry = build_plot_registry(prepared_data)

    # Decide which plots to produce
    if args.plots.strip():
        requested = [p.strip() for p in args.plots.split(",") if p.strip()]
    else:
        requested = list(registry.keys())  # all by default

    figs_map = {}
    # Validate requested keys and generate figures
    for key in requested:
        if key not in registry:
            print(f"[warning] Unknown plot key '{key}', skipping.")
            continue
        try:
            figs_map[key] = registry[key]()
        except Exception as e:
            print(f"[error] Failed to generate plot '{key}': {e}")
            figs_map[key] = None

    # ensure required keys exist in figs_map for HTML sections (use None if not)
    # Fill with None for keys referenced by HTML builder but not requested
    for k in ["message_heatmap","emoji_heatmap","top_emojis","response_time_hist",
              "avg_response_time","message_length","emoji_love_map","emoji_sentiment_board",
              "emoji_usage_over_time","emoji_time_violin","total_emojis_per_user","chat_timeline_snapshot"]:
        figs_map.setdefault(k, None)

    # Build HTML
    build_html_report(
        output_path=args.output,
        df=df,
        users=prepared_data["users"],
        figs_map={
            # map expected keys to figures
            "message_heatmap": figs_map.get("message_heatmap"),
            "emoji_heatmap": figs_map.get("emoji_heatmap"),
            "top_emojis": figs_map.get("top_emojis"),
            "response_time_hist": figs_map.get("response_time_hist"),
            "avg_response_time": figs_map.get("avg_response_time"),
            "message_length": figs_map.get("message_length"),
            "chat_timeline_snapshot": figs_map.get("chat_timeline_snapshot"),
            "emoji_love_map": figs_map.get("emoji_love_map"),
            "emoji_sentiment_board": figs_map.get("emoji_sentiment_board"),
            "emoji_usage_over_time": figs_map.get("emoji_usage_over_time"),
            "emoji_time_violin": figs_map.get("emoji_time_violin"),
            "total_emojis_per_user": figs_map.get("total_emojis_per_user"),
        },
        wordcloud_images=wordcloud_images,
        extra_info={}
    )

if __name__ == "__main__":
    main()

