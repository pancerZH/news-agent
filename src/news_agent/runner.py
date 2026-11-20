"""Run the daily analysis in a local agent harness and verify the memory guardrails afterwards."""

import json
import os
import re
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

STRIP_PREFIXES = ("ANTIGRAVITY_", "CLAUDE_CODE_", "CODEX_", "MINDSLICE_PLUGIN_", "PLUGIN_")
STRIP_NAMES = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT"}
KEEP_NAMES = {"CODEX_HOME"}
BYPASS = {"applied": "apply", "discarded": "discard", "baked": "bake"}  # history events, as commands
SESSION_KEYS = ("session_id", "conversation_id", "thread_id", "sessionId", "conversationId")


def clean_env(env: dict | None = None) -> dict:
    """Drop variables a parent harness leaks into children, so the child starts a standalone session."""
    env = dict(os.environ if env is None else env)
    return {k: v for k, v in env.items()
            if k in KEEP_NAMES or (k not in STRIP_NAMES and not k.startswith(STRIP_PREFIXES))}


def _json_values(text: str):
    """Every JSON value in the output: the whole text, else one per line (stream-json / NDJSON)."""
    try:
        yield json.loads(text)
        return
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _find_key(value, keys):
    if isinstance(value, dict):
        for k in keys:
            if isinstance(value.get(k), str) and value[k]:
                return value[k]
        value = list(value.values())
    if isinstance(value, list):
        for v in value:
            found = _find_key(v, keys)
            if found:
                return found
    return None


def find_session_id(output: str) -> str | None:
    for value in _json_values(output):
        found = _find_key(value, SESSION_KEYS)
        if found:
            return found
    return None


def final_text(output: str) -> str | None:
    """The harness's final answer: claude `result`, agy `response`, codex last agent_message."""
    text = None
    for value in _json_values(output):
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("result"), str):
            text = value["result"]
        elif isinstance(value.get("response"), str):
            text = value["response"]
        elif (value.get("item") or {}).get("type") == "agent_message":
            text = value["item"].get("text") or text
    return text


def render_prompt(template: str, **values) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


# ---------------------------------------------------------------- memory guardrails


def mindslice(mem: dict, *args: str) -> subprocess.CompletedProcess:
    ws = str(Path(mem["workspace"]).expanduser())
    return subprocess.run([mem["mindslice"], "-w", ws, "--json", *args], capture_output=True, text=True,
                          env=clean_env(), stdin=subprocess.DEVNULL)


def card_meta(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("+++"):
        return {}
    return tomllib.loads(text.split("+++", 2)[1])


def invest_slice_dir(ws: Path) -> Path | None:
    mounts = tomllib.loads((ws / "mounts.toml").read_text(encoding="utf-8")).get("mounts", [])
    for m in mounts:
        if m.get("alias") == "@invest":
            return (ws / m["path"]).resolve()
    return None


def history_since(ws: Path, since: str) -> list[dict]:
    history = ws / ".history" / "changes.jsonl"
    if not history.exists():
        return []
    start = datetime.fromisoformat(since)
    events = (json.loads(line) for line in history.read_text(encoding="utf-8").splitlines())
    return [e for e in events if datetime.fromisoformat(e["timestamp"]) >= start]


def is_invest(event: dict) -> bool:
    """news-agent owns the cards under invest/; the rest of the workspace belongs to other writers."""
    return "address" in event and event["address"].split("/", 1)[1].startswith("invest/")


def others_since(ws: Path, since: str) -> list[str]:
    """Warnings for writes outside invest/ since `since`. Other sessions share the workspace, and history does not
    say who wrote what, so these are reported but never fail a run."""
    return [f"其他位置有写入（多半是其他会话）: {e['event']} {e['address']}"
            for e in history_since(ws, since) if "address" in e and not is_invest(e)]


def verify(mem: dict, since: str | None = None, staged_only: bool = False) -> list[str]:
    """Problems with the invest/* memory: always-loaded or untagged cards, any invest/ change other than a staged
    proposal in a scheduled run since `since` (a direct write, or an apply / discard / bake that skips the review),
    validate errors, broken links. Only invest/ is checked: tags are search facets that any
    writer may use, and writes elsewhere are other sessions' (see others_since)."""
    ws = Path(mem["workspace"]).expanduser()
    problems = []
    roots = [ws / "overlay", ws / "staging"]
    baked = invest_slice_dir(ws)
    for root in roots + ([baked] if baked else []):
        for md in sorted((root / "invest").rglob("*.md")):
            meta = card_meta(md)
            if meta.get("load") == "always":
                problems.append(f"load=\"always\" 禁止用于 invest 卡片: {md}")
            if "invest" not in meta.get("tags", []):
                problems.append(f"invest 卡片缺少 \"invest\" 标签: {md}")

    for e in history_since(ws, since) if since else []:
        if staged_only and is_invest(e) and e["event"] != "proposed":
            what = ("未经 staging 的直写" if e.get("origin") == "write"
                    else f"绕过审阅的 {BYPASS.get(e['event'], e['event'])}")
            problems.append(f"定时运行中{what}: {e['address']} ({e.get('op', '')})")

    res = mindslice(mem, "validate")
    try:
        report = json.loads(res.stdout)
    except json.JSONDecodeError:
        return problems + [f"mindslice validate 失败: {res.stderr.strip() or res.stdout.strip()}"]
    problems += [f"validate error: {e.get('message')} ({e.get('address') or e.get('file')})" for e in report["errors"]]
    problems += [f"invest 卡片断链: {w['message']} ({w['address']})" for w in report["warnings"]
                 if w.get("code") == "broken_wikilink" and "/invest/" in (w.get("address") or "")]
    return problems


# ---------------------------------------------------------------- harness run


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def launch(cfg: dict, root: Path, run_dir: Path, harness: str, prompt: str) -> dict:
    """One non-interactive harness session with logs in run_dir: exit_code, status, output, session_id, resume."""
    hcfg = cfg["harness"][harness]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    exit_code, status = None, "ok"
    with open(run_dir / "stdout.log", "w") as out, open(run_dir / "stderr.log", "w") as err:
        try:
            exit_code = subprocess.run([a.replace("{prompt}", prompt) for a in hcfg["cmd"]], cwd=root, env=clean_env(),
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       timeout=cfg["harness"]["timeout_minutes"] * 60).returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
        except FileNotFoundError as e:
            status = f"harness not found: {e}"
    if status == "ok" and exit_code != 0:
        status = f"harness exit code {exit_code}"
    output = (run_dir / "stdout.log").read_text(errors="replace")
    session_id = find_session_id(output)
    return {"exit_code": exit_code, "status": status, "output": output, "session_id": session_id,
            "resume": hcfg["resume"].format(session_id=session_id) if session_id else None,
            "cmd": [a if "{prompt}" not in a else a.replace("{prompt}", "<prompt.md>") for a in hcfg["cmd"]]}


def new_run_dir(cfg: dict, root: Path, name: str) -> tuple[str, Path]:
    run_id = f"{name}-{datetime.now().strftime('%H%M%S%f')[:-3]}"
    return run_id, root / cfg["paths"]["runs"] / run_id


def save_meta(run_dir: Path, meta: dict) -> dict:
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def run_harness(cfg: dict, root: Path, issue: str, title: str, harness: str, packet: Path, report: Path,
                previous: Path | None = None) -> dict:
    mem = cfg["memory"]
    run_id, run_dir = new_run_dir(cfg, root, f"{issue}-{harness}")
    report.parent.mkdir(parents=True, exist_ok=True)
    prompt = render_prompt((root / cfg["paths"]["prompt"]).read_text(encoding="utf-8"), date=issue[:10], title=title,
                           packet=packet, report=report, previous=f"`{previous}`" if previous else "无（这是第一期）",
                           workspace=Path(mem["workspace"]).expanduser(), project=root, run_dir=run_dir)
    started, t0 = now_iso(), time.time()
    res = launch(cfg, root, run_dir, harness, prompt)
    if not report.exists() or report.stat().st_mtime < t0:
        text = final_text(res["output"])
        if text:
            report.write_text(text.rstrip() + "\n", encoding="utf-8")
    if report.exists():
        latest = report.parent / "latest.md"
        latest.unlink(missing_ok=True)
        latest.symlink_to(report.name)

    problems = verify(mem, since=started, staged_only=True)
    status = res["status"] if res["status"] != "ok" else (
        "no report" if not report.exists() else "guardrail problems" if problems else "ok")
    return save_meta(run_dir, {
        "run_id": run_id, "day": issue, "harness": harness, "started": started, "finished": now_iso(),
        "exit_code": res["exit_code"], "status": status, "session_id": res["session_id"], "resume": res["resume"],
        "run_dir": str(run_dir), "report": str(report) if report.exists() else None, "problems": problems,
        "warnings": others_since(Path(mem["workspace"]).expanduser(), started), "cmd": res["cmd"]})


# ---------------------------------------------------------------- memory review


def pending_invest(mem: dict) -> list[dict]:
    """Staged changes under @overlay/invest/ (other staged changes, e.g. always-loaded cards, are never touched)."""
    return [i for i in json.loads(mindslice(mem, "diff").stdout)["items"] if i["address"].startswith("@overlay/invest/")]


def load_decisions(path: Path, output: str) -> list[dict]:
    """The reviewer's JSON array from its decisions file, else from its final answer."""
    candidates = [path.read_text(encoding="utf-8")] if path.exists() else []
    candidates += re.findall(r"\[.*\]", final_text(output) or "", re.S)
    for text in candidates:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return [d for d in value if isinstance(d, dict)]
    return []


def run_review(cfg: dict, root: Path, issue: str, harness: str, packet: Path, report: Path) -> dict:
    """An independent session judges each staged invest/* change; this code then applies or discards it.

    The reviewer only writes a decisions file. Its decisions are executed only for pending invest/* addresses and only
    if nothing under invest/ changed while it ran (writes elsewhere are other sessions' and only warned about);
    changes without a decision stay pending."""
    mem = cfg["memory"]
    pending = pending_invest(mem)
    if not pending:
        return {"run_id": None, "status": "nothing to review", "problems": [], "decisions": []}
    run_id, run_dir = new_run_dir(cfg, root, f"{issue}-review-{harness}")
    run_dir.mkdir(parents=True, exist_ok=True)
    changes, decisions_file = run_dir / "pending.json", run_dir / "decisions.json"
    changes.write_text(json.dumps([{"address": i["address"], "change": i["change"],
                                    "reasons": [c.get("reason") for c in i.get("changes", [])], "diff": i["diff"]}
                                   for i in pending], ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = render_prompt((root / cfg["paths"]["review_prompt"]).read_text(encoding="utf-8"), changes=changes,
                           decisions=decisions_file, report=report, packet=packet, project=root,
                           workspace=Path(mem["workspace"]).expanduser())
    started = now_iso()
    res = launch(cfg, root, run_dir, harness, prompt)
    ws = Path(mem["workspace"]).expanduser()
    problems = [f"审阅期间 invest/ 下有改动，未执行任何决定: {e['event']} {e['address']}"
                for e in history_since(ws, started) if is_invest(e)]
    warnings = others_since(ws, started)
    tampered = bool(problems)
    decisions = {d.get("address"): d for d in load_decisions(decisions_file, res["output"])}
    outcome = []
    for item in pending:
        d = decisions.pop(item["address"], {})
        action = None if tampered else d.get("decision")
        if action in ("apply", "discard"):
            r = mindslice(mem, action, item["address"])
            if r.returncode != 0:
                problems.append(f"{action} 失败: {item['address']}: {r.stderr.strip() or r.stdout.strip()}")
                action = "failed"
        else:
            action = "pending"
            if not tampered:
                problems.append(f"审阅未给出决定，保留待审: {item['address']}")
        outcome.append({"address": item["address"], "decision": action, "reason": d.get("reason", "")})
    problems += [f"审阅结果含非待审地址，已忽略: {a}" for a in decisions]
    problems += verify(mem)
    (run_dir / "review.md").write_text("| 卡片 | 决定 | 理由 |\n| :--- | :--- | :--- |\n" + "".join(
        f"| {o['address']} | {o['decision']} | {o['reason']} |\n" for o in outcome), encoding="utf-8")
    status = res["status"] if res["status"] != "ok" else "guardrail problems" if problems else "ok"
    return save_meta(run_dir, {
        "run_id": run_id, "day": issue, "harness": harness, "started": started, "finished": now_iso(),
        "exit_code": res["exit_code"], "status": status, "session_id": res["session_id"], "resume": res["resume"],
        "run_dir": str(run_dir), "report": str(run_dir / "review.md"), "problems": problems, "warnings": warnings,
        "decisions": outcome, "cmd": res["cmd"]})
