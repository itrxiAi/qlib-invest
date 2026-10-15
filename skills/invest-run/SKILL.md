---
name: invest-run
description: 完整跑一轮投研深度分析：粗筛 → 逐条分析 → 存档 → 更新 briefing → 推 TG。定时任务的入口 skill。
allowed-tools:
  - read
  - write
  - edit
  - glob
  - exec
  - webfetch
triggers:
  - user
---

# 完整分析流程（编排入口）

依次执行以下步骤，本 skill 是流程说明，各步骤调对应子 skill 或 cli.py 命令。

## 输入参数

| 参数 | 说明 |
|------|------|
| 项目路径 | **必填**。项目根目录的绝对路径（含 `invest` 启动脚本的目录），所有 `./invest` 命令与 runs/ 路径都以此为 cwd |

**未提供项目路径时必须先向用户确认，不要自行猜测或使用当前工作目录。**

`./invest` 是自举脚本：首次运行自动建 `.venv` 并装依赖，之后等价于 `.venv/bin/python cli.py`。

## 步骤

### 1. 过滤原始资讯

```bash
./invest filter
```

### 2. 粗筛

执行 `invest-triage` skill，产出 `runs/tmp/triage_{date}_{hour}.json`。

### 3. 逐条深度分析

读 triage JSON，取 `to_analyze` 中 `score >= 3` 的条目，按分数降序、最多 15 条。逐条调用 `invest-analyze` skill，把该条 item 以 JSON 形式传入 prompt：

```
输入事件：
\`\`\`json
{item 的完整 JSON}
\`\`\`
```

每条产出写入 `runs/tmp/analyze_{id}.md`。单条失败不中断，继续下一条。

### 4. 存档

```bash
./invest archive
```

拼接 analyze_*.md + triage 写入 `runs/news_deep/{date}_{hour}.md`，自动清理 tmp 和 briefing 过期行。命令输出行即存档路径。

### 5. 更新简报

执行 `invest-briefing` skill，传入步骤 4 的存档文件名。

### 6. 推送

```bash
./invest push
```

把最新存档中 ≥3★ 条目推 Telegram；未配置 TG 则脚本自动跳过。

## 失败处理

- 步骤 2 失败 → 终止本轮，报告原因
- 步骤 3 中单条失败 → 该条标记后继续
- 步骤 4-6 失败 → 报告 stderr，不静默吞错
