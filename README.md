# Weekly Tech Collector

每周生成一份不超过 8 条的技术周报，并推送到 Telegram。

## 功能

- GitHub Trending：最多 3 条
- Hacker News：最多 2 条
- 关注项目的正式 GitHub Release：最多 2 条
- Hugging Face Daily Papers Radar：最多 1 条合格论文
- 生成本地 Markdown 周报
- 推送摘要到 Telegram
- GitHub Actions 主调度，macOS 本地补偿调度

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

如果遇到 macOS Python 证书错误，可以先装 `certifi`：

```bash
python3 -m pip install certifi
```

如果你只是想先验证抓取链路是否可用，也可以临时关闭证书校验：

```bash
python3 collector.py --skip-telegram --insecure
```

输出文件会保存在 `output/` 目录。

## 定时执行

GitHub Actions 每周日北京时间 08:17 发送主通知。一次成功发送后会提交一个不含敏感信息的心跳文件，防止公开仓库因长期不活跃而自动停用定时工作流。

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
- Telegram 默认按纯文本发送，避免 Markdown 转义问题
