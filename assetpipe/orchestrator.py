"""Batch orchestration: toolchain gate, intake, per-asset loops, delivery
(spec 4, 16.5-16.6, 17, 20, 22, README item 2).

:func:`run_batch` is the top-level entry point (``assetpipe batch``): it loads
config, hard-gates on toolchain version mismatch (spec 3), creates the run
directory, intakes the batch, then runs each accepted asset's
:func:`~assetpipe.loop.run_asset_loop` via :class:`~assetpipe.stages.SubprocessStages`
in a thread pool. :func:`_run_one_asset` never raises -- every exception is
caught and turned into a ``hard_failed`` manifest entry so one broken asset
never stops the batch (spec 16.5.4, 22).

**Deviation (documented): threads, not processes, for asset parallelism.**
Spec 20.4 says "process-pool over assets". Every unit of concurrent work here
is itself a Blender *subprocess* (spawned by ``SubprocessStages``), so the
orchestrator's own parallelism only needs to overlap I/O-bound waits on those
subprocesses plus (serialized per-asset, per spec 20.4) vision API calls --
there is no CPU-bound work in the orchestrator process itself that a thread
pool would serialize behind the GIL. A ``ThreadPoolExecutor`` gives the same
effective concurrency as a process pool here with far simpler manifest/state
sharing (the single-writer manifest lock in ``rundir.update_run_manifest``
would otherwise need cross-process synchronization), and is equivalent under
spec 20.4's actual constraint ("no shared mutable state between assets except
the run manifest, single-writer").
"""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import yaml

from assetpipe.contracts import Contracts
from assetpipe.diagnosis import write_diagnosis
from assetpipe.fixes.planner import LadderConfig
from assetpipe.generators.registry import Registry
from assetpipe.intake import load_requests, validate_batch
from assetpipe.loop import LoopConfig, State, run_asset_loop
from assetpipe.pipeline_config import default_probes, load_config, toolchain_check
from assetpipe.rundir import HistoryLog, RunDir, new_run_id, update_run_manifest
from assetpipe.stages import SubprocessStages

DEFAULT_THEMES_ROOT = Path(__file__).parent.parent / "themes"

_TERMINAL_STATUSES = {"validated", "best_effort", "hard_failed", "intake_rejected"}


def _themes_root(config: dict) -> Path:
    return Path(config.get("themes_root") or DEFAULT_THEMES_ROOT)


def _loop_config(config: dict) -> LoopConfig:
    it = config["iteration"]
    return LoopConfig(
        ladder=LadderConfig(max_iterations=it["max_iterations"],
                            subcomponent_regen_from=it["subcomponent_regen_from"],
                            full_regen_from=it["full_regen_from"],
                            full_regen_allowed=it["full_regen_allowed"]),
        wall_clock_budget_s=it["wall_clock_minutes_per_asset"] * 60,
        ride_along_warns=it.get("ride_along_warns", True))


def _load_theme(themes_root: Path, theme_name: str | None) -> dict:
    """Tolerate a missing theme file (README/task note: intake already
    validated theme existence when a themes_root was supplied to it; a
    missing file here just means "no theme content available yet")."""
    if not theme_name:
        return {}
    theme_path = Path(themes_root) / theme_name / "theme.json"
    if not theme_path.exists():
        return {}
    try:
        return json.loads(theme_path.read_text())
    except json.JSONDecodeError:
        return {}


def _populate_final(asset_dir: Path, run_dir: RunDir, request: dict, result) -> dict:
    asset_id = request["asset_id"]
    shipped = result.shipped_iteration
    iter_dir = run_dir.iter_dir(asset_id, shipped)
    final_dir = run_dir.final_dir(asset_id)

    glb_src = iter_dir / f"{asset_id}.glb"
    if not glb_src.exists():
        alt = iter_dir / "asset.glb"
        glb_src = alt if alt.exists() else glb_src
    if glb_src.exists():
        shutil.copy2(glb_src, final_dir / "asset.glb")

    previews = []
    renders_dir = iter_dir / "renders"
    if renders_dir.is_dir():
        for turn_png in sorted(renders_dir.glob("turn_*.png")):
            dest_name = f"preview_{turn_png.name}"
            shutil.copy2(turn_png, final_dir / dest_name)
            previews.append(dest_name)

    remaining = [{"check_id": f.check_id, "defect_type": f.defect_type,
                 "severity": f.severity, "location": f.location}
                for f in result.remaining_defects]

    status = "validated" if result.state == State.VALIDATED else "best_effort"
    manifest_entry = {
        "asset_id": asset_id, "status": status, "category": request.get("category"),
        "theme": request.get("theme"), "platform_profile": request.get("platform_profile"),
        "seed": request.get("seed"), "iterations_used": len(result.iterations),
        "shipped_iteration": shipped, "container": "glb",
        "files": {"asset": "asset.glb", "previews": previews},
        "remaining_defects": remaining,
    }
    (final_dir / "manifest.json").write_text(json.dumps(manifest_entry, indent=2))

    if status == "best_effort":
        write_diagnosis(asset_dir, request, result)

    return manifest_entry


def _run_one_asset(request: dict, run_dir: RunDir, contracts: Contracts, config: dict,
                   registry: Registry, blender_bin: str, runner: Callable,
                   vision_client_factory: Callable | None, clock: Callable[[], float]) -> dict:
    """Drive one asset's repair loop end to end. Never raises: any exception
    is caught and turned into a ``hard_failed`` manifest entry so the batch
    continues (spec 16.5.4, 22)."""
    asset_id = request["asset_id"]
    asset_dir = run_dir.asset_dir(asset_id)
    history = HistoryLog(run_dir.history_path(asset_id))
    try:
        (asset_dir / "request.json").write_text(json.dumps(request, indent=2))
        history.event("intake", asset_id, status="accepted")

        theme = _load_theme(_themes_root(config), request.get("theme"))
        generator = request.get("generator")
        param_schema = {}
        if generator and registry is not None and generator in registry:
            param_schema = getattr(registry.get(generator), "PARAM_SCHEMA", {})

        vision_client = vision_client_factory() if vision_client_factory else None
        stages = SubprocessStages(request=request, run_dir=run_dir, contracts=contracts,
                                  config=config, theme=theme, param_schema=param_schema,
                                  registry=registry, blender_bin=blender_bin, runner=runner,
                                  vision_client=vision_client, history=history)

        result = run_asset_loop(request, stages, contracts, _loop_config(config), clock)

        for ev in result.events:
            ev = dict(ev)
            event_type = ev.pop("event")
            history.event(event_type, asset_id, **ev)
        history.event("terminal", asset_id, state=result.state.value,
                     stop_reason=result.stop_reason, shipped_iteration=result.shipped_iteration)

        if result.state in (State.VALIDATED, State.BEST_EFFORT):
            return _populate_final(asset_dir, run_dir, request, result)

        entry = {"asset_id": asset_id, "status": "hard_failed", "category": request.get("category"),
                 "theme": request.get("theme"), "error": result.stop_reason}
        return entry
    except Exception as exc:  # noqa: BLE001 - must never escape (spec 16.5.4)
        history.event("error", asset_id, error=str(exc))
        return {"asset_id": asset_id, "status": "hard_failed",
               "category": request.get("category"), "error": str(exc)}


def run_batch(batch_path: Path, out_root: Path, *, config: dict | None = None,
             blender_bin: str = "blender", runner: Callable = None,
             vision_client_factory: Callable | None = None,
             clock: Callable[[], float] = time.monotonic,
             parallel: int | None = None) -> dict:
    """Run every asset in ``batch_path`` and return the final run manifest
    (also persisted at ``<out_root>/<run_id>/run_manifest.json``)."""
    import subprocess as _subprocess
    runner = runner or _subprocess.run

    batch_path = Path(batch_path)
    cfg = config if config is not None else load_config()
    contracts = Contracts.load()
    registry = Registry.discover()

    probes = default_probes(blender_bin=blender_bin)
    toolchain_errors = toolchain_check(cfg, probes)

    run_id = new_run_id(batch_path)
    run_dir = RunDir(Path(out_root) / run_id)
    run_dir.config_snapshot_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    def _init(manifest: dict) -> None:
        manifest.update({
            "run_id": run_id, "batch_path": str(batch_path),
            "toolchain_errors": toolchain_errors,
            "totals": {"validated": 0, "best_effort": 0, "hard_failed": 0, "intake_rejected": 0},
            "assets": {},
        })
    update_run_manifest(run_dir.run_manifest_path, _init)

    if toolchain_errors and cfg.get("toolchain", {}).get("require_exact", True):
        def _abort(manifest: dict) -> None:
            manifest["aborted"] = True
            manifest["abort_reason"] = "toolchain version mismatch (spec 3 hard gate)"
        return update_run_manifest(run_dir.run_manifest_path, _abort)

    themes_root = _themes_root(cfg)
    requests = load_requests(batch_path)
    accepted, rejected = validate_batch(
        requests, contracts,
        themes_root=themes_root if themes_root.exists() else None, registry=registry)

    for asset_key, errors in rejected.items():
        def _reject(manifest: dict, asset_key=asset_key, errors=errors) -> None:
            manifest["assets"][asset_key] = {"status": "intake_rejected", "errors": errors}
            manifest["totals"]["intake_rejected"] += 1
        update_run_manifest(run_dir.run_manifest_path, _reject)

    # Record every accepted asset as pending BEFORE its loop starts, so a hard
    # crash of the orchestrator process itself leaves a manifest entry that
    # `resume_run` can find and re-run (spec 17.3 / `assetpipe resume`).
    def _mark_pending(manifest: dict) -> None:
        for req in accepted:
            manifest["assets"][req["asset_id"]] = {"status": "pending"}
    update_run_manifest(run_dir.run_manifest_path, _mark_pending)

    max_workers = parallel or cfg.get("parallelism", {}).get("assets", 4)
    if accepted:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {
                pool.submit(_run_one_asset, req, run_dir, contracts, cfg, registry,
                           blender_bin, runner, vision_client_factory, clock): req["asset_id"]
                for req in accepted
            }
            for fut in as_completed(futures):
                asset_id = futures[fut]
                entry = fut.result()

                def _record(manifest: dict, asset_id=asset_id, entry=entry) -> None:
                    manifest["assets"][asset_id] = entry
                    manifest["totals"][entry["status"]] = manifest["totals"].get(entry["status"], 0) + 1
                update_run_manifest(run_dir.run_manifest_path, _record)

    return json.loads(run_dir.run_manifest_path.read_text())


def resume_run(run_root: Path, *, config: dict | None = None, blender_bin: str = "blender",
              runner: Callable = None, vision_client_factory: Callable | None = None,
              clock: Callable[[], float] = time.monotonic, parallel: int | None = None) -> dict:
    """Resume a crashed/interrupted run: re-run every asset whose manifest
    status is not terminal (spec: ``assetpipe resume``).

    **Simplification (documented, acceptable for v1 per task spec):** a
    resumed asset restarts its loop from iteration 1 in the same asset
    directory (new ``iter_01``, overwriting any prior partial ``iter_01``)
    rather than resuming mid-loop from its last completed iteration. Full
    mid-loop resumption would require persisting ``PlannerState``/pending fix
    plan across the crash boundary, which the state machine does not
    currently serialize.
    """
    import subprocess as _subprocess
    runner = runner or _subprocess.run

    run_root = Path(run_root)
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    cfg = config if config is not None else load_config()
    contracts = Contracts.load()
    registry = Registry.discover()
    run_dir = RunDir(run_root)

    to_rerun = []
    for asset_id, entry in manifest.get("assets", {}).items():
        if entry.get("status") in _TERMINAL_STATUSES:
            continue
        request_path = run_dir.request_path(asset_id)
        if request_path.exists():
            to_rerun.append(json.loads(request_path.read_text()))

    max_workers = parallel or cfg.get("parallelism", {}).get("assets", 4)
    if to_rerun:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futures = {
                pool.submit(_run_one_asset, req, run_dir, contracts, cfg, registry,
                           blender_bin, runner, vision_client_factory, clock): req["asset_id"]
                for req in to_rerun
            }
            for fut in as_completed(futures):
                asset_id = futures[fut]
                entry = fut.result()

                def _record(manifest: dict, asset_id=asset_id, entry=entry) -> None:
                    manifest["assets"][asset_id] = entry
                update_run_manifest(manifest_path, _record)

    def _recount(manifest: dict) -> None:
        totals = {"validated": 0, "best_effort": 0, "hard_failed": 0, "intake_rejected": 0}
        for entry in manifest.get("assets", {}).values():
            status = entry.get("status")
            if status in totals:
                totals[status] += 1
        manifest["totals"] = totals
    update_run_manifest(manifest_path, _recount)

    return json.loads(manifest_path.read_text())
