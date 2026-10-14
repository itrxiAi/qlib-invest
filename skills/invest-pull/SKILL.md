---
name: invest-pull
description: 拉取资讯（Techmeme/Twitter/市场快照）并归档到 runs/news_raw/，然后过滤到 runs/news_filtered/。定时或手动触发。
allowed-tools:
  - exec
triggers:
  - user
---

# 资讯拉取与过滤

纯确定性步骤，不调用 LLM。

## 执行步骤

先 `cd` 到项目根目录（含 `invest` 启动脚本的目录，即本插件安装位置），然后执行：

```bash
./invest pull
./invest filter
```

`./invest` 是自举脚本：首次运行自动建 `.venv` 并装 `requirements.txt`，之后透传给 `cli.py`。

- `pull`：拉 Techmeme/Finnhub 新闻 + Twitter 关注时间线 + 市场快照（BTC/ETH/SOL/XAU、美债、VIX、DXY），按日增量归档到 `runs/news_raw/{date}_{source}.md`，链接级去重。
- `filter`：把 `news_raw/` 复制到 `runs/news_filtered/`，按敏感词表剔除敏感条目（LLM 只读 filtered）。

执行后报告各源新增条数；任一步失败原样报告 stderr，不要重试超过一次。
