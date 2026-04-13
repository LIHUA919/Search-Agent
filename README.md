# Weekly Tech Collector

每周抓取一次 GitHub Trending 和 Hacker News 热门内容，生成 Markdown 周报，并推送到 Telegram。

## 功能

- 抓取 GitHub 每周热门仓库
- 抓取 Hacker News 热门讨论
- 生成本地 Markdown 周报
- 推送摘要到 Telegram
- 适合通过 `cron` 每周执行一次

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

给脚本执行权限：

```bash
chmod +x run_weekly.sh
```

编辑 `crontab`：

```bash
crontab -e
```

每周一上午 8 点执行一次：

```cron
0 8 * * 1 /Users/lihua/projects/weekly-tech-collector/run_weekly.sh >> /Users/lihua/projects/weekly-tech-collector/collector.log 2>&1
```

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
