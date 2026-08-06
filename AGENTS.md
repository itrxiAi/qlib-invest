# Invest Project Rules

## 项目说明

本项目每小时从 Techmeme 和 Twitter 同步全球科技资讯，由 news-sync workflow 做去重、深度分析、重要性排序，更新局势简报并推送 Telegram。

## 资讯分析规则

- 每条资讯必须标注来源和时间
- 生成判断前必须用 WebSearch 验证关键事实
- 输出格式：因果链 → 影响传导 → 跨事件关联 → 反面假设 → 置信度（详见 workflow 的输出格式）
- 不确定的内容标注 [未验证] / [单一来源] / [机制未验证]
- 不要预测利好利空，不要说"利好XX/利空XX"
- 核心目标是帮读者快速判断"这事多大，值不值得花时间细看"

## 目录结构

- `runs/news_raw/` — 原始资讯归档（scheduler 写入）
- `runs/news_deep/` — 深度分析存档（workflow 产出）
- `runs/briefing.md` — 当前局势简报（跨 session 记忆）
- `runs/causal_rules.md` — 因果方向规则库（Phase 3 加载）
- `.windsurf/workflows/news-sync.md` — 资讯分析工作流

## 压缩注意事项

本项目的对话历史包含量化资讯分析内容，压缩时请特别注意保留：
- 关键市场数据和数字
- 资讯来源和时间
- 局势判断和置信度
- 未验证的假设

## Telegram 推送

TG 推送配置在 `.env` 文件中：
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

只推送评分 ≥4 的资讯。
