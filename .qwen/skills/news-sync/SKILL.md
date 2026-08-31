---
name: news-sync
description: 读取最新同步的资讯，去重、粗筛，输出 triage 清单供后续逐条深度分析。每小时资讯同步完成后调用。
allowedTools:
  - read_file
  - glob
  - grep_search
  - write_file
  - run_shell_command
  - web_fetch
---

# 资讯同步与粗筛工作流

输入是 `runs/news_filtered/` 下的最新归档，输出是 `runs/tmp/triage_{date}_{hour}.json`。

## Artifact Paths

- `runs/briefing.md` — 当前局势简报（跨 session 记忆），标题下第一行为 `> 上次更新：{YYYY-MM-DD HH:MM}`
- `runs/causal_rules.md` — 因果方向规则库
- `runs/news_filtered/{date}_techmeme.md`、`runs/news_filtered/{date}_twitter.md` — 原始资讯
- `runs/news_deep/{date}_{hour}.md` — 本轮深度分析存档

## 通用约定

**社交信号**：🔁100+ 或 ❤500+ = 高热度信号；❤<50 的单一社交来源只作线索，须权威源确认后方可作事实引用。

## Phase 1: 加载上下文

用 `read_file` 读 `runs/briefing.md`，不要依赖记忆。

文件不存在则按 Phase 4b 的章节结构建空文件，`上次更新` 留空。

**输出**：当前状态的心智模型。

## Phase 2: 去重与粗筛

窗口 = briefing.md 的 `上次更新` 至当前时间；该字段为空则取当天。用 `run_shell_command` 执行 `find runs/news_filtered -name '*_techmeme.md' -o -name '*_twitter.md'` 列出窗口内所有文件，`read_file` 并行读取。

用 `web_fetch` 补齐 news_filtered 可能遗漏的当前市场焦点，补到的条目与 news_filtered 统一分类、统一评分。

### 分类

同一事件多个角度、相似的事件、强关联的事件需合并为一条

- `[新增]` — 历史中无相关条目
- `[更新]` — 同一事件的新进展
- 丢弃 — 内容几乎相同、无新信息的重复报道；或娱乐/社会/个人观点/营销稿/转发感谢

### 粗评分

按**本轮增量和新鲜程度对投资决策的影响**打 1-5 分。持续事件纯延续低分，有新增量才高分。

1-2 分不进 Phase 3。

## Phase 3: 输出 triage 清单

将 Phase 2 结果写入 `runs/tmp/triage_{date}_{hour}.json`（用当前日期和小时），格式：

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
  "backup": [
    {"title": "标题", "reason": "1-2分，不深度分析"}
  ],
  "discarded": [
    {"title": "标题", "reason": "丢弃原因"}
  ]
}
```

**输出**：triage JSON 文件路径。
