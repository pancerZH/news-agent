# news-agent

[English](README.md) | 中文

本项目把 **科技/AI 产业链 + 宏观经济** 新闻提炼成每日数据包，由 Agent Harness 分析，并在 `~/mindslice` 的 `invest/*` 命名空间里追踪长期投资逻辑。

## 常用命令

| 命令 | 作用 |
| :--- | :--- |
| `uv run news-agent prep` | 抓取信源 → 去重入库 → 抽取新访谈文稿的观点 → GPU 编码 → 72h 聚类 → `data/packets/YYYY-MM-DD.md` |
| `uv run news-agent run [--harness agy\|claude\|codex] [--no-review]` | 必要时先 prep，再拉起 Harness 写 `reports/YYYY-MM-DD.md`，最后 verify 并审阅提出的记忆变更 |
| `uv run news-agent show C123` | 打印一个事件簇的全部条目与 HN 评论 |
| `uv run news-agent read 4567` | 打印一个条目的全文（数据包会标注有全文的条目）；访谈文稿带段落号 |
| `uv run news-agent claims TC12` / `claims #2989` | 打印访谈观点的原文引文、说话人与归属依据 |
| `uv run news-agent voicecheck TC12` | 对无说话人标注字幕里的观点，把它的声音和已具名说话人的样本片段比对（首次会下载节目音频） |
| `uv run news-agent search 关键词 [--days 30]` | 历史条目关键词检索 |
| `uv run news-agent review` | 让独立会话审阅暂存的 `invest/*` 变更，由程序执行 apply/discard（`run` 结束后自动执行） |
| `uv run news-agent propose changes.json` | Harness 修改记忆的唯一途径：检查 `invest/*` 变更（JSON，字段同 MindSlice `write`）后暂存待审 |
| `uv run news-agent mem search\|read\|diff ...` | Harness 在命令行读记忆的途径：只读，工作区取自 `config.toml` |
| `uv run news-agent runs` | 最近的运行记录、日志目录与续聊命令 |
| `uv run news-agent verify` | `invest/` 记忆护栏校验（无 always、卡片都带 `invest` 标签、validate 零错误、无断链） |
| `uv run news-agent init-memory` | 幂等创建 watchlist / 宏观 / 主线 / 实体种子卡 |

访谈文稿：`prep` 会识别带说话人标注的全文（Dwarkesh、Stratechery、ChipStrat、Fabricated Knowledge），以及 `vtt = true` 信源节目页面里链接的 beehiiv 字幕。近 `[transcripts] lookback_days` 天发布、关注池实体与主题词密度够高的文稿，每篇起一个 harness 会话（每次 prep 最多 `max_per_run` 篇），抽取带说话人、类型（事实、预测、观点、主持人前提、转述）和逐字引文的观点，引文必须出现在所引段落里。数据包在「访谈观点」一节列出；没有说话人标注的字幕，除非文中点名，只归到机构或角色，可以用 `voicecheck` 按声纹核实。

运行后在本地生成（已被 `.gitignore` 忽略）：最新日报 `reports/latest.md`、当日数据包 `data/packets/<今天>.md`，以及 `data/lookups.jsonl`：每次 `search`/`show`/`read` 调用记一行，用来区分 agent 看过的簇和没看到的簇。

## 记忆隔离契约（`~/mindslice`）

- 只读写 `invest/` 前缀：`invest/watchlist`、`invest/macro/<slot>`、`invest/theses/<slot>`、`invest/entities/<ticker 小写>`；每张卡 `tags` 含 `"invest"`。
- **禁止** `load="always"`，一律 `as-needed`。
- 只记录能强化、削弱或证伪长期假设、或改变实体 / 宏观格局的慢变量；流水新闻留在 `data/news.sqlite`。
- 主线卡正文：`核心假设 (Premise)` / `当前状态与置信度 (Conviction)` / `关键验证记录 (Evidence)`（`[YYYY-MM-DD] ...`）/ `预设证伪条件 (Kill Criteria)`。
- 实体卡 `aliases` 必填中英文名、Ticker、核心产品代号（便于 BM25 词法召回）。
- 链接：已在 `@invest` 里的卡片写 `[[@invest/invest/...]]`，只在 `@overlay` 里的写 `[[invest/...]]`。
- 交互会话中经用户确认的变更直接写 `@overlay/invest/*`；Harness 运行只能经 `news-agent propose` 写记忆，它逐条检查（卡片 id、op、`load`、`invest` 标签、链接；主线卡不许 append，必须保留四段，任何改动都不能丢掉带日期的历史证据）后一律暂存。agy 会话里 MindSlice 自带的 write 和整个 `mindslice` 命令行都被禁用（`config.toml` 里的 `--settings` deny 规则），读记忆用 MCP search/read 或 `news-agent mem`；`verify` 仍会标出运行期间除暂存提议以外的任何 `invest/` 改动（直写、apply、discard 或 bake）。运行结束后由独立审阅会话逐条判断（出处、慢变量、去重、结构、结论克制），审阅 Agent 只写决定，由程序只对 `@overlay/invest/*` 执行 apply/discard；审阅期间 `invest/` 下若有改动则不执行任何决定。记忆库与其他会话共用，运行或审阅期间 `invest/` 以外的写入只打印为警告（`~`），不会让运行失败。审阅没有完成（决定被拦下、保留待审或执行失败）时，另发一封简短通知给发信账号本身（或 `smtp.toml` 里的 `notify`），不发给日报的 `to` 列表。审阅记录在 `data/runs/*-review-*/review.md`。
- 归档：`mindslice bake -w ~/mindslice @invest --prefix invest/`。

## 定时运行（systemd 用户定时器示例：每天 07:30 早报、18:00 晚报，按系统本地时间）

每天两期：`run` 按当前时间自动取 `--edition am|pm`（中午前为 am），数据包、事件簇、报告都按期号存放（如 `reports/2026-11-16-am.md`）。晚报的 Prompt 会带上上一期报告，只写新增信息与进展。邮件标题为「科技/AI + 宏观早报 / 晚报 · 日期」。

unit 在 `systemd/` 里。在仓库目录下安装（`@REPO@` 换成仓库路径）：

```sh
mkdir -p ~/.config/systemd/user
sed "s#@REPO@#$PWD#" systemd/news-agent.service > ~/.config/systemd/user/news-agent.service
cp systemd/news-agent.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now news-agent.timer
```

- 要改运行时间，改 `systemd/news-agent.timer` 里的 `OnCalendar`，再安装一次。
- 查看下次触发：`systemctl --user list-timers news-agent.timer`；日志：`journalctl --user -u news-agent`。
- 手动立即跑一次：`systemctl --user start news-agent.service`；停用：`systemctl --user disable --now news-agent.timer`。
- 机器关机或休眠错过的时间点，会在开机后补跑（`Persistent=true`）；如需在未登录时也运行，执行 `loginctl enable-linger`。

## 讨论纪律

- 区分一手事实、社区检验与观点；结论引用簇编号 `C###` 或来源链接。
- 评估主线时给出 `强化 / 削弱 / 中性观察` 与是否触及 Kill Criteria，不给每日买卖建议。
