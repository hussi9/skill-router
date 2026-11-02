"""Static landing-page artifact checks. Never execute or render generated content."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SECTIONS = ("Brief", "Headlines", "Page plan", "The file", "Connect your form", "Publish it", "Checked")


class _Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.ends = []
        self.declarations = []
        self.scripts = []
        self.title = ""
        self.in_title = False
        self.script = None

    def handle_decl(self, decl):
        self.declarations.append(decl.lower())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "title":
            self.in_title = True
        if tag == "script":
            self.script = {"attrs": attrs, "body": "", "closed": False}
            self.scripts.append(self.script)

    def handle_endtag(self, tag):
        self.ends.append(tag)
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.script is not None:
            self.script["closed"] = True
            self.script = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.script is not None:
            self.script["body"] += data


def _row(check_id, passed, evidence):
    return {"id": check_id, "status": "passed" if passed else "failed", "evidence": evidence}


def _section_checks(prose):
    """Recognize standalone output headings, never matches words inside HTML/code."""
    headings = []
    for line in prose.splitlines():
        plain = re.sub(r"^\s*(?:#{1,6}\s*|\d+[.)]\s*)", "", line).strip()
        plain = plain.replace("**", "").rstrip(":").strip()
        for name in SECTIONS:
            if re.fullmatch(re.escape(name) + r"(?:\s*[:—–-].*)?", plain, re.I):
                headings.append(name)
                break
    positions = [headings.index(name) if name in headings else -1 for name in SECTIONS]
    return [_row("output-sections-present", all(p >= 0 for p in positions), {"recognized": headings, "required": list(SECTIONS)}),
            _row("output-sections-order", all(p >= 0 for p in positions) and positions == sorted(positions), positions)]


def check_landing_page(output, *, node_check=False):
    """Check fenced HTML plus prose mechanically; success still needs semantic review.

    Optional node --check parses JS in a temporary file; no supplied JS is executed.
    No browser, network, package installation or generated file persistence occurs.
    """
    blocks = list(re.finditer(r"(?m)^\s*```html\s*\n([\s\S]*?)^\s*```\s*$", output, re.I))
    code_free = re.sub(r"(?m)^\s*```[^\n]*\n[\s\S]*?^\s*```\s*$", "", output)
    checks = _section_checks(code_free)
    semantic = [{"id": "outcome-semantics", "status": "pending", "evidence": "Review all recipe checklist items against supplied offer, proof and corrections"},
                {"id": "rendered-ui", "status": "not-run", "evidence": "No browser rendering, accessibility, contrast, responsive layout or form delivery test performed"}]
    if not blocks:
        checks.append({"id": "html-artifact", "status": "not-run", "evidence": "No complete HTML fenced artifact found"})
        return {"status": "not-run", "verified": False, "deterministic": checks, "semantic": semantic}
    checks.append(_row("one-html-artifact", len(blocks) == 1, f"{len(blocks)} complete HTML fences"))
    html = blocks[0].group(1)
    doc = _Document()
    doc.feed(html)
    doc.close()
    tags = [tag for tag, _ in doc.tags]
    metas = [attrs for tag, attrs in doc.tags if tag == "meta"]
    html_attrs = next((attrs for tag, attrs in doc.tags if tag == "html"), {})
    checks.extend([
        _row("html-doctype", bool(re.match(r"\s*<!doctype\s+html\s*>", html, re.I)), "HTML5 doctype must begin artifact"),
        _row("html-complete", bool(re.search(r"</html\s*>\s*$", html, re.I)) and all(tag in tags and tag in doc.ends for tag in ("html", "head", "body")), "Explicit html/head/body opening and closing tags"),
        _row("html-lang", bool(str(html_attrs.get("lang", "")).strip()), "Nonempty html lang attribute"),
        _row("html-title", "title" in doc.ends and 0 < len(doc.title.strip()) < 60, f"Title length {len(doc.title.strip())}"),
        _row("html-viewport", any(a.get("name", "").lower() == "viewport" and "width=device-width" in a.get("content", "").replace(" ", "").lower() for a in metas), "Viewport includes width=device-width"),
        _row("scripts-closed", all(script["closed"] for script in doc.scripts), f"{len(doc.scripts)} script blocks"),
    ])
    node = shutil.which("node") if node_check else None
    for index, script in enumerate(doc.scripts):
        attrs = script["attrs"]
        kind = attrs.get("type", "").lower()
        if "src" in attrs:
            checks.append(_row(f"script-{index}-self-contained", False, "External script source is forbidden by recipe"))
            continue
        if kind not in ("", "module", "text/javascript", "application/javascript"):
            checks.append({"id": f"script-{index}-syntax", "status": "not-run", "evidence": f"Non-JavaScript script type: {kind}"})
            continue
        if not node:
            checks.append({"id": f"script-{index}-syntax", "status": "not-run", "evidence": "Optional Node syntax check disabled or node unavailable"})
            continue
        with tempfile.TemporaryDirectory(prefix="lazycoder-static-") as temp:
            path = Path(temp) / ("artifact.mjs" if kind == "module" else "artifact.js")
            path.write_text(script["body"], encoding="utf-8")
            try:
                # --check parses only. The artifact is never imported or executed.
                result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=10)
                checks.append(_row(f"script-{index}-syntax", result.returncode == 0, "Node syntax parse passed" if result.returncode == 0 else "Node syntax parse failed"))
            except (OSError, subprocess.TimeoutExpired):
                checks.append({"id": f"script-{index}-syntax", "status": "not-run", "evidence": "Node syntax check unavailable or timed out"})
    return {"status": "failed" if any(c["status"] == "failed" for c in checks) else "needs-review", "verified": False,
            "deterministic": checks, "semantic": semantic}
