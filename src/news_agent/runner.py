"""Run the daily analysis in a local agent harness and verify the memory guardrails afterwards."""

import json
import os
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

STRIP_PREFIXES = ("ANTIGRAVITY_", "CLAUDE_CODE_", "CODEX_", "MINDSLICE_PLUGIN_", "PLUGIN_")
STRIP_NAMES = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT"}
KEEP_NAMES = {"CODEX_HOME"}
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


def verify(mem: dict, since: str | None = None, staged_only: bool = False) -> list[str]:
    """Problems with the invest/* memory: always-loaded or untagged cards, invest cards outside the prefix,
    writes outside invest/ (or unstaged ones in a scheduled run) since `since`, validate errors, broken links."""
    ws = Path(mem["workspace"]).expanduser()
    problems = []
    roots = [ws / "overlay", ws / "staging"]
    baked = invest_slice_dir(ws)
    for root in roots + ([baked] if baked else []):
        for md in sorted(root.rglob("*.md")):
            rel = md.relative_to(root).as_posix()
            meta = card_meta(md)
            if rel.startswith("invest/"):
                if meta.get("load") == "always":
                    problems.append(f"load=\"always\" 禁止用于 invest 卡片: {md}")
                if "invest" not in meta.get("tags", []):
                    problems.append(f"invest 卡片缺少 \"invest\" 标签: {md}")
            elif "invest" in meta.get("tags", []):
                problems.append(f"带 invest 标签的卡片越界（不在 invest/ 下）: {md}")

    history = ws / ".history" / "changes.jsonl"
    if since and history.exists():
        start = datetime.fromisoformat(since)
        for line in history.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("origin") != "write" or "address" not in e or datetime.fromisoformat(e["timestamp"]) < start:
                continue
            path = e["address"].split("/", 1)[1]
            if not path.startswith("invest/"):
                problems.append(f"运行期间越界写入: {e['address']} ({e['op']})（若同时有其它会话在写记忆库，可能误报）")
            elif staged_only and e["event"] == "applied":
                problems.append(f"定时运行中未经 staging 的直写: {e['address']} ({e['op']})")

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


def run_harness(cfg: dict, root: Path, issue: str, title: str, harness: str, stage: bool, packet: Path, report: Path,
                previous: Path | None = None) -> dict:
    hcfg = cfg["harness"][harness]
    mem = cfg["memory"]
    run_id = f"{issue}-{harness}-{datetime.now().strftime('%H%M%S%f')[:-3]}"
    run_dir = root / cfg["paths"]["runs"] / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)

    write_rule = ("本次为定时无人值守运行：所有 MindSlice 写入必须暂存待审——MCP `write` 一律传 `stage=true`，"
                  "CLI 一律加 `--stage`。" if stage else
                  "本次为人工触发运行：确认后的变更可直接写入 `@overlay/invest/*`。")
    prompt = render_prompt((root / cfg["paths"]["prompt"]).read_text(encoding="utf-8"), date=issue[:10], title=title,
                           packet=packet, report=report, previous=f"`{previous}`" if previous else "无（这是第一期）",
                           workspace=Path(mem["workspace"]).expanduser(), project=root, write_rule=write_rule)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    cmd = [a.replace("{prompt}", prompt) for a in hcfg["cmd"]]

    started, t0 = now_iso(), time.time()
    exit_code, status = None, "ok"
    with open(run_dir / "stdout.log", "w") as out, open(run_dir / "stderr.log", "w") as err:
        try:
            exit_code = subprocess.run(cmd, cwd=root, env=clean_env(), stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=err, timeout=cfg["harness"]["timeout_minutes"] * 60).returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
        except FileNotFoundError as e:
            status = f"harness not found: {e}"
    output = (run_dir / "stdout.log").read_text(errors="replace")

    session_id = find_session_id(output)
    if not report.exists() or report.stat().st_mtime < t0:
        text = final_text(output)
        if text:
            report.write_text(text.rstrip() + "\n", encoding="utf-8")
    resume = hcfg["resume"].format(session_id=session_id) if session_id else None
    if report.exists():
        latest = report.parent / "latest.md"
        latest.unlink(missing_ok=True)
        latest.symlink_to(report.name)

    problems = verify(mem, since=started, staged_only=stage)
    if status == "ok":
        status = ("harness exit code %s" % exit_code if exit_code != 0 else
                  "no report" if not report.exists() else "guardrail problems" if problems else "ok")
    meta = {"run_id": run_id, "day": issue, "harness": harness, "stage": stage, "started": started,
            "finished": now_iso(), "exit_code": exit_code, "status": status, "session_id": session_id,
            "resume": resume, "run_dir": str(run_dir), "report": str(report) if report.exists() else None,
            "problems": problems, "cmd": [a if "{prompt}" not in a else a.replace("{prompt}", "<prompt.md>")
                                          for a in hcfg["cmd"]]}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta
