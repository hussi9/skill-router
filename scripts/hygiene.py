#!/usr/bin/env python3
"""hygiene.py — block personal identifiers and real prompts from reaching commits.

Scans tracked text files for (a) regexes from the git-ignored `.hygiene-patterns`
and (b) any 8-word window copied from a real prompt in `docs/jev-eval-*/cases.json`.
Reports `file:line rule` only; matched text is never printed.

    python3 scripts/hygiene.py [--root DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

WINDOW = 8
PATTERN_FILE = ".hygiene-patterns"
SKIP_NAMES = {".gitignore", PATTERN_FILE}
_EDGE = re.compile(r"^\W+|\W+$")


_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                       "\u2013": " ", "\u2014": " "})
MIN_SHORT = 4


def tokens(text: str) -> list[str]:
    out = []
    for raw in text.translate(_FOLD).lower().split():
        t = _EDGE.sub("", raw)
        if t:
            out.append(t)
    return out


def _h(words: list[str]) -> str:
    return hashlib.sha256(" ".join(words).encode()).hexdigest()


def load_patterns(path: Path) -> list[re.Pattern]:
    pats = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pats.append(re.compile(line, re.IGNORECASE))
    return pats


def load_prompts(case_files: list[Path]) -> list[str]:
    """Prompts from case files shaped as a list or {"cases": [...]}."""
    prompts = []
    for cf in case_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("cases", [])
        for case in data if isinstance(data, list) else []:
            prompt = case.get("prompt") if isinstance(case, dict) else None
            if isinstance(prompt, str) and tokens(prompt):
                prompts.append(prompt)
    return prompts


def prompt_hashes(case_files: list[Path]) -> set[str]:
    """8-word windows (plain hex) plus whole 4-7 word prompts (`sN:` prefix)."""
    hashes: set[str] = set()
    for prompt in load_prompts(case_files):
        toks = tokens(prompt)
        if len(toks) >= WINDOW:
            for i in range(len(toks) - WINDOW + 1):
                hashes.add(_h(toks[i:i + WINDOW]))
        elif len(toks) >= MIN_SHORT:
            hashes.add(f"s{len(toks)}:{_h(toks)}")
    return hashes


def scan(paths, patterns, hashes):
    """Return [(file, line, rule_id)] — never the matched text."""
    hits = []
    short_lens = sorted({int(h[1:h.index(":")]) for h in hashes if h.startswith("s")})
    for path in paths:
        try:
            data = Path(path).read_bytes()
            if b"\0" in data:
                continue
            lines = data.decode("utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        found = set()
        for n, line in enumerate(lines, 1):
            for idx, pat in enumerate(patterns, 1):
                if pat.search(line):
                    found.add((n, f"pattern:{idx}"))
        if hashes:
            stream = []  # (token, line number) across the whole file
            for n, line in enumerate(lines, 1):
                stream.extend((t, n) for t in tokens(line))
            words = [t for t, _ in stream]
            for i in range(len(stream) - WINDOW + 1):
                if _h(words[i:i + WINDOW]) in hashes:
                    found.add((stream[i][1], "prompt-window"))
            for ln in short_lens:
                for i in range(len(stream) - ln + 1):
                    if f"s{ln}:{_h(words[i:i + ln])}" in hashes:
                        found.add((stream[i][1], "prompt-short"))
        hits.extend((str(path), n, rule) for n, rule in sorted(found))
    return hits


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root, capture_output=True, check=True).stdout
    names = [n for n in out.decode().split("\0") if n]
    return [root / n for n in names
            if Path(n).name not in SKIP_NAMES and not re.fullmatch(r"docs/jev-eval-[^/]*/cases\.json", n)
            and (root / n).is_file()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    root = Path(ap.parse_args(argv).root)
    pf = root / PATTERN_FILE
    case_files = sorted(root.glob("docs/jev-eval-*/cases.json"))
    if not pf.exists() and not case_files:
        print("hygiene: no pattern file, skipped")
        return 0
    patterns = load_patterns(pf) if pf.exists() else []
    n_prompts = len(load_prompts(case_files))
    if case_files and n_prompts == 0:
        print("hygiene: case files exist but 0 prompts loaded (unknown schema?)")
        return 1
    hashes = prompt_hashes(case_files)
    try:
        files = tracked_files(root)
    except (subprocess.CalledProcessError, OSError):
        print("hygiene: root is not a git repository")
        return 2
    hits = scan(files, patterns, hashes)
    for f, n, rule in hits:
        print(f"{Path(f).relative_to(root)}:{n} {rule}")
    if hits:
        print(f"hygiene: {len(hits)} hit(s)")
        return 1
    print(f"hygiene: clean ({len(patterns)} patterns, {n_prompts} prompts from {len(case_files)} case files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
