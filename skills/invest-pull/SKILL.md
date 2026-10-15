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

## 输入参数

| 参数 | 说明 |
|------|------|
| 数据目录 | **必填**。runs/、`.env`、`cookies.json` 所在目录的绝对路径，与插件代码目录分离（插件升级不丢数据） |

**未提供数据目录时必须先向用户确认，不要自行猜测或使用当前工作目录。**

## 执行步骤

插件目录 = 本 SKILL.md 的上两级目录。无需 cd，直接以绝对路径调用：

```bash
<插件目录>/invest --data <数据目录> pull
<插件目录>/invest --data <数据目录> filter
```

`invest` 是自举脚本：首次运行自动在插件目录建 `.venv`、装 `requirements.txt`、patch twikit，之后透传给 `cli.py`。

- `pull`：拉 Techmeme/Finnhub 新闻 + Twitter 关注时间线 + 市场快照（BTC/ETH/SOL/XAU、美债、VIX、DXY），按日增量归档到 `runs/news_raw/{date}_{source}.md`，链接级去重。
- `filter`：把 `news_raw/` 复制到 `runs/news_filtered/`，按敏感词表剔除敏感条目（LLM 只读 filtered）。

执行后报告各源新增条数；任一步失败原样报告 stderr，不要重试超过一次。
