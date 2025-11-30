import plotly.express as px

fig = px.bar(
    x=["😀","😂","❤️","🚴‍♂️","🔥"],
    y=[5, 2, 7, 3, 1],
    title="Emoji Render Test 🚀"
)
fig.show()

