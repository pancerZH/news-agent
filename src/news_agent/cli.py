"""news-agent CLI: init-memory / prep / run / review / search / show / read / verify / runs / mail."""

import argparse
import json
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import collect, db, embed_cluster, mail, market, runner

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.toml"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def log_lookup(path: Path, cmd: str, args: list[str]) -> None:
    """Append one search/show/read call: tells clusters the agent looked at but did not cite from unseen ones."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cmd": cmd, "args": args}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def load_config(path: Path) -> tuple[dict, Path]:
    root = path.resolve().parent
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg["feeds"] = tomllib.loads((root / cfg["paths"]["feeds"]).read_text(encoding="utf-8"))["feeds"]
    return cfg, root


def iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


EDITIONS = {"am": "早报", "pm": "晚报"}


def issue_of(day: str, edition: str | None) -> tuple[str, str]:
    """(issue key such as 2026-11-16-am, report title). Without an edition: am before noon, pm after."""
    edition = edition or ("am" if datetime.now().hour < 12 else "pm")
    return f"{day}-{edition}", f"科技/AI + 宏观{EDITIONS[edition]} · {day}"


def previous_report(reports: Path, issue: str) -> Path | None:
    older = sorted(p for p in reports.glob("????-??-??-??.md") if p.stem < issue)
    return older[-1] if older else None


# ---------------------------------------------------------------- prep


def prep(cfg: dict, root: Path, issue: str) -> Path:
    conn = db.connect(root / cfg["paths"]["db"])
    since = iso_hours_ago(cfg["collect"]["window_hours"])

    log(f"collect: {len(cfg['feeds'])} feeds" + (" + Hacker News" if cfg["hn"]["enabled"] else ""))
    items, errors = collect.collect_all(cfg["feeds"], cfg)
    last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM items").fetchone()[0]
    log(f"collect: {len(items)} entries, {db.upsert_items(conn, items)} new, failed: {sorted(errors) or 'none'}")

    c = cfg["collect"]
    wanted = {f["id"] for f in cfg["feeds"] if f.get("fulltext")}
    bare = [r for r in conn.execute("SELECT id, url, summary, source FROM items WHERE id > ? AND published >= ?"
                                    " AND content = ''", (last_id, since)) if r["source"] in wanted]
    if bare:
        with ThreadPoolExecutor(c["workers"]) as pool:
            texts = list(pool.map(lambda r: collect.fulltext(r["url"], c["fulltext_max_chars"], c["timeout_seconds"]), bare))
        with conn:
            for r, text in zip(bare, texts):
                if len(text) > len(r["summary"]):
                    conn.execute("UPDATE items SET content = ?, summary = ? WHERE id = ?",
                                 (text, collect.summary_of(r["summary"], text, c["fulltext_min_chars"]), r["id"]))
        log(f"fulltext: fetched {sum(len(t) > len(r['summary']) for r, t in zip(bare, texts))} of {len(bare)} items")

    snap = None
    if cfg["market"]["enabled"]:
        snap = market.snapshot(cfg["market"]["indices"], cfg["watchlist"]["tickers"], c["timeout_seconds"])
        db.save_snapshot(conn, issue, snap)
        log("market: " + ", ".join(k for k in ("treasury", "indices", "watchlist") if snap.get(k)))

    n = embed_cluster.embed_missing(conn, db.window_items(conn, since), cfg["embedding"])
    log(f"embed: {n} items encoded with {cfg['embedding']['model']}")
    clusters = embed_cluster.build_clusters(db.window_items(conn, since), cfg)
    for cl, cid in zip(clusters, db.save_clusters(conn, issue, clusters)):
        cl["id"] = cid
    packet = root / cfg["paths"]["packets"] / f"{issue}.md"
    packet.parent.mkdir(parents=True, exist_ok=True)
    packet.write_text(embed_cluster.render_packet(issue, since, clusters, market.render(snap), errors,
                                                  cfg["cluster"]["top_k"], cfg["cluster"]["macro_k"]), encoding="utf-8")
    log(f"cluster: {len(clusters)} clusters -> {packet}")
    return packet


# ---------------------------------------------------------------- memory seeding


def init_memory(cfg: dict, root: Path) -> list[str]:
    """Create the seed invest/* cards that do not exist yet (in any mount). Returns the ids created."""
    mem = cfg["memory"]
    seeds = tomllib.loads((root / cfg["paths"]["seeds"]).read_text(encoding="utf-8"))["cards"]
    tickers, entities = cfg["watchlist"]["tickers"], cfg["entities"]
    seed_links = ", ".join(f"[[{s['id']}]]" for s in seeds)
    cards = [{"id": "invest/watchlist", "title": "投资关注池 (Watchlist)",
              "summary": "当前关注标的: " + ", ".join(tickers) + "；核心主线与宏观卡见正文链接。",
              "aliases": ["关注池", "标的池", "关注标的"],  # no "watchlist": it would make invest/watchlist ambiguous with @invest
              "content": ["- **关注标的**: " + ", ".join(f"{t} ({entities[t][0]})" for t in tickers),
                          f"- **核心主线与宏观卡**: {seed_links}",
                          "- **仓位观察重点**: 待在交互会话中补充。"]}]
    cards += seeds
    for t in tickers:
        name = entities[t][0]
        cards.append({"id": f"invest/entities/{t.lower()}", "title": f"{name} ({t})",
                      "summary": f"{name}（{t}）实体卡：在 AI 算力与云产业链中的角色、关键产品与验证记录。",
                      "aliases": list(dict.fromkeys([t, *entities[t]])),
                      "content": [f"- **Ticker**: {t}；别名与核心产品代号: {', '.join(entities[t])}",
                                  "- **产业链角色与卡脖子环节**: 待在交互会话中补充。",
                                  "- **所属关注池**: [[invest/watchlist]]"]})
    created = []
    for card in cards:
        if runner.mindslice(mem, "read", f"./{card['id']}").returncode == 0:
            continue
        kind = card["id"].split("/")[1] if card["id"].count("/") > 1 else "watchlist"
        args = ["write", card["id"], "--op", "create", "--title", card["title"], "--summary", card["summary"],
                "--content", "\n".join(card["content"]), "--load", "as-needed", "--tag", "invest", "--tag", kind,
                "--reason", "news-agent init-memory: seed card from config.toml / memory_seeds.toml defaults"]
        for a in card.get("aliases", []):
            args += ["--alias", a]
        res = runner.mindslice(mem, *args)
        if res.returncode != 0:
            raise SystemExit(f"mindslice write {card['id']} failed: {res.stderr or res.stdout}")
        created.append(card["id"])
    return created


# ---------------------------------------------------------------- mail


def deliver(cfg: dict, title: str, report: Path, status: str = "ok") -> bool:
    """Email the report if mail is enabled and configured. Returns False only when sending failed."""
    smtp = Path(cfg["mail"]["smtp"]).expanduser()
    if not cfg["mail"]["enabled"]:
        return True
    if not smtp.exists():
        log(f"mail: skipped, {smtp} not found")
        return True
    try:
        log(f"mail: sent to {', '.join(mail.send(report, title, smtp, status))}")
        return True
    except Exception as e:
        log(f"mail: failed: {type(e).__name__}: {e}")
        return False


def record_run(cfg: dict, root: Path, meta: dict) -> None:
    conn = db.connect(root / cfg["paths"]["db"])
    with conn:
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (meta["run_id"], meta["day"], meta["harness"], meta["started"], meta["finished"], meta["exit_code"],
                      meta["session_id"], meta["resume"], meta["status"], meta["run_dir"], meta["report"]))


def review(cfg: dict, root: Path, issue: str, harness: str, packet: Path, report: Path) -> bool:
    """Run the memory review session and print its outcome. Returns False when it had problems."""
    meta = runner.run_review(cfg, root, issue, harness, packet, report)
    if meta["run_id"] is None:
        log("review: no staged invest/* changes")
        return True
    record_run(cfg, root, meta)
    counts = {k: sum(d["decision"] == k for d in meta["decisions"]) for k in ("apply", "discard", "pending", "failed")}
    print(f"review: {meta['status']} · applied {counts['apply']}, discarded {counts['discard']}, "
          f"pending {counts['pending']}, failed {counts['failed']}\n  log: {meta['report']}")
    for prob in meta["problems"]:
        print(f"  ! {prob}")
    return meta["status"] == "ok"


# ---------------------------------------------------------------- search / show / runs


def load_cluster(conn, cluster_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "title": row["title"], "score": row["score"], "entities": json.loads(row["entities"]),
            "rows": db.cluster_items(conn, cluster_id)}


def search(conn, terms: list[str], days: int, limit: int) -> list:
    where = " AND ".join(["(title LIKE ? OR summary LIKE ? OR content LIKE ?)"] * len(terms))
    params = [p for t in terms for p in (f"%{t}%",) * 3]
    return conn.execute(
        f"SELECT i.*, (SELECT MAX(ci.cluster_id) FROM cluster_items ci WHERE ci.item_id = i.id) AS cluster_id"
        f" FROM items i WHERE published >= ? AND {where} ORDER BY published DESC LIMIT ?",
        [iso_hours_ago(days * 24), *params, limit]).fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="news-agent", description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-memory", help="seed invest/* cards in the MindSlice workspace (idempotent)")
    edition = {"choices": sorted(EDITIONS), "help": "am (早报) or pm (晚报); default: by the current hour"}
    p = sub.add_parser("prep", help="collect, embed, cluster and write this edition's packet")
    p.add_argument("--day", default=date.today().isoformat())
    p.add_argument("--edition", **edition)
    p = sub.add_parser("run", help="prep (if needed), run the daily brief in a harness, then verify")
    p.add_argument("--harness")
    p.add_argument("--day", default=date.today().isoformat())
    p.add_argument("--edition", **edition)
    p.add_argument("--prep", action="store_true", help="re-run prep even if this edition's packet exists")
    p.add_argument("--no-stage", action="store_true", help="let the harness write memory directly (no staging)")
    p = sub.add_parser("review", help="let an independent session review the staged invest/* memory changes")
    p.add_argument("--harness")
    p = sub.add_parser("search", help="keyword search over collected items")
    p.add_argument("terms", nargs="+")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("show", help="print a cluster in full")
    p.add_argument("cluster", help="cluster id, e.g. C123 or 123")
    p = sub.add_parser("read", help="print the full text of collected items")
    p.add_argument("items", nargs="+", help="item ids, as `show` and the packet give them")
    p = sub.add_parser("verify", help="check the invest/* memory guardrails")
    p.add_argument("--since", help="also check history events since this ISO time")
    p.add_argument("--staged-only", action="store_true", help="with --since: direct (unstaged) writes are problems")
    sub.add_parser("runs", help="list recent harness runs and how to resume them")
    p = sub.add_parser("mail", help="email a report (without the memory-change section)")
    p.add_argument("--day", default=date.today().isoformat())
    p.add_argument("--edition", **edition)
    p.add_argument("--html", type=Path, help="write the HTML email body to this file instead of sending")
    args = ap.parse_args(argv)
    cfg, root = load_config(args.config)

    if args.cmd == "init-memory":
        created = init_memory(cfg, root)
        print(f"created {len(created)} cards" + "".join(f"\n  {c}" for c in created))
        return 0
    if args.cmd in ("prep", "run", "mail"):
        issue, title = issue_of(args.day, args.edition)
    if args.cmd == "prep":
        print(prep(cfg, root, issue))
        return 0
    if args.cmd == "run":
        harness = args.harness or cfg["harness"]["default"]
        packet = root / cfg["paths"]["packets"] / f"{issue}.md"
        if args.prep or not packet.exists():
            prep(cfg, root, issue)
        stage = cfg["memory"]["stage_scheduled_writes"] and not args.no_stage
        report = root / cfg["paths"]["reports"] / f"{issue}.md"
        previous = previous_report(report.parent, issue)
        log(f"run: {issue} with {harness} (stage={stage}, previous={previous.name if previous else None}) ...")
        meta = runner.run_harness(cfg, root, issue, title, harness, stage, packet, report, previous)
        record_run(cfg, root, meta)
        print(f"status: {meta['status']}\nreport: {meta['report']}\nlogs:   {meta['run_dir']}\nresume: {meta['resume']}")
        for prob in meta["problems"]:
            print(f"  ! {prob}")
        ok = deliver(cfg, title, report, meta["status"]) if meta["report"] else True
        if stage and cfg["review"]["enabled"]:
            ok = review(cfg, root, issue, harness, packet, report) and ok
        return 0 if meta["status"] == "ok" and ok else 1

    if args.cmd == "review":
        report = (root / cfg["paths"]["reports"] / "latest.md").resolve()
        packet = root / cfg["paths"]["packets"] / f"{report.stem}.md"
        return 0 if review(cfg, root, report.stem, args.harness or cfg["harness"]["default"], packet, report) else 1

    if args.cmd == "mail":
        report = root / cfg["paths"]["reports"] / f"{issue}.md"
        if not report.exists():
            print(f"no report {report}", file=sys.stderr)
            return 1
        if args.html:
            text = mail.public_text(report.read_text(encoding="utf-8"))
            args.html.write_text(mail.render_html(text, title), encoding="utf-8")
            print(args.html)
            return 0
        if not Path(cfg["mail"]["smtp"]).expanduser().exists():
            print(f"missing {cfg['mail']['smtp']}", file=sys.stderr)
            return 1
        return 0 if deliver(cfg, title, report) else 1

    conn = db.connect(root / cfg["paths"]["db"])
    lookup = {"search": "terms", "show": "cluster", "read": "items"}.get(args.cmd)
    if lookup:
        value = getattr(args, lookup)
        log_lookup(root / cfg["paths"]["lookups"], args.cmd, value if isinstance(value, list) else [value])
    if args.cmd == "search":
        for r in search(conn, args.terms, args.days, args.limit):
            cl = f"C{r['cluster_id']}" if r["cluster_id"] else "-"
            print(f"{r['published'][:10]} {cl:>6} [{r['source']}] {r['title']}\n{'':18}{r['url']}")
        return 0
    if args.cmd == "show":
        c = load_cluster(conn, int(args.cluster.lstrip("Cc")))
        if c is None:
            print(f"no cluster {args.cluster}", file=sys.stderr)
            return 1
        print("\n".join(embed_cluster.render_cluster(c, max_items=None, summary_chars=3000)))
        return 0
    if args.cmd == "read":
        for item_id in args.items:
            r = conn.execute("SELECT * FROM items WHERE id = ?", (int(item_id.lstrip("#")),)).fetchone()
            if r is None:
                print(f"no item {item_id}", file=sys.stderr)
                return 1
            print(f"# {r['title']}\n\n{r['source']} · {r['published'][:10]} · {r['url']}\n\n{r['content'] or r['summary']}\n")
        return 0
    if args.cmd == "verify":
        problems = runner.verify(cfg["memory"], since=args.since, staged_only=args.staged_only)
        print("\n".join(f"! {p}" for p in problems) or "ok: invest/* memory guardrails pass")
        return 1 if problems else 0
    if args.cmd == "runs":
        for r in conn.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 20"):
            print(f"{r['id']}  {r['status']}\n  report: {r['report']}\n  logs:   {r['run_dir']}\n  resume: {r['resume']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
