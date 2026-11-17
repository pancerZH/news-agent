# news-agent · 交互会话引导

本项目把 **科技/AI 产业链 + 宏观经济** 新闻提炼成每日数据包，由 Agent Harness 分析，并在 `~/mindslice` 的 `invest/*` 命名空间里追踪长期投资逻辑。

## 常用命令

| 命令 | 作用 |
| :--- | :--- |
| `uv run news-agent prep` | 抓取信源 → 去重入库 → GPU 编码 → 72h 聚类 → `data/packets/YYYY-MM-DD.md` |
| `uv run news-agent run [--harness agy\|claude\|codex] [--no-stage]` | 必要时先 prep，再拉起 Harness 写 `reports/YYYY-MM-DD.md`，最后 verify |
| `uv run news-agent show C123` | 打印一个事件簇的全部条目与 HN 评论 |
| `uv run news-agent search 关键词 [--days 30]` | 历史条目关键词检索 |
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
- 交互会话中经用户确认的变更直接写 `@overlay/invest/*`；定时运行的写入进入 `staging/`，用 `mindslice diff` / `mindslice apply` 审阅。
- 归档：`mindslice bake -w ~/mindslice @invest --prefix invest/`。

## 定时运行（示例，需手动安装）

`~/.config/systemd/user/news-agent.service`：

```ini
[Service]
Type=oneshot
WorkingDirectory=/path/to/news-agent
ExecStart=%h/.local/bin/uv run news-agent run
```

`~/.config/systemd/user/news-agent.timer`：

```ini
[Timer]
OnCalendar=*-*-* 07:30
Persistent=true

[Install]
WantedBy=timers.target
```

启用：`systemctl --user daemon-reload && systemctl --user enable --now news-agent.timer`。也可在 Harness 内用 `/schedule` 定时执行 `uv run news-agent run`。

## 讨论纪律

- 区分一手事实、社区检验与观点；结论引用簇编号 `C###` 或来源链接。
- 评估主线时给出 `强化 / 削弱 / 中性观察` 与是否触及 Kill Criteria，不给每日买卖建议。
