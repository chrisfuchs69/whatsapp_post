#!/usr/bin/env python3
"""
Whatsapp interactive report generator (Plotly)
Saves a single file: whatsapp_report.html
"""

import re
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

# ---------- Config ----------
CHAT_FILE = "chat_complete.txt"
OUTPUT_HTML = "whatsapp_report.html"
TOP_EMOJI_COUNT = 20

# ---------- Helpers ----------
def extract_emojis(text):
    """Return list of emojis in text using python-emoji (handles multi-codepoint)."""
    return [e["emoji"] for e in emoji.emoji_list(text)]

# ---------- Parse chat ----------
# Expected line format: [23.11.23, 06:53:39] Name: Message
line_re = re.compile(r'^\[(\d{2}\.\d{2}\.\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+): (.+)$')

rows = []
with open(CHAT_FILE, 'r', encoding='utf-8') as f:
    for raw in f:
        line = raw.rstrip("\n")
        m = line_re.match(line)
        if not m:
            # skip non-matching lines (continuation lines not handled here)
            continue
        date_s, time_s, user, msg = m.groups()
        dt = datetime.strptime(f"{date_s} {time_s}", "%d.%m.%y %H:%M:%S")
        rows.append({"datetime": dt, "user": user.strip(), "message": msg})

if not rows:
    raise SystemExit(f"No messages parsed from {CHAT_FILE} — check file and format.")

df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)

# ---------- Derived columns ----------
df["date"] = df["datetime"].dt.date
df["hour"] = df["datetime"].dt.hour
df["weekday"] = df["datetime"].dt.weekday  # Monday=0
df["msg_length"] = df["message"].str.len()
df["emojis"] = df["message"].apply(extract_emojis)
df["emoji_count"] = df["emojis"].apply(len)

users = df["user"].unique().tolist()

# ---------- Emoji counts per user ----------
emoji_per_user = df.explode("emojis").dropna(subset=["emojis"]).groupby("user")["emojis"].value_counts()
total_emoji_counts = df.groupby("user")["emoji_count"].sum().reindex(users).fillna(0).astype(int)

# ---------- Top emojis overall ----------
emoji_all = df.explode("emojis").dropna(subset=["emojis"])
top_emojis = emoji_all["emojis"].value_counts().nlargest(TOP_EMOJI_COUNT)

# Build user_emojis dict: user -> Counter of emojis
user_emojis = {}
for user in users:
    user_emoji_counts = emoji_per_user.loc[user] if user in emoji_per_user.index.get_level_values(0) else pd.Series(dtype=int)
    user_emojis[user] = Counter(user_emoji_counts.to_dict())

# ---------- Emoji usage over time (per day) ----------
emoji_by_day_user = (df.explode("emojis")
                       .dropna(subset=["emojis"])
                       .groupby(["user","date"])
                       .size()
                       .reset_index(name="emoji_count_per_day"))

# ---------- Time-of-day distribution for emojis (violin uses hours with fractional minutes if desired) ----------
# For greater resolution, map each emoji to the fractional hour of the message
def hour_fraction(dt):
    return dt.hour + dt.minute/60.0 + dt.second/3600.0

emoji_time_rows = []
for idx, row in df.iterrows():
    if row["emoji_count"] == 0:
        continue
    hf = hour_fraction(row["datetime"])
    for em in row["emojis"]:
        emoji_time_rows.append({"user": row["user"], "emoji": em, "hour_frac": hf})

df_emoji_time = pd.DataFrame(emoji_time_rows)

# ---------- Heatmap: emojis by hour and weekday ----------
heat = df.explode("emojis").dropna(subset=["emojis"])
heat_table = heat.groupby(["hour","weekday"]).size().unstack(fill_value=0)

# ---------- Message activity heatmap (messages count) ----------
msg_heat_table = df.groupby(["hour","weekday"]).size().unstack(fill_value=0)

# ---------- Message length distribution ----------
# computed per user using df['msg_length']

# ---------- Response times per user (in minutes) and average response time ----------
response_times = defaultdict(list)
last_ts_per_user = {}

for idx, row in df.iterrows():
    user = row["user"]
    ts = row["datetime"]
    if user in last_ts_per_user:
        diff_min = (ts - last_ts_per_user[user]).total_seconds() / 60.0
        # Exclude negatives (shouldn't happen) and very long gaps optionally
        if 0 < diff_min < 60*24:  # < 24h
            response_times[user].append(diff_min)
    last_ts_per_user[user] = ts

avg_response_time = {user: (np.mean(response_times[user]) if response_times[user] else np.nan) for user in users}

# ---------- Emoji co-occurrence network ----------
cooccurrence = Counter()
for _, row in df.iterrows():
    ems = list(dict.fromkeys(row["emojis"]))  # unique emojis in message preserving order
    if len(ems) > 1:
        for a,b in combinations(sorted(ems), 2):
            cooccurrence[(a,b)] += 1

G = nx.Graph()
for (a,b),w in cooccurrence.items():
    G.add_edge(a,b,weight=w)
# node sizes by total frequency
node_freq = emoji_all["emojis"].value_counts().to_dict()
for n in G.nodes():
    G.nodes[n]["freq"] = node_freq.get(n, 1)

# compute layout
if len(G) > 0:
    pos = nx.spring_layout(G, k=0.5, iterations=100, seed=42)
else:
    pos = {}

# ---------- Word clouds per user (generate PNG images encoded as base64 to embed) ----------
wordcloud_images = {}
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

# ---------- Build Plotly Figures ----------
figs = {}

# 1) Total Emojis per User
figs['total_emojis_per_user'] = px.bar(
    x=total_emoji_counts.index.astype(str),
    y=total_emoji_counts.values,
    labels={'x':'User','y':'Total Emojis'},
    title='Du natürlich :)'
)

# 2) Emoji time-of-day violin plot (per user)
if not df_emoji_time.empty:
    violin_df = df_emoji_time.copy()
    figs['emoji_time_violin'] = px.violin(
        violin_df, x="user", y="hour_frac", points="all",
        labels={'hour_frac':'Hour of day','user':'User'},
        title='Emoji Usage Time of Day (Violin plot)'
    )
    figs['emoji_time_violin'].update_yaxes(range=[0,24])
else:
    figs['emoji_time_violin'] = None

# 3) Emoji usage over time (line plot per user)
if not emoji_by_day_user.empty:
    # pivot to ensure zero-filled dates
    pivot = emoji_by_day_user.pivot(index="date", columns="user", values="emoji_count_per_day").fillna(0)
    fig = go.Figure()
    for user in pivot.columns:
        fig.add_trace(go.Scatter(x=pivot.index, y=pivot[user], mode='lines+markers', name=user))
    fig.update_layout(title="Emoji Usage Over Time (per day)", xaxis_title="Date", yaxis_title="Emoji count")
    figs['emoji_usage_over_time'] = fig
else:
    figs['emoji_usage_over_time'] = None

# 4) Top emojis overall (horizontal bar)
if not top_emojis.empty:
    labels = top_emojis.index.astype(str)
    values = top_emojis.values
    figs['top_emojis'] = px.bar(
        x=values[::-1],
        #y=labels[::-1],
        text=labels[::-1],  # show emoji text
        orientation='h',
        labels={'x':'Count','y':'Emoji'},
        title=f"Top {len(labels)} Emojis Overall"
    )
    figs['top_emojis'].update_traces(textfont_size=26, textposition="outside")
    #figs['top_emojis'].update_layout(yaxis=dict(tickfont=dict(size=28)))
else:
    figs['top_emojis'] = None

# 5) Emoji heatmap by hour and weekday
heat_mat = heat_table.reindex(index=range(24), columns=range(7), fill_value=0)
fig_heat = go.Figure(data=go.Heatmap(
    z=heat_mat.values,
    x=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
    y=[f"{h}:00" for h in heat_mat.index],
    colorscale="YlGnBu",
    colorbar=dict(title="Emoji count")
))
fig_heat.update_layout(title="Emoji Heatmap by Hour and Day of Week")
fig_heat.update_yaxes(autorange='reversed')
figs['emoji_heatmap'] = fig_heat

# 6) Message activity heatmap (messages)
msg_mat = msg_heat_table.reindex(index=range(24), columns=range(7), fill_value=0)
fig_msg_heat = go.Figure(data=go.Heatmap(
    z=msg_mat.values,
    x=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
    y=[f"{h}:00" for h in msg_mat.index],
    colorscale="Plasma",
    colorbar=dict(title="Messages")
))
fig_msg_heat.update_layout(title="Message Activity Heatmap by Hour and Day of Week")
fig_msg_heat.update_yaxes(autorange='reversed')
figs['message_heatmap'] = fig_msg_heat

# 7) Message length distributions (violin/hist per user) - combined violin
fig_len = go.Figure()
for user in users:
    fig_len.add_trace(go.Violin(x=[user]*len(df.loc[df['user']==user,'msg_length']),
                                y=df.loc[df['user']==user,'msg_length'],
                                name=user, box_visible=True, meanline_visible=True))
fig_len.update_layout(title="Message Length Distribution per User", yaxis_title="Characters")
figs['message_length'] = fig_len

# 8) Response time histogram and avg response time (bar)
# histogram (overlayed)
fig_rt = go.Figure()
for user in users:
    vals = response_times.get(user, [])
    if vals:
        fig_rt.add_trace(go.Histogram(x=vals, name=user, opacity=0.6))
fig_rt.update_layout(barmode='overlay', title="Response Time Distribution per User (minutes)", xaxis_title="Minutes")
figs['response_time_hist'] = fig_rt

# avg response times bar
avg_df = pd.DataFrame({"user": list(avg_response_time.keys()), "avg_min": list(avg_response_time.values())})
fig_avg_rt = px.bar(avg_df.sort_values("avg_min"), x="user", y="avg_min", labels={"avg_min":"Avg response (min)","user":"User"},
                    title="Average Response Time per User (minutes)")
figs['avg_response_time'] = fig_avg_rt

# 9) Emoji co-occurrence network (plotly scatter)
if len(G) > 0:
    edge_x = []
    edge_y = []
    edge_w = []
    for u,v,data in G.edges(data=True):
        x0,y0 = pos[u]
        x1,y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        edge_w.append(data.get('weight',1))

    node_x = []
    node_y = []
    node_text = []
    node_size = []
    for n in G.nodes():
        x,y = pos[n]
        node_x.append(x)
        node_y.append(y)
        node_text.append(f"{n} (freq {G.nodes[n]['freq']})")
        node_size.append(max(20, G.nodes[n]['freq'] * 6))

    edge_trace = go.Scatter(x=edge_x, y=edge_y, mode='lines', line=dict(width=3, color='#888'), hoverinfo='none')
    node_trace = go.Scatter(x=node_x, y=node_y, mode='markers+text',
                            text=[n for n in G.nodes()],
                            hovertext=node_text,
                            textposition="top center",
                            marker=dict(size=node_size, color='orange', line=dict(width=1)))
    fig_net = go.Figure(data=[edge_trace, node_trace])
    fig_net.update_layout(title="Emoji Co-occurrence Network", showlegend=False)
    figs['emoji_network'] = fig_net
else:
    figs['emoji_network'] = None

# 10) Wordclouds (embedded images)
# wordcloud_images dict already contains base64 images for each user


# ----------------------------
# 11) Emoji Pairing “Love” Map (Network with styled edges)

if len(G) > 0:
    # Filter to top N emojis for clarity
    TOP_N = 25
    top_emoji_nodes = sorted(node_freq.items(), key=lambda x: x[1], reverse=True)[:TOP_N]
    top_nodes = set([em for em, freq in top_emoji_nodes])
    subG = G.subgraph(top_nodes).copy()

    # Positions for subgraph
    pos_sub = nx.spring_layout(subG, k=0.5, iterations=100, seed=42)

    edge_x = []
    edge_y = []
    edge_colors = []
    edge_widths = []
    for u, v, d in subG.edges(data=True):
        x0, y0 = pos_sub[u]
        x1, y1 = pos_sub[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        w = d.get('weight', 1)
        edge_widths.append(max(1, w))
        if w > 3:
            edge_colors.append('red')
        else:
            edge_colors.append('pink')

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
        node_size.append(max(20, freq*6))

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

    love_map_fig = go.Figure(data=[edge_trace, node_trace])
    love_map_fig.update_layout(
        title="Emoji Pairing 'Love' Map",
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        margin=dict(t=40, b=0, l=0, r=0),
        height=600,
    )
    figs['emoji_love_map'] = love_map_fig
else:
    figs['emoji_love_map'] = None

# ----------------------------
# 12) Emoji Sentiment Mood Board (bubble chart by sentiment category)

# Minimal emoji sentiment dictionary, expand if you like
emoji_sentiment = {
    '😀': 'Positive', '😃': 'Positive', '😂': 'Positive', '❤️': 'Positive', '👍': 'Positive',
    '😞': 'Negative', '😡': 'Negative', '😢': 'Negative', '👎': 'Negative',
    '😐': 'Neutral', '🤔': 'Neutral', '😶': 'Neutral'
}

# Sum all emoji counts from all users
all_emoji_counts = Counter()
for user_counts in user_emojis.values():
    all_emoji_counts.update(user_counts)

# Group emojis by sentiment
sentiment_groups = {'Positive': [], 'Negative': [], 'Neutral': []}
for em, cnt in all_emoji_counts.items():
    sentiment = emoji_sentiment.get(em, 'Neutral')
    sentiment_groups[sentiment].append((em, cnt))

# Create scatter traces, cluster them horizontally by sentiment
mood_board_fig = go.Figure()
x_offsets = {'Positive': -1, 'Neutral': 0, 'Negative': 1}
y_base = 0

for sentiment, emojis_list in sentiment_groups.items():
    for i, (em, cnt) in enumerate(emojis_list):
        mood_board_fig.add_trace(go.Scatter(
            x=[x_offsets[sentiment] + i*0.05],
            y=[y_base],
            mode='text',
            text=[em],
            textfont=dict(size=10 + cnt*4),
            hoverinfo='text',
            hovertext=f"{em}: {cnt} times ({sentiment})"
        ))
    # Add sentiment label above
    mood_board_fig.add_trace(go.Scatter(
        x=[x_offsets[sentiment]],
        y=[y_base + 0.3],
        mode='text',
        text=[sentiment],
        textfont=dict(size=20, family="Arial Black", color="black"),
        hoverinfo='skip'
    ))

mood_board_fig.update_layout(
    title="Emoji Sentiment Mood Board",
    xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-2, 2]),
    yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-0.5, 0.5]),
    height=300,
    margin=dict(t=40, b=20, l=20, r=20)
)

figs['emoji_sentiment_board'] = mood_board_fig

# 13) Chat Timeline Snapshot (message + emoji density per day)

# Aggregate messages and emojis per day
daily_counts = df.groupby("date").agg(
    messages=("message", "count"),
    emojis=("emoji_count", "sum")
).reset_index()

# Create a grouped bar chart with Plotly
fig_timeline = go.Figure(data=[
    go.Bar(name='Messages', x=daily_counts['date'], y=daily_counts['messages'], marker_color='blue'),
    go.Bar(name='Emojis', x=daily_counts['date'], y=daily_counts['emojis'], marker_color='orange'),
])

fig_timeline.update_layout(
    barmode='group',
    title="Chat Timeline Snapshot: Messages & Emojis per Day",
    xaxis_title="Date",
    yaxis_title="Count",
    xaxis_tickformat='%d %b %Y',
    xaxis_tickangle=-45,
    legend_title_text="",
    template="simple_white"
)

# Calculate 7-day rolling average trends separately for messages and emojis
daily_counts['messages_trend'] = daily_counts['messages'].rolling(window=7, min_periods=1, center=True).mean()
daily_counts['emojis_trend'] = daily_counts['emojis'].rolling(window=7, min_periods=1, center=True).mean()

# Create the figure with bars (messages and emojis)
fig_timeline = go.Figure(data=[
    go.Bar(name='Messages', x=daily_counts['date'], y=daily_counts['messages'], marker_color='blue'),
    go.Bar(name='Emojis', x=daily_counts['date'], y=daily_counts['emojis'], marker_color='orange'),
])

# Add trend line for messages
fig_timeline.add_trace(go.Scatter(
    x=daily_counts['date'],
    y=daily_counts['messages_trend'],
    mode='lines',
    line=dict(color='blue', width=3, dash='dash'),
    name='Messages Trend'
))

# Add trend line for emojis
fig_timeline.add_trace(go.Scatter(
    x=daily_counts['date'],
    y=daily_counts['emojis_trend'],
    mode='lines',
    line=dict(color='orange', width=3, dash='dash'),
    name='Emojis Trend'
))

# Layout updates
fig_timeline.update_layout(
    barmode='group',
    title="Chat Timeline Snapshot: Messages & Emojis per Day",
    xaxis_title="Date",
    yaxis_title="Count",
    xaxis_tickformat='%d %b %Y',
    xaxis_tickangle=-45,
    legend_title_text="",
    template="simple_white"
)



figs['chat_timeline_snapshot'] = fig_timeline


# ---------- Assemble HTML ----------
html_parts = []
html_parts.append("""
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
""")
html_parts.append(f"<h1>WhatsApp Emoji & Chat Report</h1>")
html_parts.append(f"<p>Messages parsed: {len(df)} · Users: {len(users)} · Date range: {df['datetime'].min()} — {df['datetime'].max()}</p>")

def fig_to_html_div(fig):
    if fig is None:
        return "<p><i>No data for this plot.</i></p>"
    return pio.to_html(fig, full_html=False, include_plotlyjs='cdn')

# add figures sections
sections = [
    ("Fangen wir künstlerisch an, wenn unser Chat jeweils ein Kunstwerk wäre", None),
    ("Zu welcher Tageszeit in der Woche waren wir denn besonders fleißig am Texten?", 'message_heatmap'),
    ("...und am Emojis verschicken?", 'emoji_heatmap'),
    (f"Dabei waren unsere Top {TOP_EMOJI_COUNT} Emojis diese", 'top_emojis'),
    ("Aber wie lange musste ich denn immer so warten auf Antwort von dir? Ist da mehr orange als blau?", 'response_time_hist'),
    ("Jetzt schwarz auf weiß - da lässt sich aber eine länger Zeit...", 'avg_response_time'),
    ("À propos lang, das scheint dir zu gefallen, thats what the statistics says", 'message_length'),
    ("Ich lenke ab, zurück zu den Emojis: Lecker ist gemeinsam mit Tims Sonnenbrille bei uns ziemlich isoliert von den anderen", "emoji_love_map"),
    ("Unser Spirit Animal ist auf jeden Fall Tim '😂'", "emoji_sentiment_board"),
    ("An den Tagen kann man es nicht mehr ablesen wer nun führt bzgl. der Emojis", 'emoji_usage_over_time'),
    ("Hier mal auf die Tageszeit im gemittelt, also vor 8 ist bei mir mit keinem Emoji zu rechnen", 'emoji_time_violin'),
    ("Hier jetzt schwarz auf weiß, wer hat denn nun die meisten Emojis versendet?", 'total_emojis_per_user'),
#    ("Emoji Co-occurrence Network", 'emoji_network'),
#    ("Chat timeline_snapshot", "chat_timeline_snapshot"),
    ]


first = True
for title, key in sections:
    if not first:
        html_parts.append("<div class='pagebreak'></div>")
    first = False

    html_parts.append(f"<hr><h2>{title}</h2>")

    if key:
        fig_html = fig_to_html_div(figs.get(key))
        html_parts.append(fig_html)
    else:
        for user, img_b64 in wordcloud_images.items():
            html_parts.append(f"<h3>{user}</h3>")
            html_parts.append(
                f"<img src='{img_b64}' alt='Wordcloud for {user}' "
                "style='max-width:100%;height:auto;"
                "border:1px solid #ddd;padding:4px;margin-bottom:10px;'>"
            )


# Write to file
with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
    f.write("\n".join(html_parts))

# Get a CSS linebreak https://chatgpt.com/s/t_6931f4dfa92c8191a85aeb821be0352a

print(f"Report written to {OUTPUT_HTML}. Open this file in a browser to explore the interactive plots.")

