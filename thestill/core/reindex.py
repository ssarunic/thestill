"""Spec #28 §2.4 — reindex stage handler.

Two-step process for one episode:

1. ``write-corpus`` (the previous stage) has already produced
   ``data/corpus/episodes/<podcast-slug>/<episode-id>.md`` and the
   matching ``<episode-id>.segmap.json`` sidecar.
2. This module shells out to the ``qmd`` binary to update the lexical
   index and refresh the vector embeddings for the touched paths.

Per the Phase 0.1 spike: qmd has no programmatic write API — its
MCP server is read-only — so we use the CLI subcommands ``qmd update
--paths <...>`` and ``qmd embed`` over a subprocess. The MCP transport
is reserved for queries (Phase 2.5).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from structlog import get_logger

logger = get_logger(__name__)


DEFAULT_COLLECTION = "thestill-corpus"


class QmdNotInstalledError(RuntimeError):
    """Raised when the ``qmd`` binary cannot be located on PATH.

    The reindex stage treats this as a soft failure on individual
    runs — the writer has already produced the Markdown + segmap, so
    a fresh ``qmd-up`` later catches up. The handler logs at warn
    level and returns without raising.
    """


def reindex_paths(
    paths: Sequence[Path],
    *,
    collection: str = DEFAULT_COLLECTION,
    embed: bool = True,
    qmd_binary: Optional[str] = None,
) -> dict:
    """Tell qmd to re-ingest specific files and (optionally) re-embed them.

    Returns a small report dict ``{"updated": int, "embedded": bool,
    "skipped": list[str]}``.

    ``paths`` may be empty — reindexing zero files is a no-op that
    returns ``{"updated": 0, ...}`` without invoking qmd.
    """
    files = [Path(p) for p in paths if p]
    files = [p for p in files if p.suffix == ".md"]  # .segmap.json is for us only
    if not files:
        return {"updated": 0, "embedded": False, "skipped": []}

    binary = qmd_binary or shutil.which("qmd")
    if binary is None:
        raise QmdNotInstalledError(
            "qmd binary not found on PATH. Install qmd (https://qmd.dev) "
            "and re-run, or call ``make qmd-up`` to bootstrap the index."
        )

    skipped: List[str] = []
    existing = []
    for p in files:
        if p.exists():
            existing.append(str(p))
        else:
            skipped.append(str(p))

    if not existing:
        return {"updated": 0, "embedded": False, "skipped": skipped}

    logger.info("qmd_update_invoking", file_count=len(existing), collection=collection)
    update_cmd = [binary, "update", "--paths", *existing]
    _run(update_cmd, label="qmd update")

    if embed:
        logger.info("qmd_embed_invoking")
        _run([binary, "embed"], label="qmd embed")

    return {"updated": len(existing), "embedded": embed, "skipped": skipped}


def bootstrap_collection(
    corpus_dir: Path,
    *,
    collection: str = DEFAULT_COLLECTION,
    glob: str = "**/*.md",
    qmd_binary: Optional[str] = None,
) -> dict:
    """Idempotent ``qmd collection add`` for the corpus root.

    If the named collection already exists qmd's ``add`` exits non-zero
    with "already registered"; we treat that as success rather than a
    failure. Returns ``{"added": bool, "collection": ..., "path": ...}``.
    """
    binary = qmd_binary or shutil.which("qmd")
    if binary is None:
        raise QmdNotInstalledError("qmd binary not found on PATH. See https://qmd.dev for installation.")
    if not corpus_dir.exists():
        raise FileNotFoundError(f"corpus directory does not exist: {corpus_dir}")

    # qmd CLI: ``collection add <path> [--name NAME] [--glob GLOB]``.
    # Order matters — path is positional, name is an option.
    cmd = [binary, "collection", "add", str(corpus_dir), "--name", collection, "--glob", glob]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    already = any(s in output.lower() for s in ("already exists", "already registered", "duplicate"))
    if proc.returncode != 0 and not already:
        logger.warning("qmd_collection_add_failed", returncode=proc.returncode, output=output[:500])
        raise RuntimeError(f"qmd collection add failed: {output.strip()}")
    if already:
        logger.info("qmd_collection_already_registered", collection=collection)
        return {"added": False, "collection": collection, "path": str(corpus_dir)}
    logger.info("qmd_collection_added", collection=collection, path=str(corpus_dir))
    return {"added": True, "collection": collection, "path": str(corpus_dir)}


def _run(cmd: Sequence[str], *, label: str) -> None:
    """Run a qmd CLI command and surface meaningful output on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        logger.error(
            f"{label}_failed",
            cmd=" ".join(cmd),
            returncode=proc.returncode,
            stdout=(proc.stdout or "")[:500],
            stderr=(proc.stderr or "")[:500],
        )
        raise RuntimeError(f"{label} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    logger.debug(f"{label}_completed", cmd=" ".join(cmd[:3]) + " …")
