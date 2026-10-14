"""Patch twikit 1.4.0 KeyError bugs.

PyPI 原版多处裸索引 legacy/entities/card 字段，特定数据形状会 KeyError：
- user.py: User() 构造时 legacy['xxx'] / legacy['entities']['description']['urls']
- tweet.py: data['card']['legacy']['name']（poll 卡片缺字段）
- client.py: get_latest_timeline 循环里 find_dict()[0] / tweet_info['core'] 直连

本脚本把已知崩溃点全部改为防御性写法；幂等，可重复执行。
由 ./invest 在首次安装依赖后自动调用。
"""
import pathlib
import re

try:
    import twikit
except ImportError:
    print("twikit not installed, skip")
    raise SystemExit(0)

PKG = pathlib.Path(twikit.__file__).parent


def replace_once(t, old, new):
    """old → new 全量替换（同一代码块在文件里可能出现多次，如多个 timeline 方法）。
    new 已存在且无 old 残留则幂等跳过。返回 (text, changed, found)。"""
    has_old = old in t
    has_new = new in t
    if has_old:
        return t.replace(old, new), True, True
    return t, False, has_old or has_new


# ─── user.py ────────────────────────────────────────────────────────────────

p = PKG / "user.py"
t = p.read_text()

# 特殊行必须先于通用正则：legacy['entities']['description']['urls'] 是链式索引
t, changed1, _ = replace_once(
    t,
    "legacy['entities']['description']['urls']",
    "legacy.get('entities', {}).get('description', {}).get('urls', [])",
)


# 通用规则：legacy['key'] → legacy.get('key', default)
# default: '' for str, 0 for int, False for bool, [] for list
def replacer(m):
    full = m.group(0)
    key = m.group(1)
    if '.get(' in full:
        return full
    line = m.string[m.start():m.start() + 120]
    if ': str' in line:
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


new_t = re.sub(r"legacy\['([^']+)'\]", replacer, t)
if new_t != t:
    t = new_t
    changed1 = True

if changed1:
    p.write_text(t)
    print(f"patched {p}")
else:
    print("user.py already patched, skip")


# ─── tweet.py：poll 卡片缺 legacy/name ──────────────────────────────────────

p = PKG / "tweet.py"
t = p.read_text()
t, changed, found = replace_once(
    t,
    "data['card']['legacy']['name'].startswith('poll')",
    "data['card'].get('legacy', {}).get('name', '').startswith('poll')",
)
if changed:
    p.write_text(t)
    print(f"patched {p}")
elif found:
    print("tweet.py already patched, skip")
else:
    print("tweet.py pattern not found, skip")


# ─── client.py：timeline 循环整段 try/except ────────────────────────────────
# 历史上有两种旧形态（原版直连 / 只加空结果检查），统一收敛到 try/except 兜底：
# 单条推文解析失败跳过，不炸掉整条时间线。

p = PKG / "client.py"
t = p.read_text()

target = """            try:
                tweet_info = find_dict(item, 'result')[0]
                if tweet_info['__typename'] == 'TweetWithVisibilityResults':
                    tweet_info = tweet_info['tweet']
                user_info = tweet_info['core']['user_results']['result']
            except (IndexError, KeyError, TypeError):
                continue
            results.append(Tweet(self, tweet_info, user_info))"""

old_variants = [
    # 原版：无任何防护
    """            tweet_info = find_dict(item, 'result')[0]
            if tweet_info['__typename'] == 'TweetWithVisibilityResults':
                tweet_info = tweet_info['tweet']
            user_info = tweet_info['core']['user_results']['result']
            results.append(Tweet(self, tweet_info, user_info))""",
    # 旧补丁：空结果 + core 检查（仍会在 Tweet() 构造时炸）
    """            _results = find_dict(item, 'result')
            if not _results:
                continue
            tweet_info = _results[0]
            if 'core' not in tweet_info:
                continue
            if tweet_info['__typename'] == 'TweetWithVisibilityResults':
                tweet_info = tweet_info['tweet']
            user_info = tweet_info['core']['user_results']['result']
            results.append(Tweet(self, tweet_info, user_info))""",
    # 旧补丁：只有 core 检查
    """            tweet_info = find_dict(item, 'result')[0]
            if 'core' not in tweet_info:
                continue
            if tweet_info['__typename'] == 'TweetWithVisibilityResults':
                tweet_info = tweet_info['tweet']
            user_info = tweet_info['core']['user_results']['result']
            results.append(Tweet(self, tweet_info, user_info))""",
]

changed = False
for old in old_variants:
    t, c, _ = replace_once(t, old, target)
    changed = changed or c
if changed:
    p.write_text(t)
    print(f"patched {p}")
elif "except (IndexError, KeyError, TypeError):" in t:
    print("client.py already patched, skip")
else:
    print("client.py pattern not found, skip")
