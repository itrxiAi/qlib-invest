"""Patch twikit 1.4.0 KeyError bugs in user.py.

PyPI 原版直接索引 legacy 字段，部分用户 profile 缺字段会 KeyError。
改为 .get() 链式调用。
"""
import pathlib

p = pathlib.Path(__file__).parent / ".venv/lib/python3.10/site-packages/twikit/user.py"
if not p.exists():
    print("twikit not installed, skip")
    raise SystemExit(0)

t = p.read_text()

fixes = [
    ("legacy['location']", "legacy.get('location', '')"),
    ("legacy['description']", "legacy.get('description', '')"),
    ("legacy['entities']['description']['urls']", "legacy.get('entities', {}).get('description', {}).get('urls', [])"),
    ("legacy['pinned_tweet_ids_str']", "legacy.get('pinned_tweet_ids_str', [])"),
    ("legacy['verified']", "legacy.get('verified', False)"),
    ("legacy['possibly_sensitive']", "legacy.get('possibly_sensitive', False)"),
    ("legacy['can_dm']", "legacy.get('can_dm', False)"),
    ("legacy['can_media_tag']", "legacy.get('can_media_tag', False)"),
    ("legacy['want_retweets']", "legacy.get('want_retweets', False)"),
]

changed = False
for old, new in fixes:
    if old in t:
        t = t.replace(old, new)
        changed = True
        print(f"  fixed: {old}")

if changed:
    p.write_text(t)
    print(f"patched {p}")
else:
    print("already patched, skip")
