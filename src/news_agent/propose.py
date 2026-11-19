"""`news-agent propose`: the harness's only way to change invest/* memory. Every change is checked here, then staged
for the memory review: staging is enforced by this code, not asked for in the prompt."""

import json
import re
from pathlib import Path

from .runner import invest_slice_dir, mindslice

ID = re.compile(r"invest/watchlist|invest/(macro|theses|entities)/[a-z0-9][a-z0-9-]*")
OPS = ("create", "replace", "patch", "append")
LINK = re.compile(r"\[\[([^\]|]+)")
LISTS = {"tags": "--tag", "aliases": "--alias", "extends": "--extends", "supersedes": "--supersedes"}


def _texts(w: dict) -> list[str]:
    content = w.get("content") or ""
    texts = content if isinstance(content, list) else [content]
    return [*texts, w.get("summary") or "", *(e.get("new", "") for e in w.get("edits") or [])]


def check_links(w: dict, ws: Path, baked: Path | None, batch: set[str]) -> list[str]:
    """Links between invest cards must resolve: cards already in @invest are linked `[[@invest/invest/...]]` (an
    unqualified link only searches @overlay, and a staged change to such a card may be discarded)."""
    problems = []
    for target in (m.strip() for t in _texts(w) for m in LINK.findall(t)):
        if target.startswith("@invest/"):
            path = target.removeprefix("@invest/")
            if not (baked and (baked / f"{path}.md").exists()):
                problems.append(f"{w['id']}: 链接 [[{target}]] 指向不存在的卡片")
        elif target.startswith("invest/"):
            if baked and (baked / f"{target}.md").exists():
                problems.append(f"{w['id']}: 链接 [[{target}]] 要写成 [[@invest/{target}]]")
            elif not (target in batch or any((ws / d / f"{target}.md").exists() for d in ("overlay", "staging"))):
                problems.append(f"{w['id']}: 链接 [[{target}]] 指向不存在的卡片")
    return problems


def check(w: dict, ws: Path, baked: Path | None, batch: set[str]) -> list[str]:
    if not isinstance(w, dict) or not isinstance(w.get("id"), str):
        return [f"每条变更必须是带 id 的 JSON 对象: {w!r}"[:200]]
    card, op = w["id"], w.get("op", "create")
    problems = []
    if not ID.fullmatch(card):
        problems.append(f"{card}: id 只能是 invest/watchlist、invest/macro|theses|entities/<小写 slot>")
    if op not in OPS:
        problems.append(f"{card}: op 只能是 {' / '.join(OPS)}")
    if op == "append" and card.startswith("invest/theses/"):
        problems.append(f"{card}: 主线卡不能 append（会加到 Kill Criteria 之后），改用 replace")
    if op == "patch" and not w.get("edits"):
        problems.append(f"{card}: patch 需要 edits")
    if not w.get("reason"):
        problems.append(f"{card}: 缺少 reason")
    if w.get("load", "as-needed") != "as-needed":
        problems.append(f"{card}: invest 卡片不能设置 load=\"{w['load']}\"")
    unknown = set(w) - {"id", "op", "title", "summary", "content", "edits", "reason", "load", *LISTS}
    if unknown:
        problems.append(f"{card}: 不认识的字段 {sorted(unknown)}")
    return problems + check_links(w, ws, baked, batch)


def write_args(w: dict) -> list[str]:
    op = w.get("op", "create")
    args = ["write", w["id"], "--op", op, "--stage", "--reason", w["reason"]]
    for key in ("title", "summary"):
        if w.get(key):
            args += [f"--{key}", w[key]]
    if w.get("content"):
        content = w["content"]
        args += ["--content", "\n".join(content) if isinstance(content, list) else content]
    tags = list(w.get("tags") or [])
    if (op == "create" or "tags" in w) and "invest" not in tags:
        tags.insert(0, "invest")
    for key, flag in LISTS.items():
        for value in tags if key == "tags" else w.get(key) or []:
            args += [flag, value]
    for e in w.get("edits") or []:
        args += ["--edit", e["old"], e["new"]]
    return args


def propose(mem: dict, writes: list[dict]) -> tuple[list[str], list[str]]:
    """(staged lines, problems). Nothing is written unless every change passes the checks."""
    ws = Path(mem["workspace"]).expanduser()
    baked = invest_slice_dir(ws)
    batch = {w["id"] for w in writes if isinstance(w, dict) and isinstance(w.get("id"), str)}
    problems = [p for w in writes for p in check(w, ws, baked, batch)]
    if problems:
        return [], problems
    staged = []
    for w in writes:
        res = mindslice(mem, *write_args(w))
        try:
            out = json.loads(res.stdout)
        except json.JSONDecodeError:
            out = {}
        if res.returncode != 0 or not out.get("staged"):
            problems.append(f"{w['id']}: 写入失败: {res.stderr.strip() or res.stdout.strip()}")
            continue
        staged.append(f"staged: {out['address']} ({w.get('op', 'create')})"
                      + "".join(f"\n  warning: {x.get('message', x) if isinstance(x, dict) else x}"
                                for x in out.get("warnings") or []))
    return staged, problems


def load(source: str, stdin) -> list[dict]:
    value = json.loads(stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8"))
    return value if isinstance(value, list) else [value]
