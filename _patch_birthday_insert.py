"""One-shot: insert the Birthday & Occasions layer into bot.py before __main__."""
layer = open("_bday_layer.py", encoding="utf-8").read()
src = open("bot.py", encoding="utf-8").read()
anchor = 'if __name__ == "__main__":'
assert src.count(anchor) == 1, "anchor must be unique"
idx = src.index(anchor)
src = src[:idx] + layer + src[idx:]
open("bot.py", "w", encoding="utf-8").write(src)
print("inserted OK, new length:", len(src.splitlines()))
