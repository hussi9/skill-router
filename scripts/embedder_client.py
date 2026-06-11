#!/usr/bin/env python3
"""
embedder_client.py — fail-open Unix-socket client for the embedder daemon.

Used by router.py on the SKIP path to ask the local daemon "does this look
like an engineering prompt?" If the daemon responds with a confident match,
router.py uses that route. If the daemon is missing, slow, or errors out,
this client returns None and router.py falls back to today's silent SKIP.

NEVER raises. NEVER blocks longer than the timeout. NEVER reaches the
network. The whole point of this layer is to extend recall WITHOUT
introducing new failure modes — a broken daemon must look identical to
the user as today's silent-SKIP behavior.

Why fail-open and not fail-closed:
  The router is in the hot path of every user prompt. If a regression
  here blocks tools, the user's session breaks. Treating any anomaly as
  "no fallback available" is the safe default — the regex layer above
  is unaffected.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Optional


SOCKET_PATH = Path.home() / ".claude" / "skill_router_embedder.sock"
DEFAULT_TIMEOUT = float(os.environ.get("SKILL_ROUTER_EMB_TIMEOUT", "2.0"))  # seconds
# Bumped from 0.8 → 2.0 because the first call after the daemon has been idle
# for a while pays ~800ms socket round-trip overhead (the model itself is
# warm — the latency is OS-level socket setup, not inference). Sub-second
# timeout was timing out cold paths and silently falling back to SKIP.
# Subsequent calls in the same session are 36-130ms, well under the bump.


def classify(prompt: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    """Ask the daemon to classify a prompt. Returns the daemon's JSON response,
    or None if the daemon is unavailable or anything goes wrong.

    Caller MUST treat None as "stay silent — no fallback available."
    """
    if not prompt:
        return None
    if not SOCKET_PATH.is_socket():
        return None
    deadline = time.perf_counter() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(SOCKET_PATH))
            payload = json.dumps({"op": "classify", "prompt": prompt}) + "\n"
            sock.sendall(payload.encode("utf-8"))
            buf = b""
            while time.perf_counter() < deadline:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                sock.settimeout(remaining)
                try:
                    chunk = sock.recv(4096)
                except (socket.timeout, TimeoutError):
                    return None
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            if not buf:
                return None
            line = buf.decode("utf-8", errors="replace").splitlines()[0]
            return json.loads(line)
    except (FileNotFoundError, ConnectionRefusedError, BrokenPipeError,
            OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def ping(timeout: float = 0.5) -> Optional[dict]:
    """Health-check. Returns daemon info dict, or None if unreachable."""
    if not SOCKET_PATH.is_socket():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(SOCKET_PATH))
            sock.sendall(b'{"op":"ping"}\n')
            buf = sock.recv(4096)
            if not buf:
                return None
            return json.loads(buf.decode("utf-8").splitlines()[0])
    except (FileNotFoundError, ConnectionRefusedError, BrokenPipeError,
            OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


if __name__ == "__main__":
    # Quick CLI for debugging: echo "fix the checkout flow" | embedder_client.py
    import sys
    text = sys.stdin.read().strip()
    if not text:
        print(json.dumps({"error": "no_input"}))
        sys.exit(1)
    result = classify(text)
    print(json.dumps(result or {"path": "SKIP", "reason": "client_failed"}, indent=2))
