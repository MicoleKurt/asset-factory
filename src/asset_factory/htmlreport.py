"""Renders the generated Markdown reports as standalone HTML.

Same content, presented so a reviewer can open it in a browser instead of
reading raw Markdown. Nothing is restated or re-derived here: the HTML is a
transform of the Markdown that the report emitters already produced, so the
two cannot disagree.

Deliberately dependency-free and offline: no CDN, no webfont, no script. The
files are opened from disk by someone who may have no network.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

CSS = """
:root{--ink:#14151a;--muted:#6b6f76;--faint:#9a9da3;--rule:#e5e3de;
--paper:#fbfaf8;--panel:#f4f2ed;--ok:#1f6f4a;--bad:#a33028;--warn:#a5761b;
--info:#2c5a82}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
padding:48px 24px 96px}
main{max-width:900px;margin:0 auto}
h1{font-size:30px;font-weight:500;letter-spacing:-.01em;margin:0 0 4px}
h2{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.09em;
color:var(--faint);margin:44px 0 12px;padding-bottom:8px;
border-bottom:1px solid var(--rule)}
h3{font-size:15px;font-weight:600;margin:26px 0 8px;font-family:ui-monospace,
SFMono-Regular,Menlo,Consolas,monospace}
p{margin:10px 0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:13px;background:var(--panel);padding:2px 5px;border-radius:3px}
ul{margin:10px 0;padding-left:20px}
li{margin:5px 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
th{text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;
letter-spacing:.07em;color:var(--faint);padding:8px 10px;
border-bottom:1px solid var(--rule)}
td{padding:9px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:none}
blockquote{margin:14px 0;padding:10px 16px;background:var(--panel);
border-left:2px solid var(--faint);color:var(--muted)}
strong{font-weight:600}
.tag{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.05em;
text-transform:uppercase;padding:2px 7px;border-radius:3px}
.missing{color:var(--bad);background:rgba(163,48,40,.08)}
.conflict{color:var(--warn);background:rgba(165,118,27,.08)}
.confirm{color:var(--info);background:rgba(44,90,130,.08)}
.approved{color:var(--ok);background:rgba(31,111,74,.08)}
footer{max-width:900px;margin:64px auto 0;padding-top:16px;
border-top:1px solid var(--rule);color:var(--faint);font-size:12px}
@media print{
/* Force the light palette: a dark-themed PDF wastes ink and reads badly
   printed. Also keep table rows and report entries off page breaks. */
:root{--ink:#14151a;--muted:#4a4e55;--faint:#6b6f76;--rule:#d8d5cf;
--paper:#fff;--panel:#f4f2ed}
body{padding:0;font-size:10.5pt;line-height:1.45}
main{max-width:none}
h1{font-size:20pt;margin-bottom:2pt}
h2{font-size:9pt;margin:18pt 0 6pt;break-after:avoid;page-break-after:avoid}
h3{font-size:11pt;margin:12pt 0 4pt;break-after:avoid;page-break-after:avoid}
table{font-size:9pt;break-inside:auto}
tr{break-inside:avoid;page-break-inside:avoid}
thead{display:table-header-group}
ul,blockquote{break-inside:avoid;page-break-inside:avoid}
.tag{border:0.5pt solid currentColor}
a{color:inherit;text-decoration:none}
footer{margin-top:24pt;font-size:8pt}
}
@media(prefers-color-scheme:dark){
:root{--ink:#edece8;--muted:#8e9198;--faint:#7c7f86;--rule:#26282d;
--paper:#101114;--panel:#17181c;--ok:#5cbf90;--bad:#e0776d;--warn:#d6a54a;
--info:#7fb0d8}}
"""

LIST_ITEM = re.compile(r"^\s*[-*]\s+")

_STATUS = [
    ("Human confirmation required", "confirm"),
    ("Conflicting", "conflict"),
    ("Missing", "missing"),
    ("Approved", "approved"),
]


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    for label, cls in _STATUS:
        text = text.replace(
            f"<strong>{label}</strong>",
            f'<span class="tag {cls}">{label}</span>')
    return text


def markdown_to_html(md: str, title: str) -> str:
    body: list[str] = []
    lines = md.splitlines()
    i = 0
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i].rstrip()

        if line.startswith("|") and i + 1 < len(lines) and set(
                lines[i + 1].replace("|", "").strip()) <= {"-", ":", " "}:
            close_list()
            headers = [c.strip() for c in line.strip("|").split("|")]
            body.append("<table><thead><tr>"
                        + "".join(f"<th>{_inline(h)}</th>" for h in headers)
                        + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                body.append("<tr>" + "".join(
                    f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
            body.append("</tbody></table>")
            continue

        if line.startswith("### "):
            close_list(); body.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list(); body.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list(); body.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("> "):
            close_list(); body.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.startswith("---"):
            close_list()
        elif LIST_ITEM.match(line):
            if not in_list:
                body.append("<ul>"); in_list = True
            item = _inline(LIST_ITEM.sub("", line))
            body.append(f"<li>{item}</li>")
        elif line.strip():
            close_list(); body.append(f"<p>{_inline(line)}</p>")
        else:
            close_list()
        i += 1

    close_list()
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        "<body><main>" + "\n".join(body) + "</main>"
        "<footer>Generated by Asset Factory from the Markdown report of the "
        "same name. Content is identical; only the presentation differs."
        "</footer></body></html>"
    )


def render_all(out_dir: Path) -> list[Path]:
    written: list[Path] = []
    for name, title in (
        ("exception-report", "Exception report"),
        ("dry-run", "Dry run"),
        ("before-after", "Before / after evidence"),
        ("active-time-log", "Active time log"),
        ("qa-checklist", "Candidate QA checklist"),
    ):
        source = out_dir / f"{name}.md"
        if not source.exists():
            continue
        target = out_dir / f"{name}.html"
        target.write_text(
            markdown_to_html(source.read_text(encoding="utf8"), title),
            encoding="utf8")
        written.append(target)
    return written


# --- PDF ------------------------------------------------------------------

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str | None:
    """Locate a Chromium-family browser for headless PDF printing.

    Chrome is used rather than a Python PDF library so the PDF is rendered by
    the same engine as the HTML. A separate renderer would mean the PDF and
    the web version could disagree about the same report, which is exactly
    the kind of quiet divergence this workflow exists to prevent.
    """
    import os
    import shutil

    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("chrome", "chromium", "msedge", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render_pdfs(out_dir: Path, browser: str | None = None) -> list[Path]:
    """Print each generated HTML report to PDF. Returns what was written."""
    import subprocess

    browser = browser or find_browser()
    if not browser:
        return []

    import tempfile

    written: list[Path] = []
    for source in sorted(out_dir.resolve().glob("*.html")):
        # Absolute. Chrome resolves --print-to-pdf against ITS working
        # directory, not the caller's, so a relative path silently writes
        # somewhere else -- or fails with "Failed to write file".
        target = source.with_suffix(".pdf").resolve()
        if target.exists():
            target.unlink()
        # Each print gets its own profile directory. Without it Chrome hands
        # the request to an existing singleton process, which then writes the
        # PDF somewhere else -- the first file succeeds and every subsequent
        # one fails with "Failed to write" while still exiting 0.
        with tempfile.TemporaryDirectory(prefix="af-pdf-") as profile:
            result = subprocess.run(
                [browser, "--headless=new", "--disable-gpu",
                 "--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={profile}",
                 "--no-pdf-header-footer",
                 "--run-all-compositor-stages-before-draw",
                 f"--print-to-pdf={target}", source.resolve().as_uri()],
                capture_output=True, text=True, timeout=180,
            )
        if target.exists() and target.stat().st_size > 1024:
            written.append(target)
        else:
            detail = (result.stderr or result.stdout or "").strip()
            print(f"  ! PDF failed for {source.name}: {detail[:140]}")
    return written
