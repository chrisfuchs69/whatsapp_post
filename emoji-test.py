import matplotlib
matplotlib.use('Qt5Agg')

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# Path to the installed font
font_path = '/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf'
font_prop = fm.FontProperties(fname=font_path)

# Apply for all text
plt.rcParams['font.family'] = font_prop.get_name()
plt.rcParams['font.sans-serif'] = [font_prop.get_name()]

print("Using font:", font_prop.get_name())

plt.figure(figsize=(6, 2))
plt.text(
    0.5, 0.5,
    "😀 😂 👍 ❤️ 🇩🇪 🚴‍♂️ ☕️ 🔥 🎨",
    fontsize=60,
    ha='center',
    va='center',
    fontproperties=font_prop
)
plt.axis('off')
plt.tight_layout()
plt.show()

