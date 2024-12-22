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
message_pattern = re.compile(r'(\[\d{2}.\d{2}.\d{2}, \d{2}:\d{2}:\d{2}\])')
# message_pattern = re.compile(r'(\[\d{2}.\d{2}.\d{2}, \d{2}:\d{2}:\d{2}\]: \w+:)')
# Dictionary to store messages
user_messages = {}

# Parse the chat data
for line in chat_data:
    match = message_pattern.match(line)
    if match:
        day, month, year, hour, minute, user = match.groups()
        if user not in user_messages:
            user_messages[user] = []
        user_messages[user].append(message)

# Print complete directory
print(user_messages)
