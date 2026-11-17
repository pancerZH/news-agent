"""Email delivery of the daily report: drop the memory-change section, render Markdown to HTML, send via SMTP."""

import re
import smtplib
import tomllib
from email.message import EmailMessage
from pathlib import Path

from markdown_it import MarkdownIt

CSS = """
body { margin: 0; padding: 16px; background: #f6f7f9; }
.page { max-width: 780px; margin: 0 auto; padding: 20px 24px; background: #fff; border-radius: 8px;
        font: 15px/1.7 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; color: #1f2328; }
h1 { font-size: 22px; border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }
h2 { font-size: 18px; margin-top: 28px; border-left: 4px solid #2563eb; padding-left: 10px; }
a { color: #2563eb; text-decoration: none; }
blockquote { margin: 12px 0; padding: 8px 14px; background: #f3f4f6; border-left: 4px solid #9ca3af; color: #4b5563; }
table { border-collapse: collapse; margin: 12px 0; font-size: 13px; }
th, td { border: 1px solid #e5e7eb; padding: 4px 8px; }
th { background: #f9fafb; }
code { background: #f3f4f6; padding: 1px 4px; border-radius: 4px; font-size: 13px; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }
"""


CLUSTER = r"`?(?<![A-Za-z0-9])C\d+(?![A-Za-z0-9])`?"  # C158, `C158`; not SC26 or C2C
SEP = r"\s*[,，、/;；]\s*"


def scrub_clusters(text: str) -> str:
    """Drop cluster ids (kept in the report file for fact checking) and tidy the brackets they leave."""
    text = re.sub(rf"\s*[（(]\s*{CLUSTER}(?:{SEP}{CLUSTER})*\s*[）)]", "", text)  # "（C158, C161）"
    text = re.sub(rf"{CLUSTER}(?:{SEP}{CLUSTER})*(?:{SEP}|\s+)?", "", text)  # "（`C171`，HN 87 分）"
    text = re.sub(r"（\s*[,，、;；]?\s*", "（", text)
    text = re.sub(r"\s*[,，、;；]?\s*）", "）", text)
    return re.sub(r"\s*（）", "", text)


def public_text(report_md: str) -> str:
    """The report for readers: everything from the "## ③" memory-change section on is dropped, and internal
    MindSlice card paths (`invest/...`) and cluster ids (`C158`) are scrubbed from what is left."""
    m = re.search(r"^#{1,6}\s*③.*$", report_md, re.M)
    text = report_md[: m.start()] if m else report_md
    text = re.sub(r"\s*[（(]\s*`?invest/[\w/.-]+`?\s*[）)]", "", text)  # "标题（`invest/theses/x`）"
    text = re.sub(r"`invest/[\w/.-]+`", "", text)
    text = scrub_clusters(text)
    return re.sub(r"(\s*\n-{3,})+\s*$", "", text.rstrip()) + "\n"


def render_html(markdown_text: str, title: str) -> str:
    body = MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(markdown_text)
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title><style>{CSS}</style></head>'
            f'<body><div class="page">{body}</div></body></html>')


def build_message(report: Path, title: str, sender: str, to: list[str], status: str = "ok") -> EmailMessage:
    text = public_text(report.read_text(encoding="utf-8"))
    subject = title + ("" if status == "ok" else f" [{status}]")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(render_html(text, subject), subtype="html")
    msg.add_attachment(text.encode("utf-8"), maintype="text", subtype="markdown", filename=report.name)
    return msg


def send(report: Path, title: str, smtp_file: str | Path, status: str = "ok") -> list[str]:
    """Send the report with the settings in smtp_file (host, port, username, password, to[, from])."""
    conf = tomllib.loads(Path(smtp_file).expanduser().read_text(encoding="utf-8"))
    to = conf["to"] if isinstance(conf["to"], list) else [conf["to"]]
    msg = build_message(report, title, conf.get("from", conf["username"]), to, status)
    port = conf.get("port", 465)
    with (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(conf["host"], port, timeout=30) as smtp:
        if port != 465:
            smtp.starttls()
        smtp.login(conf["username"], conf["password"])
        smtp.send_message(msg)
    return to
