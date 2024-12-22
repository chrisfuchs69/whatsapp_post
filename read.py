import re

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

print(type(chat_data))
# Regular expression to match WhatsApp chat format
#prog = re.compile(pattern)
#message_pattern = re.compile(r'\[(\d{2}\.\d{2}\.\d{2}), (\d{2}:\d{2}:\d{2})\] (\w+): (.+)')
#result = prog.match(string)


# Kompiliertes Muster
pattern = re.compile(r"I like to eat \w+")

# Liste mit Strings
list_of_strings = [
    "I like to eat meat", 
    "I don't like to eat meat", 
    "I like to eat fish", 
    "I don't like to eat fish"
]

# Extrahiere alle Treffer
outcome = [pattern.match(x) for x in list_of_strings if pattern.match(x)]

print(outcome)




