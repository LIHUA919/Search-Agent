# Weekly Tech Collector + Agent Radar

同一个采集器提供两条互不干扰的路径：现有每周技术周报照常推送到 Telegram；新增的 Agent Radar 按需生成最多 5 条、带证据等级和 X 写作角度的每日草稿。

## 功能

- GitHub Trending：最多 3 条
- Hacker News：最多 2 条
- 关注项目的正式 GitHub Release：最多 2 条
- Hugging Face Daily Papers Radar：最多 1 条合格论文
- 生成本地 Markdown 周报
- 推送摘要到 Telegram
- GitHub Actions 主调度，macOS 本地补偿调度
- Agent Radar：官方 GitHub Release + `AI-Agents-Daily-Research` 每日 arXiv 数据
- 统一 Agent Signal 字段：来源、时间、主题、优先级、证据等级、摘要和 X 草稿角度
- Agent Radar 只写本地 Markdown，不自动发 Telegram，也不自动发布到 X

## 环境

- Python 3.11+

## 配置

1. 复制环境变量模板：

```bash
cp .env.example .env
```

2. 填写：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

3. 在 `watchlist.json` 填写你明确想关注的 GitHub 项目：

```json
{
  "github_releases": [
    "owner/repository"
  ]
}
```

默认空列表不会产生 Release 通知。请把这个普通配置文件提交到仓库，GitHub Actions 才能读取同一份关注清单。

Agent Radar 使用独立的 `agent_sources.json`。这里登记的是可审计的一手来源及其固定主题、优先级；默认包含 OpenAI Agents SDK、MCP、A2A 的正式 Release，以及本人的 `AI-Agents-Daily-Research` 每日 JSONL。修改来源必须走代码审查，不从运行参数临时扩大范围。

## 运行

先只生成报告，不推送 Telegram：

```bash
python3 collector.py --skip-telegram
```

临时指定另一份关注清单或调整条目上限：

```bash
python3 collector.py --skip-telegram --watchlist-file watchlist.json \
  --github-limit 3 --hn-limit 2 --release-limit 2 --hf-limit 1
```

正常执行：

```bash
python3 collector.py
```

生成 Agent Radar 草稿：

```bash
python3 collector.py --agent-radar
```

默认读取最近 2 天，最多保留 5 条。可以在人工运行时收窄窗口或条目数：

```bash
python3 collector.py --agent-radar --agent-window-days 1 --agent-limit 3
```

这个模式无论是否传入 `--skip-telegram` 都只生成草稿；发布到 X 始终需要人工核验事实、补充观点并手动发送。

如果遇到 macOS Python 证书错误，可以先装 `certifi`：

```bash
python3 -m pip install certifi
```

如果你只是想先验证抓取链路是否可用，也可以临时关闭证书校验：

```bash
python3 collector.py --skip-telegram --insecure
```

输出文件会保存在 `output/` 目录。周报以 `weekly-report-` 开头，Agent Radar 以 `daily-agent-radar-` 开头。

## 定时执行

GitHub Actions 每周日北京时间 08:17 发送主通知。一次成功发送后会提交一个不含敏感信息的心跳文件，防止公开仓库因长期不活跃而自动停用定时工作流。

Agent Radar v1 没有新增或修改任何定时任务；先通过人工运行验证信号质量。后续若启用每日调度，应单独评审，并继续保持“只产草稿、不自动发 X”的边界。

macOS 本地补偿任务在每周日 18:00 执行；如果当天 GitHub Actions 已成功发送，它会跳过，因此通常不会产生重复通知。它使用 `launchd`，在 Mac 睡眠时错过的运行会在唤醒后补跑。

安装或更新本地补偿任务：

```bash
mkdir -p ~/Library/LaunchAgents
cp launchd/com.lihua.weekly-tech-collector.plist ~/Library/LaunchAgents/
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.lihua.weekly-tech-collector.plist 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.lihua.weekly-tech-collector.plist
```

移除旧的 cron 条目，避免重复执行：

```bash
crontab -e
```

删除其中的 `weekly-tech-collector/run_weekly.sh` 行。

详细的信息预算、来源边界、调度和验收标准见 [DESIGN.md](DESIGN.md)。

## Telegram Chat ID

最简单的方法：

1. 在 Telegram 里给你的机器人发一条消息
2. 访问：

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

3. 在返回结果里找到 `chat.id`

## 说明

- GitHub Trending 不是官方 API，这里是从页面解析数据
- Hacker News 使用官方 Firebase API
- 关注项目的 Release 使用 GitHub 官方 REST API；草稿和预发布版本不会进入周报
- Hugging Face Daily Papers 仅筛选最近 7 天内主题相关、带公开资源且至少 5 个 upvote 的论文，最多推送 1 条
- Agent Radar 的官方 Release 标为 `first_party`；arXiv 条目标为 `author_preprint`，两者不会伪装成同一种证据
- `AI-Agents-Daily-Research` 仍负责广泛论文采集；本仓库只读取其公开 JSONL 并筛选 Agent 相关条目，不迁移历史文件
- Telegram 默认按纯文本发送，避免 Markdown 转义问题
