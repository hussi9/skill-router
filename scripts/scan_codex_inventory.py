#!/usr/bin/env python3
"""Repo-level wrapper for the installable Codex skill script."""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> int:
    target = (
        Path(__file__).resolve().parents[1]
        / "codex-skill"
        / "skill-router"
        / "scripts"
        / "scan_codex_inventory.py"
    )
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
