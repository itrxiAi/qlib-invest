---
name: invest-triage
description: 读取最新归档资讯，去重、粗筛、打分，输出 triage 清单供逐条深度分析。
allowed-tools:
  - read
  - write
  - glob
  - exec
  - webfetch
triggers:
  - user
  - model
---

# 资讯粗筛（triage）

输入是 `runs/news_filtered/` 下窗口内的归档，输出是 `runs/tmp/triage_{date}_{hour}.json`。

以下所有路径相对数据目录（含 `runs/` 的目录），数据目录由调用方传入，未知时先向用户确认。

## Artifact Paths

- `runs/briefing.md` — 当前局势简报（跨 session 记忆），格式见 invest-briefing skill
- `runs/news_filtered/{date}_techmeme.md`、`{date}_twitter.md`、`{date}_market.md` — 原始资讯
- `runs/tmp/triage_{date}_{hour}.json` — 本步骤输出

## 通用约定

**社交信号**：🔁100+ 或 ❤500+ = 高热度信号；❤<50 的单一社交来源只作线索，须权威源确认后方可作事实引用。

## Phase 1: 加载上下文

读 `runs/briefing.md`，不要依赖记忆。

## Phase 2: 去重与粗筛

窗口 = briefing.md 的 `上次更新` 至当前时间，**上限 3 天**——`上次更新` 距今超过 3 天时，窗口 = 3 天前至当前时间（更早的积压不扫：追踪表已按同期限清理，扫了也没有去重上下文，且对"当前局势"无价值）。该字段为空则取当天。列出窗口内 `runs/news_filtered/` 文件，并行读取。

可用 `webfetch` 补齐 filtered 可能遗漏的当前市场焦点，补到的条目统一分类、统一评分。

### 分类

同一事件多个角度、相似的事件、强关联的事件合并为一条：

- `[新增]` — briefing 追踪表中没有对应事件
- `[更新]` — 追踪表中已有事件的新进展
- 丢弃 — 内容几乎相同、无新信息的重复报道；或娱乐/社会/个人观点/营销稿/转发感谢

### 粗评分

按**本轮增量和新鲜程度对投资决策的影响**打 1-5 分。持续事件纯延续低分，有新增量才高分。1-2 分不进 to_analyze。

## Phase 3: 输出 triage 清单

写入 `runs/tmp/triage_{date}_{hour}.json`（用当前日期和小时）：

```json
{
  "date": "2026-08-31",
  "hour": "18",
  "briefing_updated": "2026-08-31 18:00",
  "to_analyze": [
    {
      "id": 1,
      "title": "事件标题",
      "source": "techmeme|twitter|web_search",
      "time": "事件时间",
      "link": "链接",
      "score": 5,
      "category": "新增|更新",
      "raw_text": "原始资讯文本"
    }
  ],
  "backup": [{"title": "标题", "reason": "1-2分原因"}],
  "discarded": [{"title": "标题", "reason": "丢弃原因"}]
}
```
