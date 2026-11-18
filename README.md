# news-agent

本项目把 **科技/AI 产业链 + 宏观经济** 新闻提炼成每日数据包，由 Agent Harness 分析，并在 `~/mindslice` 的 `invest/*` 命名空间里追踪长期投资逻辑。

## 常用命令

| 命令 | 作用 |
| :--- | :--- |
| `uv run news-agent prep` | 抓取信源 → 去重入库 → GPU 编码 → 72h 聚类 → `data/packets/YYYY-MM-DD.md` |
| `uv run news-agent run [--harness agy\|claude\|codex] [--no-stage]` | 必要时先 prep，再拉起 Harness 写 `reports/YYYY-MM-DD.md`，最后 verify |
| `uv run news-agent show C123` | 打印一个事件簇的全部条目与 HN 评论 |
| `uv run news-agent search 关键词 [--days 30]` | 历史条目关键词检索 |
| `uv run news-agent review` | 让独立会话审阅暂存的 `invest/*` 变更，由程序执行 apply/discard（`run` 结束后自动执行） |
| `uv run news-agent runs` | 最近的运行记录、日志目录与续聊命令 |
| `uv run news-agent verify` | 记忆护栏校验（无 always、标签/前缀合规、validate 零错误、无断链） |
| `uv run news-agent init-memory` | 幂等创建 watchlist / 宏观 / 主线 / 实体种子卡 |

运行后在本地生成（已被 `.gitignore` 忽略）：最新日报 `reports/latest.md`、当日数据包 `data/packets/<今天>.md`。

## 记忆隔离契约（`~/mindslice`）

- 只读写 `invest/` 前缀：`invest/watchlist`、`invest/macro/<slot>`、`invest/theses/<slot>`、`invest/entities/<ticker 小写>`；每张卡 `tags` 含 `"invest"`。
- **禁止** `load="always"`，一律 `as-needed`。
- 只记录能强化、削弱或证伪长期假设、或改变实体 / 宏观格局的慢变量；流水新闻留在 `data/news.sqlite`。
- 主线卡正文：`核心假设 (Premise)` / `当前状态与置信度 (Conviction)` / `关键验证记录 (Evidence)`（`[YYYY-MM-DD] ...`）/ `预设证伪条件 (Kill Criteria)`。
- 实体卡 `aliases` 必填中英文名、Ticker、核心产品代号（便于 BM25 词法召回）。
- 链接：同在 `@overlay` 写 `[[invest/...]]`；引用已烘焙进 `@invest` 的卡片写 `[[@invest/invest/...]]`。
- 交互会话中经用户确认的变更直接写 `@overlay/invest/*`；定时运行的写入进入 `staging/`，运行结束后由独立审阅会话逐条判断（出处、慢变量、去重、结构、结论克制），审阅 Agent 只写决定，由程序只对 `@overlay/invest/*` 执行 apply/discard；审阅期间记忆库若有改动则不执行任何决定。审阅记录在 `data/runs/*-review-*/review.md`。
- 归档：`mindslice bake -w ~/mindslice @invest --prefix invest/`。

## 定时运行（systemd 用户定时器示例：每天 07:30 早报、18:00 晚报，按系统本地时间）

每天两期：`run` 按当前时间自动取 `--edition am|pm`（中午前为 am），数据包、事件簇、报告都按期号存放（如 `reports/2026-11-16-am.md`）。晚报的 Prompt 会带上上一期报告，只写新增信息与进展。邮件标题为「科技/AI + 宏观早报 / 晚报 · 日期」。

`~/.config/systemd/user/news-agent.service`：

```ini
[Service]
Type=oneshot
WorkingDirectory=/path/to/news-agent
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/bin/uv run news-agent run
TimeoutStartSec=90min
Nice=5
```

`~/.config/systemd/user/news-agent.timer`：

```ini
[Timer]
OnCalendar=*-*-* 07:30:00
OnCalendar=*-*-* 18:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- 启用：`systemctl --user daemon-reload && systemctl --user enable --now news-agent.timer`。
- 查看下次触发：`systemctl --user list-timers news-agent.timer`；日志：`journalctl --user -u news-agent`。
- 手动立即跑一次：`systemctl --user start news-agent.service`；停用：`systemctl --user disable --now news-agent.timer`。
- 机器关机或休眠错过的时间点，会在开机后补跑（`Persistent=true`）；如需在未登录时也运行，执行 `loginctl enable-linger`。

## 讨论纪律

- 区分一手事实、社区检验与观点；结论引用簇编号 `C###` 或来源链接。
- 评估主线时给出 `强化 / 削弱 / 中性观察` 与是否触及 Kill Criteria，不给每日买卖建议。
