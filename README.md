# news-agent

English | [中文](README-zh.md)

news-agent distills **tech/AI supply-chain + macroeconomic** news into a daily data packet, has an agent harness analyze it, and tracks long-term investment theses in the `invest/*` namespace of the `~/mindslice` memory workspace.

## Commands

| Command | What it does |
| :--- | :--- |
| `uv run news-agent prep` | Fetch feeds → dedupe into the database → GPU embedding → 72h clustering → `data/packets/YYYY-MM-DD.md` |
| `uv run news-agent run [--harness agy\|claude\|codex] [--no-stage]` | Run prep if needed, launch the harness to write `reports/YYYY-MM-DD.md`, then verify |
| `uv run news-agent show C123` | Print every item and HN comment of one event cluster |
| `uv run news-agent read 4567` | Print the full text of an item (the packet marks items that have one) |
| `uv run news-agent search <keyword> [--days 30]` | Keyword search over past items |
| `uv run news-agent review` | Have an independent session review the staged `invest/*` changes; the program executes apply/discard (runs automatically after `run`) |
| `uv run news-agent runs` | Recent runs, their log directories and resume commands |
| `uv run news-agent verify` | Memory guardrail checks (no always-loaded cards, tag/prefix compliance, zero validate errors, no broken links) |
| `uv run news-agent init-memory` | Idempotently create the watchlist / macro / thesis / entity seed cards |

Generated locally at run time (ignored by `.gitignore`): the latest report `reports/latest.md`, today's packet `data/packets/<today>.md`, and `data/lookups.jsonl`, one line per `search`/`show`/`read` call (to tell clusters the agent looked at from ones it never saw).

## Memory isolation contract (`~/mindslice`)

- Read and write only the `invest/` prefix: `invest/watchlist`, `invest/macro/<slot>`, `invest/theses/<slot>`, `invest/entities/<lowercase ticker>`; every card's `tags` include `"invest"`.
- **Never** `load="always"`; always `as-needed`.
- Record only slow variables that strengthen, weaken or falsify a long-term thesis, or change an entity or the macro regime; day-to-day news stays in `data/news.sqlite`.
- Thesis card body: `核心假设 (Premise)` / `当前状态与置信度 (Conviction)` / `关键验证记录 (Evidence)` (`[YYYY-MM-DD] ...`) / `预设证伪条件 (Kill Criteria)`.
- Entity card `aliases` must include the Chinese and English names, the ticker and core product code names (for BM25 lexical recall).
- Links: within `@overlay` write `[[invest/...]]`; for cards already baked into `@invest` write `[[@invest/invest/...]]`.
- Changes the user confirms in an interactive session go straight to `@overlay/invest/*`. Writes from scheduled runs go to `staging/`; after the run an independent review session judges each one (provenance, slow variable, deduplication, structure, restrained conclusions). The review agent only writes decisions, and the program executes apply/discard on `@overlay/invest/*` only; if the memory workspace changes during the review, no decision is executed. Review records are in `data/runs/*-review-*/review.md`.
- Archiving: `mindslice bake -w ~/mindslice @invest --prefix invest/`.

## Scheduled runs (systemd user timer example: morning edition at 07:30, evening edition at 18:00, system local time)

Two editions a day: `run` picks `--edition am|pm` from the current time (am before noon), and packets, event clusters and reports are stored per edition (e.g. `reports/2026-11-16-am.md`). The evening prompt includes the previous report and covers only new information and developments. The email subject is `科技/AI + 宏观早报 / 晚报 · <date>` (morning / evening edition).

The units are in `systemd/`. Install them from the checkout (`@REPO@` becomes its path):

```sh
mkdir -p ~/.config/systemd/user
sed "s#@REPO@#$PWD#" systemd/news-agent.service > ~/.config/systemd/user/news-agent.service
cp systemd/news-agent.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now news-agent.timer
```

- To change the times, edit `OnCalendar` in `systemd/news-agent.timer` and install it again.
- Next trigger: `systemctl --user list-timers news-agent.timer`; logs: `journalctl --user -u news-agent`.
- Run once now: `systemctl --user start news-agent.service`; disable: `systemctl --user disable --now news-agent.timer`.
- Runs missed while the machine was off or asleep are made up after boot (`Persistent=true`); to run while you are not logged in, run `loginctl enable-linger`.

## Discussion discipline

- Separate first-hand facts, community validation and opinion; cite conclusions with cluster IDs `C###` or source links.
- When assessing a thesis, give `强化 / 削弱 / 中性观察` (strengthen / weaken / neutral observation) and whether it touches the Kill Criteria; no daily buy/sell advice.
