"""Run-directory layout and append-only history logging (spec 17.1-17.2).

:class:`RunDir` is the single place that knows the on-disk shape of
``runs/<run_id>/...`` so no other module hand-builds these paths. Every helper
creates the directories it names (idempotently) so callers never need a
separate ``mkdir`` dance.

:class:`HistoryLog` is the append-only ``history.jsonl`` writer (spec 17.2):
one JSON object per line, never rewritten, always carrying an ISO-8601 UTC
``t`` timestamp.

:func:`update_run_manifest` is the single-writer read-modify-write helper for
``run_manifest.json`` (spec 20.4: "no shared mutable state between assets
except the run manifest (single-writer via the orchestrator process)"). A
module-level lock serializes concurrent callers defensively even though the
orchestrator's documented contract is that only the main thread calls it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_manifest_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RunDir:
    """Layout helpers for one run directory (spec 17.1).

    ```
    runs/<run_id>/
      run_manifest.json
      pipeline_config_snapshot.yaml
      <asset_id>/
        request.json
        history.jsonl
        diagnosis.md              (best_effort only)
        iter_NN/{maps,renders,logs}/
        final/
    ```
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- run-level ----------

    @property
    def run_manifest_path(self) -> Path:
        return self.root / "run_manifest.json"

    @property
    def config_snapshot_path(self) -> Path:
        return self.root / "pipeline_config_snapshot.yaml"

    # ---------- per-asset ----------

    def asset_dir(self, asset_id: str) -> Path:
        d = self.root / asset_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def request_path(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "request.json"

    def history_path(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "history.jsonl"

    def diagnosis_path(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "diagnosis.md"

    def iter_dir(self, asset_id: str, n: int) -> Path:
        """``<asset>/iter_NN/`` with its ``maps/``, ``renders/``, ``logs/``
        subdirectories created (spec 17.1)."""
        d = self.asset_dir(asset_id) / f"iter_{n:02d}"
        for sub in ("maps", "renders", "logs"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def final_dir(self, asset_id: str) -> Path:
        d = self.asset_dir(asset_id) / "final"
        d.mkdir(parents=True, exist_ok=True)
        return d


class HistoryLog:
    """Append-only ``history.jsonl`` writer (spec 17.2).

    Event types: ``intake``, ``stage_start``, ``stage_end``, ``fix_planned``,
    ``fix_applied``, ``state_change``, ``escalation``, ``error``, ``terminal``
    -- this class does not enforce the enum (callers own event semantics); it
    only guarantees the file is append-only and every line carries ``t``.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event_type: str, asset: str, iter: int | None = None, **fields) -> dict:
        entry = {"t": _now_iso(), "asset": asset, "event": event_type}
        if iter is not None:
            entry["iter"] = iter
        entry.update(fields)
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return entry


def new_run_id(batch_path: Path, now: Callable[[], datetime] | None = None) -> str:
    """``run_id`` = UTC timestamp + short sha of the batch file's bytes (spec
    17.1), so re-running the identical batch file at a different time never
    collides, and identical (path, content, second) collisions are
    vanishingly unlikely."""
    ts = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y%m%dT%H%M%SZ")
    sha = hashlib.sha256(Path(batch_path).read_bytes()).hexdigest()[:8]
    return f"{ts}_{sha}"


def update_run_manifest(path: Path, mutate_fn: Callable[[dict], None]) -> dict:
    """Single-writer read-modify-write of ``run_manifest.json``: read the
    current dict (``{}`` if the file doesn't exist yet), call
    ``mutate_fn(manifest)`` to mutate it in place, write it back, and return
    the resulting dict. Serialized by a module-level lock."""
    path = Path(path)
    with _manifest_lock:
        manifest = json.loads(path.read_text()) if path.exists() else {}
        mutate_fn(manifest)
        path.write_text(json.dumps(manifest, indent=2, default=str))
        return manifest
