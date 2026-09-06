#!/usr/bin/env python3
"""
fix_agent_models.py — stop sub-agents from silently downgrading your model.

Same fault as the routing table's old model column, in a different file. An
agent whose frontmatter says `model: sonnet` runs at Sonnet no matter what the
session is, so on an Opus or Fable session every dispatch to it buys sub-agent
overhead at a *lower* capability than the model the user chose. Nothing warns
about it; the work just comes back slightly worse.

Measured on this machine, Claude Code 2.1.263, parent session Opus 5:

    model: sonnet   dispatch used claude-opus-5 + claude-sonnet-5   (downgraded)
    model: inherit  dispatch used claude-opus-5 only                (correct)
    no model line   dispatch used claude-opus-5 only                (correct)

`inherit` is preferred over deleting the line: both follow the parent today,
but `inherit` states the intent, so the next reader can tell inheritance was
chosen rather than forgotten.

`haiku` is left alone. It is the one downgrade that is deliberate — bulk
read-only work whose output is a list of file paths, not a judgment — and the
agents carrying it say so in their own descriptions.

Plugin agents under ~/.claude/plugins/cache are never touched: they are
replaced wholesale on the next plugin update, so an edit there is lost work.

Usage:
    python3 scripts/fix_agent_models.py --dry-run   # show what would change
    python3 scripts/fix_agent_models.py             # apply (archives first)
    python3 scripts/fix_agent_models.py --check     # exit 1 if any pin remains
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

AGENTS_DIR = Path.home() / ".claude" / "agents"
ARCHIVE_DIR = Path.home() / ".claude" / ".archive"

# Deliberate cost choices, not accidents. Leave them pinned.
KEEP = {"haiku"}

# Everything that names a specific frontier model. Any of these silently wins
# over the session's own model.
DOWNGRADING = {"sonnet", "opus", "fable"}

MODEL_LINE = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE)


def frontmatter_bounds(text: str) -> tuple[int, int] | None:
    """Byte offsets of the frontmatter block, or None if there isn't one.

    Only the frontmatter is rewritten. A `model:` line in the agent's prose —
    an instruction telling the agent which model to call, say — must not be
    touched.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    return (0, end) if end != -1 else None


def current_model(text: str) -> str | None:
    bounds = frontmatter_bounds(text)
    if bounds is None:
        return None
    match = MODEL_LINE.search(text, *bounds)
    return match.group(1) if match else None


def retarget(text: str) -> str:
    bounds = frontmatter_bounds(text)
    if bounds is None:
        return text
    start, end = bounds
    head, tail = text[start:end], text[end:]
    return MODEL_LINE.sub("model: inherit", head, count=1) + tail


def main() -> int:
    ap = argparse.ArgumentParser(description="Point sub-agents at the session model.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if any downgrading pin remains")
    args = ap.parse_args()

    if not AGENTS_DIR.is_dir():
        print(f"no agents directory at {AGENTS_DIR}", file=sys.stderr)
        return 1

    to_change: list[tuple[Path, str]] = []
    kept: list[tuple[str, str]] = []
    already: list[str] = []

    for path in sorted(AGENTS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        model = current_model(text)
        if model is None or model == "inherit":
            already.append(path.stem)
        elif model in KEEP:
            kept.append((path.stem, model))
        elif model in DOWNGRADING or model.startswith("claude-"):
            to_change.append((path, model))

    for name, model in kept:
        print(f"  keep    {name:26} {model}  (deliberate)")
    for path, model in to_change:
        print(f"  change  {path.stem:26} {model} -> inherit")
    print(f"\n  {len(to_change)} to change · {len(kept)} deliberate · "
          f"{len(already)} already following the session")

    if args.check:
        if to_change:
            print("\n  Pinned agents downgrade every dispatch on a stronger session.",
                  file=sys.stderr)
            return 1
        return 0

    if args.dry_run or not to_change:
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = ARCHIVE_DIR / f"agents-pre-inherit-{stamp}"
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copytree(AGENTS_DIR, backup)
        print(f"\n  archived originals to {backup}")

    for path, _ in to_change:
        text = path.read_text(encoding="utf-8")
        updated = retarget(text)
        if current_model(updated) != "inherit":
            print(f"  SKIPPED {path.stem}: rewrite did not take", file=sys.stderr)
            continue
        path.write_text(updated)

    print(f"  updated {len(to_change)} agents")
    print("  Restart Claude Code — agent definitions are cached at session start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
