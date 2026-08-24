"""One-shot: fix KeyboardButton text extraction in _manager_main_keyboard wrapper."""
src = open("bot.py", encoding="utf-8").read()
old = "        rows = [[str(x) for x in r] for r in markup.keyboard]"
new = "        rows = [[(getattr(x, 'text', None) or str(x)) for x in r] for r in markup.keyboard]"
assert src.count(old) == 1
src = src.replace(old, new)
open("bot.py", "w", encoding="utf-8").write(src)
print("kb fix OK")
