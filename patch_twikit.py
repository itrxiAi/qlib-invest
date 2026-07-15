"""Patch twikit 1.4.0 KeyError bug on user.py:102.
PyPI 原版直接索引 legacy['entities']['description']['urls']，
部分用户 profile 缺少该字段会 KeyError。改为 .get() 链。
"""
import pathlib

p = pathlib.Path(__file__).parent / ".venv/lib/python3.10/site-packages/twikit/user.py"
if not p.exists():
    print("twikit not installed, skip")
    raise SystemExit(0)

t = p.read_text()
old = "legacy['entities']['description']['urls']"
new = "legacy.get('entities', {}).get('description', {}).get('urls', [])"
if old in t:
    p.write_text(t.replace(old, new))
    print(f"patched {p}")
else:
    print("already patched, skip")
