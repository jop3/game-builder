"""Engine adapter contract (spec §18): the pipeline core never imports engine
specifics. An adapter is a plain Python class registered under a name
(`assetpipe/adapters/__init__.py::get_adapter`); `pipeline.yaml ->
delivery.adapters: [...]` selects which ones run.

Adapter rules (spec §18, verbatim):
- adapters may *add* engine files and *compress copies*, but must never mutate
  the canonical `final/` artifacts produced by the fix loop;
- `deliver()` must be idempotent — re-delivery overwrites cleanly, it never
  accumulates stale files from a previous delivery;
- `verify()` failures mark the asset `delivery_failed` in the run manifest but
  never re-enter the fix loop (the loop guards the canonical asset; an adapter
  bug is a pipeline bug, not an asset defect).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class DeliveryRecord:
    """What `deliver()` did, handed to `verify()` unchanged.

    Fields mirror the per-asset manifest (spec §17.3): `asset_id`, `category`,
    `theme`, `container` (`"glb"` | `"exr"` | ...) identify *what* was
    delivered; `delivered_paths` + `target_root` say *where* (inside the
    engine project); `manifest` is the full per-asset manifest dict as
    delivered alongside the asset.

    `asset_dir` is one extra field beyond the spec's minimal shape: it is the
    *source* run directory (`runs/<run_id>/<asset_id>/`), needed so `verify()`
    can write `final/godot_report.json` back next to the canonical artifacts
    per spec §19.4 ("stored at runs/<run_id>/<asset_id>/final/godot_report.json").
    Without it, `DeliveryRecord` would only know the *destination* engine
    project root, not the *source* run directory the report belongs in.
    Optional/defaulted so positional construction with just the seven
    spec-listed fields still works.
    """

    asset_id: str
    category: str
    theme: str
    container: str
    delivered_paths: list[Path]
    target_root: Path
    manifest: dict
    asset_dir: Path | None = None


@dataclass
class AdapterReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


class EngineAdapter(Protocol):
    """Contract every engine adapter implements (spec §18)."""

    name: str

    def deliver(self, asset_dir: Path, manifest: dict, target_root: Path) -> DeliveryRecord:
        """Copy/transform `asset_dir/final/` artifacts into the engine project
        rooted at `target_root`. Pure file ops + engine-CLI calls. Must be
        idempotent (re-delivery overwrites cleanly)."""
        ...

    def verify(self, record: DeliveryRecord) -> AdapterReport:
        """Headless-engine check that the asset actually imports and
        instantiates. Returns pass/fail + errors; failures mark the asset
        `delivery_failed` in the run manifest (the asset itself keeps its
        validated status — the canonical glb is fine; the adapter is what's
        broken) and never re-enter the fix loop."""
        ...
