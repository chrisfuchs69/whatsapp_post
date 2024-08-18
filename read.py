import re
import emoji
from collections import Counter
import matplotlib.pyplot as plt

# Chat file
chat_file = 'chat.txt'

# Open and read the file
try:
    with open(chat_file, 'r', encoding='utf-8') as f:
        chat_data = f.readlines()
        print(f'Reading of {chat_file} successful')
except OSError:
    print(f'File {chat_file} not found')
    exit()

# Regular expression to match WhatsApp chat format
message_pattern = re.compile(r'(\d{2}/\d{2}/\d{2}, \d{2}:\d{2}) - ([^:]+): (.+)')

# Dictionary to store messages
user_messages = {}

# Parse the chat data
for line in chat_data:
    match = message_pattern.match(line)
    if match:
        timestamp, user, message = match.groups()
        if user not in user_messages:
            user_messages[user] = []
        user_messages[user].append(message)

# Function to extract emojis
def extract_emojis(text):
    return [char for char in text if char in emoji.EMOJI_DATA]

# Dictionary to store emoji counts by user
user_emojis = {}

# Extract emojis from each user's messages
for user, messages in user_messages.items():
    all_emojis = []
    for message in messages:
        all_emojis.extend(extract_emojis(message))
    user_emojis[user] = Counter(all_emojis)

# Print emoji counts for each user
for user, emoji_count in user_emojis.items():
    print(f"Emojis used by {user}:")
    for emoji_char, count in emoji_count.most_common():
        print(f"{emoji_char}: {count}")
    print("\n")

# Visualize top 5 emojis for each user
for user, emoji_count in user_emojis.items():
    if emoji_count:
        emojis, counts = zip(*emoji_count.most_common(5))

        plt.figure(figsize=(10, 4))
        plt.bar(emojis, counts, color='skyblue')
        plt.xlabel('Emojis')
        plt.ylabel('Counts')
        plt.title(f'Top 5 Emojis Used by {user}')
        plt.savefig(f'top_emojis_{user}.png')
        plt.close()
    else:
        print(f"No emojis used by {user}")

# Optional: Print all user messages if needed
# print(user_messages)

