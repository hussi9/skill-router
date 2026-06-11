#!/usr/bin/env python3
"""
embedder_daemon.py — local-only Unix socket server for SKIP-fallback routing.

Loads the BAAI/bge-small-en-v1.5 ONNX model ONCE at startup (~30MB, ~300ms)
and stays warm so per-call hook latency stays under 30ms. Speaks a tiny
line-delimited JSON protocol over a Unix socket.

Architecture:
  hook → embedder_client.py → unix socket → this daemon → embedded result
                                                       ← top-k k-NN labels

Why a daemon (vs in-process embedding):
  Each UserPromptSubmit hook spawns a fresh Python process. Loading
  fastembed cold takes ~500ms, which blows the 3s hook budget. Keeping
  the model resident in a long-running daemon makes per-call cost <30ms.

Local-first commitment:
  Zero network calls. Model files live in ~/.cache/fastembed (downloaded
  ONCE on first daemon start). Corpus embeddings stored in-memory and
  to local disk. No data leaves the user's machine.

Protocol (one JSON object per line, both directions):
  request:  {"op": "classify", "prompt": "..."}
  response: {"path": "BROKEN|BUILD|OPERATE", "domains": [...], "skill": "...", "confidence": 0.83, "neighbors": [...]}
            or {"path": "SKIP", "reason": "low_confidence|no_match"}

  request:  {"op": "ping"}
  response: {"ok": true, "corpus_size": N, "model": "..."}

  request:  {"op": "shutdown"}
  response: {"ok": true}

Run:
  ./.venv/bin/python scripts/embedder_daemon.py

Auto-start via launchd:
  setup/launchd-embedder.plist (loaded once per user login).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

import numpy as np


# ---- Configuration ---------------------------------------------------------

SOCKET_PATH = Path.home() / ".claude" / "skill_router_embedder.sock"
LOG_PATH = Path.home() / ".claude" / "skill_router_embedder.log"
MODEL_NAME = "BAAI/bge-small-en-v1.5"  # 384-dim, ~33MB ONNX
TOP_K = 5
CONFIDENCE_THRESHOLD = float(os.environ.get("SKILL_ROUTER_EMB_THRESHOLD", "0.68"))
AGREEMENT_THRESHOLD = int(os.environ.get("SKILL_ROUTER_EMB_AGREEMENT", "3"))
MARGIN_THRESHOLD = float(os.environ.get("SKILL_ROUTER_EMB_MARGIN", "0.01"))

# Path resolution — daemon may be invoked via absolute path or symlink.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))


# ---- Logging ---------------------------------------------------------------

def log(msg: str) -> None:
    """Append a timestamped line to the daemon log. Local file, never network."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ---- Corpus ----------------------------------------------------------------

class CorpusEntry(NamedTuple):
    prompt: str
    path: str           # BROKEN | BUILD | OPERATE
    skill: str          # primary skill (e.g., "superpowers:systematic-debugging")
    embedding: np.ndarray


CATALOG_PATH = Path.home() / ".claude" / "skill_router_catalog.json"

# Keywords used for route classification of catalog entries
_BROKEN_KEYWORDS = {
    "debug", "systematic-debugging", "investigate", "test-runner", "qa", "verify",
    "qa-only", "scrollbook-qa", "tiffinbox-qa", "webapp-testing", "ads-test",
}
_OPERATE_KEYWORDS = {
    "refactor", "clean", "simplify", "review", "requesting-code-review",
    "verification-before-completion", "deploy", "ship", "optimize", "audit",
    "plan-design-review", "seo-audit", "ads-audit", "land-and-deploy",
    "aso-audit", "sentigent-review", "plan-ceo-review",
}
_BROKEN_DESC_PREFIXES = ("Debug", "Fix", "Diagnose")
_BUILD_DESC_PREFIXES = ("Build", "Create", "Generate", "Design", "Integrate")


def _classify_catalog_route(name: str, description: str) -> str:
    """Heuristic path classifier for catalog entries.

    Priority order:
      1. Name contains a BROKEN keyword → BROKEN
      2. Name contains an OPERATE keyword → OPERATE
      3. Description starts with a BROKEN prefix → BROKEN
      4. Description starts with a BUILD prefix → BUILD
      5. Default → BUILD (creating/integrating is the majority case)
    """
    name_lower = name.lower()
    # Check full name and each hyphen-segment
    name_parts = {name_lower} | set(name_lower.split("-"))
    if name_parts & _BROKEN_KEYWORDS:
        return "BROKEN"
    if name_parts & _OPERATE_KEYWORDS:
        return "OPERATE"
    # Soft fallback via description first word
    first_word = description.split()[0] if description else ""
    if first_word in _BROKEN_DESC_PREFIXES:
        return "BROKEN"
    if first_word in _BUILD_DESC_PREFIXES:
        return "BUILD"
    return "BUILD"


def load_catalog_entries() -> list[CorpusEntry]:
    """Load skill descriptions from ~/.claude/skill_router_catalog.json.

    Fail-open: returns [] if the file is missing, unreadable, or malformed.
    Each catalog entry is converted to a CorpusEntry whose prompt text is the
    skill description (richer signal than a short name alone).
    """
    if not CATALOG_PATH.exists():
        log(f"catalog not found at {CATALOG_PATH}, skipping")
        return []
    try:
        with CATALOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        log(f"catalog load error (fail-open): {e}")
        return []

    entries_raw = data.get("entries", [])
    if not isinstance(entries_raw, list):
        log("catalog 'entries' field is not a list, skipping")
        return []

    result: list[CorpusEntry] = []
    skipped = 0
    for item in entries_raw:
        name = (item.get("name") or "").strip()
        description = (item.get("description") or "").strip()
        if not name or not description:
            skipped += 1
            continue
        path = _classify_catalog_route(name, description)
        result.append(CorpusEntry(
            prompt=description,
            path=path,
            skill=name,
            embedding=np.zeros(0),  # filled in by build_corpus()
        ))

    log(f"catalog loaded: {len(result)} entries ({skipped} skipped, no name/description)")
    return result


def load_calibration_cases() -> list[CorpusEntry]:
    """Load curated prompts and label them with the router's canonical output.

    Calibration expectations can mention aspirational or unavailable
    specialist names. The fallback must announce the same installed skills as
    router.py, so the corpus stores router.route()'s current primary step
    rather than the fixture's expected skill string.

    SKIP cases are excluded because they're the inputs we're trying to rescue
    from silent SKIP. Including them would just teach the embedder to confirm
    SKIP labels.
    """
    sys.path.insert(0, str(PROJECT_DIR / "tests"))
    try:
        import calibration  # type: ignore[import-not-found]
        import router  # type: ignore[import-not-found]
    except ImportError as e:
        log(f"FATAL could not import corpus source: {e}")
        return []

    entries: list[CorpusEntry] = []
    for case in calibration.CASES:
        if case.path == "SKIP":
            continue  # see docstring
        actual_path, chain, _, _ = router.route(case.prompt)
        if actual_path == "SKIP" or not chain:
            log(f"skipping corpus case with no canonical route: {case.prompt[:80]!r}")
            continue
        entries.append(CorpusEntry(
            prompt=case.prompt,
            path=actual_path,
            skill=chain[0].skill,
            embedding=np.zeros(0),  # filled in by build_corpus()
        ))
    return entries


def build_corpus(model, raw: list[CorpusEntry]) -> list[CorpusEntry]:
    """Compute embeddings for every corpus prompt. One-shot at daemon startup."""
    if not raw:
        return []
    prompts = [e.prompt for e in raw]
    log(f"embedding {len(prompts)} corpus prompts")
    vectors = list(model.embed(prompts))
    out: list[CorpusEntry] = []
    for entry, vec in zip(raw, vectors):
        normed = vec / (np.linalg.norm(vec) + 1e-12)
        out.append(CorpusEntry(entry.prompt, entry.path, entry.skill, normed))
    return out


# ---- k-NN classification ---------------------------------------------------

def classify(query_vec: np.ndarray, corpus: list[CorpusEntry]) -> dict:
    """Return classification result for a query embedding.

    Strategy:
      1. Cosine similarity against every corpus entry (brute force ~5ms @ 100).
      2. Take top-K nearest.
      3. If enough neighbors share the same path, the winning path's average
         similarity clears CONFIDENCE_THRESHOLD, and the winner beats the
         runner-up path by MARGIN_THRESHOLD,
         return that path + the most-common skill.
      4. Otherwise, SKIP — silent (preserves precision principle).
    """
    if not corpus:
        return {"path": "SKIP", "reason": "empty_corpus"}

    # Brute-force cosine: corpus is normalized, query needs to be normalized.
    qnorm = query_vec / (np.linalg.norm(query_vec) + 1e-12)
    matrix = np.stack([e.embedding for e in corpus])
    sims = matrix @ qnorm  # cosine since both are unit-normalized

    # Top-K
    k = min(TOP_K, len(corpus))
    top_idx = np.argpartition(-sims, k - 1)[:k]
    top_idx = top_idx[np.argsort(-sims[top_idx])]
    top_entries = [corpus[i] for i in top_idx]
    top_sims = [float(sims[i]) for i in top_idx]
    avg_sim = float(np.mean(top_sims))

    # Path voting
    path_counts: dict[str, int] = {}
    for e in top_entries:
        path_counts[e.path] = path_counts.get(e.path, 0) + 1
    winner_path, winner_count = max(path_counts.items(), key=lambda kv: kv[1])

    path_sims: dict[str, list[float]] = {}
    for e, sim in zip(top_entries, top_sims):
        path_sims.setdefault(e.path, []).append(sim)
    path_avg_sims = {
        path: float(np.mean(values))
        for path, values in path_sims.items()
    }
    winner_avg_sim = path_avg_sims[winner_path]
    runner_up_avg_sim = max(
        (sim for path, sim in path_avg_sims.items() if path != winner_path),
        default=0.0,
    )
    margin = winner_avg_sim - runner_up_avg_sim

    if (
        winner_count < AGREEMENT_THRESHOLD
        or winner_avg_sim < CONFIDENCE_THRESHOLD
        or margin < MARGIN_THRESHOLD
    ):
        return {
            "path": "SKIP",
            "reason": "low_confidence",
            "winner_count": winner_count,
            "avg_sim": avg_sim,
            "winner_avg_sim": winner_avg_sim,
            "runner_up_avg_sim": runner_up_avg_sim,
            "margin": margin,
            "neighbors": [
                {"prompt": e.prompt[:80], "path": e.path, "skill": e.skill, "sim": s}
                for e, s in zip(top_entries, top_sims)
            ],
        }

    # Skill voting — among neighbors that share the winner path
    winner_neighbors = [e for e in top_entries if e.path == winner_path]
    skill_counts: dict[str, int] = {}
    for e in winner_neighbors:
        skill_counts[e.skill] = skill_counts.get(e.skill, 0) + 1
    winner_skill = max(skill_counts.items(), key=lambda kv: kv[1])[0]

    return {
        "path": winner_path,
        "skill": winner_skill,
        "domains": [],
        "confidence": winner_avg_sim,
        "agreement": f"{winner_count}/{k}",
        "avg_sim": avg_sim,
        "winner_avg_sim": winner_avg_sim,
        "runner_up_avg_sim": runner_up_avg_sim,
        "margin": margin,
        "neighbors": [
            {"prompt": e.prompt[:80], "path": e.path, "skill": e.skill, "sim": s}
            for e, s in zip(top_entries, top_sims)
        ],
    }


# ---- Server ----------------------------------------------------------------

class State:
    def __init__(self):
        self.model = None
        self.corpus: list[CorpusEntry] = []
        self.lock = threading.Lock()


def handle_client(conn: socket.socket, state: State) -> None:
    try:
        with conn:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            if not data:
                return
            try:
                req = json.loads(data.decode("utf-8").splitlines()[0])
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                conn.sendall((json.dumps({"error": f"bad_json: {e}"}) + "\n").encode())
                return

            op = req.get("op")
            if op == "ping":
                resp = {
                    "ok": True,
                    "corpus_size": len(state.corpus),
                    "model": MODEL_NAME,
                    "top_k": TOP_K,
                    "threshold": CONFIDENCE_THRESHOLD,
                    "agreement_threshold": AGREEMENT_THRESHOLD,
                    "margin_threshold": MARGIN_THRESHOLD,
                }
            elif op == "classify":
                prompt = req.get("prompt", "").strip()
                if not prompt:
                    resp = {"path": "SKIP", "reason": "empty_prompt"}
                else:
                    with state.lock:
                        if state.model is None:
                            resp = {"path": "SKIP", "reason": "model_not_loaded"}
                        else:
                            t0 = time.perf_counter()
                            qvec = list(state.model.embed([prompt]))[0]
                            resp = classify(qvec, state.corpus)
                            resp["_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            elif op == "shutdown":
                resp = {"ok": True}
                conn.sendall((json.dumps(resp) + "\n").encode())
                log("shutdown via socket")
                os._exit(0)
            else:
                resp = {"error": f"unknown_op: {op}"}

            conn.sendall((json.dumps(resp) + "\n").encode())
    except Exception as e:  # noqa: BLE001
        log(f"client handler error: {e}")


def serve() -> None:
    log(
        f"daemon starting (pid={os.getpid()}, top_k={TOP_K}, "
        f"threshold={CONFIDENCE_THRESHOLD}, agreement={AGREEMENT_THRESHOLD}, "
        f"margin={MARGIN_THRESHOLD})"
    )
    state = State()

    # Load model (slow — ~300-500ms)
    t0 = time.perf_counter()
    from fastembed import TextEmbedding  # lazy import keeps cold-start measurable
    state.model = TextEmbedding(MODEL_NAME)
    log(f"model loaded in {round((time.perf_counter() - t0) * 1000)}ms")

    # Build corpus — calibration cases + catalog entries, deduplicated
    t0 = time.perf_counter()
    calibration_raw = load_calibration_cases()
    catalog_raw = load_catalog_entries()

    # Deduplicate by (text, skill): catalog wins when there is an overlap.
    seen: dict[tuple[str, str], CorpusEntry] = {}
    for entry in calibration_raw:
        seen[(entry.prompt, entry.skill)] = entry
    for entry in catalog_raw:
        seen[(entry.prompt, entry.skill)] = entry  # catalog overwrites if same key

    raw = list(seen.values())
    state.corpus = build_corpus(state.model, raw)
    log(
        f"corpus built ({len(state.corpus)} entries: "
        f"{len(calibration_raw)} calibration + {len(catalog_raw)} catalog, "
        f"deduped to {len(raw)}) in {round((time.perf_counter() - t0) * 1000)}ms"
    )

    # Bind socket
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)  # owner-only — local privacy
    sock.listen(8)
    log(f"listening on {SOCKET_PATH}")

    while True:
        try:
            conn, _ = sock.accept()
            t = threading.Thread(target=handle_client, args=(conn, state), daemon=True)
            t.start()
        except KeyboardInterrupt:
            log("interrupted, shutting down")
            break
        except Exception as e:  # noqa: BLE001
            log(f"accept error: {e}")
            time.sleep(0.1)


if __name__ == "__main__":
    serve()
