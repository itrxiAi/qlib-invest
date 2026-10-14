# qlib-invest 插件规则

投研资讯管线：采集 → 粗筛 → 深度分析 → 简报 → 推送。

## 核心约定

- 确定性操作全部走 `./invest <cmd>`（自举脚本：首次运行自动建 `.venv`、装 requirements、patch twikit，然后透传给 `cli.py`），不直接调内部脚本
- LLM 流程全部是 `skills/` 下的 SKILL.md，由 agent 按流程驱动
- briefing 格式契约定义在 `skills/invest-briefing/SKILL.md`，是唯一权威来源
- 旧编排脚本 `run_deep_analysis.py` / `scheduler.py`（含定时器）已被插件流程取代，保留仅作数据拉取与 TG 推送的实现被 `cli.py` 复用
- `.qwen/skills/` 下的 news-sync / news-analyze 是旧版 skill，以 `skills/` 目录为准

## 环境

- 系统需预装 `python3.10`（`./invest` 首次运行会用它建 `.venv`；缺失时报错退出）
- `.env`：TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（可选）、QWEN_PROXY、DEEP_MIN_SCORE（默认 3）、BRIEFING_STALE_DAYS（默认 3）、SYNCER_API_URL/KEY
- Twitter cookie：`TWITTER_AUTH_TOKEN` / `TWITTER_CT0`

## 工作流程

1. `/invest-pull` 拉资讯（定时：每小时；纯确定性，无 LLM）
2. `/invest-run` 完整分析一轮（定时：每天 08:00 / 20:00）
   - 内部依次：filter → invest-triage → 逐条 invest-analyze → archive → invest-briefing → push

## 数据流

```
news_raw/（原始归档，全量）
  → news_filtered/（敏感词过滤，LLM 可见）
    → triage_{d}_{h}.json（粗筛清单）
      → analyze_{id}.md（逐条分析）
        → news_deep/{d}_{h}.md（存档）
          → briefing.md（跨会话记忆）
            → TG 推送
```
