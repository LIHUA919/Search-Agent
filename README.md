# Weekly Tech Collector

每周抓取一次 GitHub Trending 和 Hacker News 热门内容，生成 Markdown 周报，并推送到 Telegram。

## 功能

- 抓取 GitHub 每周热门仓库
- 抓取 Hacker News 热门讨论
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

## 运行

先只生成报告，不推送 Telegram：

```bash
python3 collector.py --skip-telegram
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

详细的调度、恢复和故障策略见 [DESIGN.md](DESIGN.md)。

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
- Telegram 默认按纯文本发送，避免 Markdown 转义问题
