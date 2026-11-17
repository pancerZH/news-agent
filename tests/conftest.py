import shutil
import subprocess
import sys
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def rss(items: list[tuple[str, str, str]]) -> str:
    """items: (title, link, description); all published one hour ago."""
    when = format_datetime(datetime.now(timezone.utc) - timedelta(hours=1))
    body = "".join(f"<item><title>{t}</title><link>{link}</link><description>{d}</description>"
                   f"<pubDate>{when}</pubDate></item>" for t, link, d in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'


def fake_encode(texts, cfg):
    """Deterministic bag-of-words embedding: no model, no GPU."""
    out = np.zeros((len(texts), 256), dtype=np.float32)
    for i, text in enumerate(texts):
        for word in text.lower().split():
            out[i, zlib.crc32(word.encode()) % 256] += 1
    return out / np.linalg.norm(out, axis=1, keepdims=True)


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "mindslice"
    subprocess.run([shutil.which("mindslice"), "init", str(ws)], check=True, capture_output=True)
    return ws


@pytest.fixture
def project(tmp_path, workspace) -> Path:
    """A throwaway project: real config with tmp memory, local file:// feeds, HN/market off, fake harness."""
    proj = tmp_path / "proj"
    proj.mkdir()
    shutil.copy(ROOT / "memory_seeds.toml", proj)
    shutil.copytree(ROOT / "prompts", proj / "prompts")
    feeds = proj / "feeds"
    feeds.mkdir()
    (feeds / "a.xml").write_text(rss([
        ("NVIDIA launches Rubin GPU platform", "https://example.com/rubin?utm_source=x", "NVIDIA Rubin GPU launch"),
        ("Fed holds rates steady", "https://example.com/fed", "The FOMC kept rates unchanged"),
    ]))
    (feeds / "b.xml").write_text(rss([
        ("NVIDIA Rubin is here", "https://www.example.com/rubin/", "Rubin GPU racks ship"),
    ]))
    (proj / "feeds.toml").write_text(
        f'[[feeds]]\nid = "feed_a"\nname = "A"\ntier = 1\nurl = "{(feeds / "a.xml").as_uri()}"\n\n'
        f'[[feeds]]\nid = "feed_b"\nname = "B"\ntier = 2\nurl = "{(feeds / "b.xml").as_uri()}"\n')
    config = (ROOT / "config.toml").read_text()
    config = config.replace("enabled = true", "enabled = false").replace("[mail]\nenabled = false", "[mail]\nenabled = true")
    config = config.replace('workspace = "~/mindslice"', f'workspace = "{workspace}"')
    config = config.replace('default = "agy"', 'default = "fake"')
    # never the real ~/.config/news-agent/smtp.toml: tests that want mail write this file themselves
    config = config.replace('smtp = "~/.config/news-agent/smtp.toml"', f'smtp = "{proj / "smtp.toml"}"')
    config += (f'\n[harness.fake]\ncmd = ["{sys.executable}", "{ROOT / "tests" / "fake_harness.py"}", "{{prompt}}"]\n'
               'resume = "fake --resume {session_id}"\n')
    (proj / "config.toml").write_text(config)
    return proj
