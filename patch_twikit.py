"""Patch twikit 1.4.0 KeyError bugs in user.py.

PyPI 原版直接索引 legacy 字段，部分用户 profile 缺字段会 KeyError。
把所有 legacy['xxx'] 改为 .get() 防护。
"""
import pathlib
import re

p = pathlib.Path(__file__).parent / ".venv/lib/python3.10/site-packages/twikit/user.py"
if not p.exists():
    print("twikit not installed, skip")
    raise SystemExit(0)

t = p.read_text()

# Replace legacy['key'] → legacy.get('key', default)
# default: '' for str, 0 for int, False for bool, [] for list, {} for dict
def replacer(m):
    full = m.group(0)
    key = m.group(1)
    # already .get() — skip
    if '.get(' in full:
        return full
    # guess default by type hint
    line = m.string[m.start():m.start()+120]
    if ': str' in line or ': list' in line and 'str' in line:
        default = "''"
    elif ': bool' in line:
        default = "False"
    elif ': int' in line:
        default = "0"
    elif ': list' in line:
        default = "[]"
    else:
        default = "None"
    return f"legacy.get('{key}', {default})"

# Match legacy['xxx'] but not legacy.get(...)
pattern = r"legacy\['([^']+)'\]"
new_t = re.sub(pattern, replacer, t)

if new_t != t:
    p.write_text(new_t)
    print(f"patched {p}")
else:
    print("already patched, skip")
