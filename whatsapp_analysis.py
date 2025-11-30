import re
import emoji
from collections import Counter, defaultdict
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta
import matplotlib.dates as mdates
from wordcloud import WordCloud, STOPWORDS
import networkx as nx
from itertools import combinations

# import matplotlib.pyplot as plt
# 
plt.rcParams['font.family'] = '/usr/share/fonts/google-noto-emoji-fonts/NotoEmoji-Regular.ttf'
# print("Using font: DejaVu Sans")


# === CONFIG ===
chat_file = 'chat.txt'


# === READ CHAT ===
try:
    with open(chat_file, 'r', encoding='utf-8') as f:
        chat_data = f.readlines()
    print(f"Read {chat_file} successfully")
except OSError:
    print(f"File {chat_file} not found")
    chat_data = []

# === PARSE CHAT ===
# Format: [23.11.23, 06:53:39] Name: Message
pattern = re.compile(
    r'^\[(\d{2}\.\d{2}\.\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+): (.+)$'
)

user_messages = defaultdict(list)
user_timestamps = defaultdict(list)
emoji_times = defaultdict(list)
emoji_counts = Counter()
emoji_per_user = defaultdict(Counter)
message_lengths = defaultdict(list)
message_times = []
message_senders = []
message_datetimes = []

def extract_emojis(text):
    return [e["emoji"] for e in emoji.emoji_list(text)]

for line in chat_data:
    match = pattern.match(line.strip())
    if not match:
        continue
    date_str, time_str, user, message = match.groups()
    dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%y %H:%M:%S")
    
    user_messages[user].append(message)
    user_timestamps[user].append(dt)
    message_lengths[user].append(len(message))
    
    message_times.append(dt)
    message_senders.append(user)
    message_datetimes.append(dt)
    
    # Emojis and their times per user
    emojis = extract_emojis(message)
    if emojis:
        for em in emojis:
            emoji_times[user].append(dt.hour + dt.minute/60)
        emoji_counts.update(emojis)
        emoji_per_user[user].update(emojis)

print(f"Parsed {len(message_datetimes)} messages from {len(user_messages)} users")

# === 1) Total Emojis per User (Bar) ===
plt.figure(figsize=(8,4))
users = list(emoji_per_user.keys())
totals = [sum(emoji_per_user[u].values()) for u in users]
plt.bar(users, totals, color='skyblue')
plt.title("Total Emojis per User")
plt.ylabel("Count")
plt.xticks(rotation=30)
plt.tight_layout()
plt.savefig("plot_total_emojis_per_user.png")

# === 2) Emoji Usage Time of Day (Violin) ===
plt.figure(figsize=(10,5))
data = [emoji_times[u] for u in users]
plt.violinplot(data, showmeans=True)
plt.xticks(range(1, len(users)+1), users, rotation=30)
plt.ylabel("Hour of Day (0-24)")
plt.title("Emoji Usage Time of Day (Violin Plot)")
plt.tight_layout()
plt.savefig("plot_emoji_time_of_day_violin.png")

# === 3) Emoji Usage Over Time (Line Plot per Day) ===
# Count emojis per day per user
emoji_day_counts = defaultdict(lambda: defaultdict(int))  # user -> date -> count

for user in users:
    for dt, msgs in zip(user_timestamps[user], user_messages[user]):
        emojis_in_msg = extract_emojis(msgs)
        emoji_day_counts[user][dt.date()] += len(emojis_in_msg)

plt.figure(figsize=(12,6))
for user in users:
    days = sorted(emoji_day_counts[user].keys())
    counts = [emoji_day_counts[user][day] for day in days]
    plt.plot(days, counts, marker='o', label=user)
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%d-%b'))
plt.xticks(rotation=45)
plt.ylabel("Number of Emojis")
plt.title("Emoji Usage Over Time (Per Day)")
plt.legend()
plt.tight_layout()
plt.savefig("plot_emoji_usage_over_time.png")

# === 4) Most Frequent Emojis (Overall) ===
top_emojis = emoji_counts.most_common(15)
labels, values = zip(*top_emojis)
plt.figure(figsize=(8,5))
plt.barh(labels[::-1], values[::-1], color='lightgreen')
plt.title("Top 15 Most Frequent Emojis Overall")
plt.xlabel("Count")
plt.tight_layout()
plt.savefig("plot_top_emojis_overall.png")

# === 5) Emoji Heatmap by Hour and Day of Week ===
heatmap_data = np.zeros((24,7))  # rows=hours, cols=weekdays (Mon=0)

for user in users:
    for dt, msg in zip(user_timestamps[user], user_messages[user]):
        hour = dt.hour
        weekday = dt.weekday()
        count_emojis = len(extract_emojis(msg))
        heatmap_data[hour, weekday] += count_emojis

# === 6) Message Length Distribution (Histogram per User) ===
plt.figure(figsize=(10,6))
bins = range(0, 300, 10)
for user in users:
    plt.hist(message_lengths[user], bins=bins, alpha=0.5, label=user)
plt.xlabel("Message Length (characters)")
plt.ylabel("Count")
plt.title("Message Length Distribution per User")
plt.legend()
plt.tight_layout()
plt.savefig("plot_message_length_histogram.png")

# === 7) Response Time Analysis ===
# Compute response time between consecutive messages per user (approximate)
response_times = defaultdict(list)

# Sort messages by datetime
all_msgs = sorted(zip(message_datetimes, message_senders), key=lambda x: x[0])

last_msg_time = {}
for dt, user in all_msgs:
    if user in last_msg_time:
        diff = (dt - last_msg_time[user]).total_seconds() / 60  # in minutes
        if 0 < diff < 1440:  # Ignore very long breaks > 1 day
            response_times[user].append(diff)
    last_msg_time[user] = dt

plt.figure(figsize=(10,6))
for user in users:
    plt.hist(response_times[user], bins=30, alpha=0.5, label=user)
plt.xlabel("Response Time (minutes)")
plt.ylabel("Frequency")
plt.title("Response Time Distribution per User")
plt.legend()
plt.tight_layout()
plt.savefig("plot_response_time_histogram.png")

# Calculate average response time per user (in minutes)
avg_response_time = {}
for user, times in response_times.items():
    if times:
        avg_response_time[user] = sum(times) / len(times)
    else:
        avg_response_time[user] = 0

# Plot average response time per user
plt.figure(figsize=(8, 4))
users_sorted = sorted(avg_response_time, key=avg_response_time.get)
avg_times = [avg_response_time[u] for u in users_sorted]

plt.bar(users_sorted, avg_times, color='salmon')
plt.ylabel("Average Response Time (minutes)")
plt.title("Average Response Time per User")
plt.xticks(rotation=30)
plt.tight_layout()
plt.savefig("plot_avg_response_time_per_user.png")


# === 8) Emoji Co-occurrence Network ===
# Build graph of emoji co-occurrences per message (consider messages with ≥2 emojis)
cooccurrence = Counter()

for msgs in user_messages.values():
    for msg in msgs:
        ems = set(extract_emojis(msg))
        if len(ems) > 1:
            for pair in combinations(sorted(ems), 2):
                cooccurrence[pair] += 1

# Build networkx graph
G = nx.Graph()
for (e1,e2), weight in cooccurrence.items():
    G.add_edge(e1, e2, weight=weight)

plt.figure(figsize=(10,10))
pos = nx.spring_layout(G, k=0.3)
weights = [G[u][v]['weight'] for u,v in G.edges()]
nx.draw_networkx_nodes(G, pos, node_size=300, node_color='orange')
nx.draw_networkx_edges(G, pos, width=[w*0.3 for w in weights], alpha=0.7)
nx.draw_networkx_labels(G, pos, font_size=14)
plt.title("Emoji Co-occurrence Network")
plt.axis('off')
plt.tight_layout()
plt.savefig("plot_emoji_cooccurrence_network.png")

# === 9) Daily Activity Heatmap (Messages) ===
# Count messages per hour per weekday
msg_heatmap = np.zeros((24,7))

for dt in message_datetimes:
    msg_heatmap[dt.hour, dt.weekday()] += 1

plt.figure(figsize=(8,6))
plt.imshow(msg_heatmap, aspect='auto', cmap='plasma', origin='lower')
plt.colorbar(label="Number of Messages")
plt.yticks(range(24), [f"{h}:00" for h in range(24)])
plt.xticks(range(7), ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"])
plt.title("Message Activity Heatmap by Hour and Day of Week")
plt.tight_layout()
plt.savefig("plot_message_activity_heatmap.png")

# === 10) Word Cloud per User ===
for user in users:
    text = " ".join(user_messages[user])
    stopwords = set(STOPWORDS)
    wc = WordCloud(width=800, height=400, background_color='white',
                   stopwords=stopwords, collocations=False).generate(text)
    plt.figure(figsize=(10,5))
    plt.imshow(wc, interpolation='bilinear')
    plt.axis('off')
    plt.title(f"Word Cloud for {user}")
    plt.tight_layout()
    plt.savefig(f"wordcloud_{user}.png")

print("All plots saved!")

# Optional: To display plots interactively, add plt.show() calls if running interactively.

