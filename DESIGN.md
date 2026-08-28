# Weekly Tech Collector Design

## 产品目标

在五分钟内读完一份不超过 8 条的周报。它优先回答“本周哪些变化会影响我”，而不是复述互联网的热门内容。

默认周报由三种互补信号组成：

| 栏目 | 上限 | 解决的问题 |
| --- | ---: | --- |
| GitHub Trending | 3 | 哪些开源项目正在快速获得关注 |
| Hacker News | 2 | 工程师社区正在讨论什么 |
| Watched Project Releases | 2 | 我已关注的工具是否有正式版本、重大变更或安全修复 |
| Hugging Face Daily Papers Radar | 1 | 哪篇近期 AI 论文同时具备相关性、公开资源和社区信号 |

没有合格条目时，栏目可以少于上限；系统绝不为凑数补充低价值信息。

## Agent Radar v1

Agent Radar 是并列的草稿模式，不替换周报，也不共享 Telegram 交付路径。它解决的是“每天有哪些 Agent 一手变化值得进一步验证和在 X 表达”，而不是再做一份泛 AI 新闻榜。

默认主题只有三类：

| 主题 | 关注的问题 |
| --- | --- |
| `control_plane` | 长时任务、并行 Agent、状态、handoff、审批、恢复与冲突 |
| `memory_context_skills` | Memory、Context、Skills、工具使用与协议边界 |
| `agent_evaluation` | 复杂问题成功率、成本、人工介入、恢复和可复现实验 |

### 来源注册表

`agent_sources.json` 是版本化、可审查的来源注册表。v1 支持两类输入：

- `github_releases`：官方项目的非 draft、非 prerelease Release，证据等级为 `first_party`；
- `github_daily_jsonl`：读取 `AI-Agents-Daily-Research` 的 `data/{date}.jsonl`，只保留标题或摘要命中 Agent 主题的 arXiv 条目，证据等级为 `author_preprint`。

两个仓库因此形成清晰分工：`AI-Agents-Daily-Research` 继续做广泛论文采集和历史保存；本仓库做统一筛选、证据标注和编辑草稿。v1 不复制历史数据，也不停止任何原有 Action。

### 统一信号契约

每条候选归一化为 `AgentSignal`：

```text
signal_id, title, url, publisher, source_kind, published_at,
topics, priority, evidence_level, summary, why_it_matters, x_angle
```

`signal_id` 由来源类型与 canonical URL 计算，保证同一来源在一个报告窗口内稳定去重。排序先看 P0/P1/P2，再看发布时间；最多输出 5 条，不为凑数补充弱相关内容。

X 角度是确定性的写作提示，不是事实结论。最终发布前仍需人工打开原始链接、核验具体主张、加入自己的实验或判断。程序没有 X token、发布接口或自动发送动作。

## 信息源边界

### GitHub Trending 与 Hacker News

保留现有抓取方式，但默认从各 10 条降至各 3 条。它们提供广泛的“发现”信号，而不是变更通知。

### 关注项目的 GitHub Releases

这是本期唯一新增的默认信息源。`watchlist.json` 中的每一项都是用户明确选择的 `owner/repository`，例如 `vllm-project/vllm`；系统只读取这些仓库的正式 GitHub Release。

- 报告窗口：运行时刻向前 7 天。
- 过滤：排除 draft 和 prerelease；按发布时间倒序。
- 上限：所有关注项目合计最多 2 条，且每个项目最多保留最新的 1 条。
- 内容：仓库、版本标签、官方 Release 链接、截断后的 release notes、发布日期。
- 失败策略：单个仓库的 API 失败只记录 warning，不阻断主周报。

```json
{
  "github_releases": [
    "owner/repository"
  ]
}
```

`watchlist.json` 是普通、可提交的配置，不包含 token 或聊天 ID。用户负责选择关注项目；默认空列表保证系统不会擅自扩大信息范围。

### Hugging Face Daily Papers Radar

默认最多贡献 1 条。它是发现近期 AI 论文和社区关注度的雷达，不代表同行评审或严格编辑精选。候选必须在最近 7 天进入 Daily Papers，并同时满足：

- 标题或摘要与 Agent、LLM、RAG、推理、多模态、代码或工具使用等 AI 工程主题相关；
- 提供 GitHub 或 Hugging Face 上的公开实现资源；
- 至少获得 5 个社区 upvote。

合格候选按 upvote、收录时间倒序选择；没有候选或接口失败时不显示该栏目，也不影响其他来源和 Telegram 交付。

arXiv、供应商新闻、云厂商更新、Reddit/X 和泛科技媒体同样不进入默认周报；它们要么和现有来源重复，要么噪声高于用户的阅读预算。

## 数据流

```text
watchlist.json ─┐
                ├─ GitHub Releases API ──> 7 天窗口 + 正式版过滤 ─┐
GitHub Trending ┼──────────────────────────────────────────────────┤
Hacker News ────┤                                                  ├─> 最多 8 条 Markdown
HF Daily Papers ┴─> 相关性 + 公开资源 + upvote 过滤 ───────────────┘
                                                                   └─> Telegram

agent_sources.json ──> 官方 GitHub Releases ─┐
                                             ├─> AgentSignal ─> P0/P1 + 时间排序 ─> Daily Agent Radar 草稿
AI-Agents-Daily-Research JSONL ─> 主题筛选 ───┘
```

当前实现不维护跨周的“已见条目”状态：Release 的 `published_at` 与 7 天窗口已经满足每周定时任务的需求。若未来改为不定期运行或增加会修订历史条目的来源，再引入持久化游标和 canonical URL 去重。

所有 HTTP 抓取对瞬时连接错误最多重试 3 次，间隔为 1 秒、2 秒；最终失败才向调用方报错。Release 抓取在这个基础上仍以单仓库为粒度降级。

## 调度与交付

GitHub Actions 是主调度器，每周日 08:17（北京时间）运行。一次成功的定时发送会提交无敏感信息的心跳文件，防止公开仓库因 60 天无活动而停用定时工作流。

macOS `launchd` 在每周日 18:00 执行本地补偿；若当天 GitHub Actions 已成功发送，本地任务跳过。`launchd` 在睡眠期间错过的日历任务会在唤醒后合并补跑。

Agent Radar v1 仅提供 `python3 collector.py --agent-radar` 手动入口。它始终只写 Markdown，既不调用 Telegram，也不发布 X。是否增加每日 Action 属于后续独立变更，需要先用实际草稿验证信号质量和重复率。

## 故障恢复

- GitHub Actions 显示 `disabled_inactivity` 时，运行 `gh workflow enable weekly-report.yml` 恢复；成功的定时发送会再次写入心跳，避免重复停用。
- 主信息源连续 3 次请求失败时，工作流失败且不会写入成功心跳；检查 Actions 日志后再重跑。
- 单个关注项目的 Release API 失败只写入 warning，主周报继续发送。
- 本地补偿任务的输出保存在 `collector.log`；可用 `launchctl print "gui/$(id -u)/com.lihua.weekly-tech-collector"` 检查加载状态。

## 验收标准

- 默认运行最多输出 8 条内容项。
- 空 watchlist 不增加 Release 栏目。
- 配置中的仓库只纳入 7 天内的非 draft、非 prerelease Release。
- Release API 的单仓库故障不影响 GitHub Trending、HN 或 Telegram 交付。
- HF Daily Papers 最多 1 条，且只纳入最近 7 天内同时满足主题、公开资源和社区信号的论文。
- HF Daily Papers API 故障不影响其他来源或 Telegram 交付。
- Agent Radar 注册表拒绝非法仓库、未知优先级和无界路径模板。
- 官方 Release 与研究预印本进入同一个 `AgentSignal` 契约，但保留不同证据等级。
- Agent Radar 最多 5 条，输出必须包含“发生了什么、为什么重要、证据、X 草稿角度”。
- Agent Radar 不调用 Telegram；现有每周参数、8 条预算和定时调度保持兼容。
- 每次变更通过单元测试、Python 编译和一次 `--skip-telegram` 实际抓取验证。

## 运行检查

```bash
python3 -m unittest discover -s tests -v
python3 collector.py --skip-telegram --insecure
python3 collector.py --agent-radar --insecure
gh workflow list --all
launchctl print "gui/$(id -u)/com.lihua.weekly-tech-collector"
```
