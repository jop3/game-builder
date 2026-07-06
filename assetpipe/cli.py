"""CLI entry points (spec 20.2).

Every subcommand is a thin wrapper over an existing module so Claude (or CI)
can drive and debug any stage in isolation:

    assetpipe generate  --request path.json [--out runs/] [--max-iterations N]
    assetpipe batch     --requests batch.json [--out runs/] [--parallel N]
    assetpipe validate  --glb some.glb --request path.json      # V1 (GLB) only
    assetpipe render    --glb some.glb --out renders/           # R only
    assetpipe inspect   --renders renders/ --request path.json  # V2 only
    assetpipe deliver   --run runs/<id> --adapter godot --project /path
    assetpipe resume    --run runs/<id>
    assetpipe report    --run runs/<id>

Run as ``python -m assetpipe <command> ...``. Commands print a JSON result to
stdout and exit 0 on success / 1 on failure so they compose in shell scripts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assetpipe.contracts import Contracts
from assetpipe.pipeline_config import load_config

BLENDER_SCRIPTS_DIR = Path(__file__).parent / "blender_scripts"


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _load_one_request(path: Path) -> dict:
    from assetpipe.intake import load_requests
    requests = load_requests(Path(path))
    if len(requests) != 1:
        raise SystemExit(f"{path} must contain exactly one request (got {len(requests)})")
    return requests[0]


def _config(args) -> dict:
    return load_config(Path(args.config) if getattr(args, "config", None) else None)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_generate(args) -> int:
    """One asset through the full loop: a single-request batch (spec 20.2)."""
    from assetpipe.orchestrator import run_batch
    cfg = _config(args)
    if args.max_iterations is not None:
        cfg["iteration"]["max_iterations"] = args.max_iterations
    manifest = run_batch(Path(args.request), Path(args.out), config=cfg,
                         blender_bin=args.blender_bin, parallel=1,
                         vision_client_factory=_vision_client_factory(cfg))
    _print(manifest)
    statuses = {e.get("status") for e in manifest.get("assets", {}).values()}
    return 0 if statuses and statuses <= {"validated", "best_effort"} else 1


def cmd_batch(args) -> int:
    from assetpipe.orchestrator import run_batch
    cfg = _config(args)
    manifest = run_batch(Path(args.requests), Path(args.out), config=cfg,
                         blender_bin=args.blender_bin, parallel=args.parallel,
                         vision_client_factory=_vision_client_factory(cfg))
    _print(manifest)
    return 0 if not manifest.get("aborted") else 1


def cmd_validate(args) -> int:
    """Standalone V1 on an exported .glb: the orchestrator-side GLB structural
    checks (S20b-S20d; spec 13.5). Mesh/UV checks (S1-S12) need the authoring
    .blend and run inside Blender; map checks (S14-S18) need the iteration's
    maps/ dir - both out of scope for a bare-glb invocation."""
    from assetpipe.validation.glb import run_glb_checks
    request = _load_one_request(Path(args.request))
    cfg = _config(args)
    contracts = Contracts.load()
    profile = contracts.profile(request["platform_profile"])
    cap = profile.get("file_bytes", {}).get(request["category"], 0) or 0
    checks = run_glb_checks(
        Path(args.glb), expected={}, max_bytes=cap,
        whitelist=frozenset(cfg["validation"]["gltf_extension_whitelist"]))
    verdict = "pass" if all(c["verdict"] == "pass" for c in checks) else "fail"
    _print({"asset_id": request["asset_id"], "stage": "V1(glb)", "verdict": verdict,
            "checks": checks})
    return 0 if verdict == "pass" else 1


def cmd_render(args) -> int:
    """Standalone R: spawn the render harness on an exported .glb."""
    import subprocess
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = _config(args)
    payload = {"request": _load_one_request(Path(args.request)) if args.request else
               {"asset_id": Path(args.glb).stem, "category": "prop_small"},
               "glb_path": str(Path(args.glb)), "out_dir": str(out_dir),
               "render_config": cfg.get("render", {}), "iter_dir": str(out_dir.parent),
               "iteration": 0}
    args_path = out_dir / "render.args.json"
    args_path.write_text(json.dumps(payload, indent=2))
    cmd = [args.blender_bin, "--background", "--python",
           str(BLENDER_SCRIPTS_DIR / "render_views.py"), "--", "--args-json", str(args_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=cfg.get("stage_timeouts", {}).get("render", 900))
    _print({"exit": proc.returncode, "out_dir": str(out_dir),
            "stderr_tail": (proc.stderr or "")[-500:]})
    return 0 if proc.returncode == 0 else 1


def cmd_inspect(args) -> int:
    """Standalone V2 on an existing renders/ directory."""
    from assetpipe.orchestrator import DEFAULT_THEMES_ROOT, _load_theme
    from assetpipe.vision.inspector import inspect_asset
    request = _load_one_request(Path(args.request))
    cfg = _config(args)
    contracts = Contracts.load()
    theme = _load_theme(DEFAULT_THEMES_ROOT, request.get("theme"))
    renders_dir = Path(args.renders)
    contact_sheets = sorted(renders_dir.glob("contact_sheet_*.png")) \
        or sorted(renders_dir.glob("*.png"))[:6]
    result = inspect_asset(_anthropic_client(), request=request, theme=theme,
                           bbox_range=str(request.get("bbox_range", "unspecified")),
                           contact_sheets=contact_sheets, renders_dir=renders_dir,
                           iteration=0, contracts=contracts, config=cfg,
                           log_path=renders_dir / "vision_call.json")
    _print({"passed": result.passed,
            "blockers": [f.__dict__ for f in result.blockers],
            "warns": [f.__dict__ for f in result.warns]})
    return 0 if result.passed else 1


def cmd_deliver(args) -> int:
    """Deliver every validated/best_effort asset of a run via an adapter
    (spec 18-19). Adapter verification failures mark delivery_failed in the
    run manifest but never touch the canonical artifacts."""
    from assetpipe.adapters import get_adapter
    from assetpipe.rundir import update_run_manifest
    run_root = Path(args.run)
    manifest_path = run_root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    adapter = get_adapter(args.adapter, project_path=Path(args.project),
                          godot_bin=args.godot_bin)
    results = {}
    for asset_id, entry in manifest.get("assets", {}).items():
        if entry.get("status") not in ("validated", "best_effort"):
            continue
        asset_dir = run_root / asset_id
        final_manifest_path = asset_dir / "final" / "manifest.json"
        if not final_manifest_path.exists():
            results[asset_id] = {"delivered": False, "error": "no final/manifest.json"}
            continue
        asset_manifest = json.loads(final_manifest_path.read_text())
        record = adapter.deliver(asset_dir, asset_manifest, Path(args.project))
        report = adapter.verify(record)
        results[asset_id] = {"delivered": True, "verified": report.passed,
                             "errors": report.errors}
        if not report.passed:
            def _mark(m: dict, asset_id=asset_id) -> None:
                m["assets"][asset_id]["delivery_failed"] = True
            update_run_manifest(manifest_path, _mark)
    _print({"adapter": args.adapter, "results": results})
    return 0 if all(r.get("verified") for r in results.values()) else 1


def cmd_resume(args) -> int:
    from assetpipe.orchestrator import resume_run
    cfg = _config(args)
    manifest = resume_run(Path(args.run), config=cfg, blender_bin=args.blender_bin,
                          vision_client_factory=_vision_client_factory(cfg))
    _print(manifest)
    return 0


def cmd_report(args) -> int:
    """Human/model-readable run summary from the manifest + per-asset history."""
    run_root = Path(args.run)
    manifest = json.loads((run_root / "run_manifest.json").read_text())
    lines = [f"run {manifest.get('run_id', run_root.name)}",
             f"totals: {json.dumps(manifest.get('totals', {}))}"]
    for asset_id, entry in sorted(manifest.get("assets", {}).items()):
        line = f"  {asset_id}: {entry.get('status')}"
        if entry.get("status") == "best_effort":
            defects = entry.get("remaining_defects", [])
            line += f" ({len(defects)} remaining defect(s))"
        if entry.get("error"):
            line += f" - {entry['error']}"
        if entry.get("delivery_failed"):
            line += " [delivery_failed]"
        lines.append(line)
        history = run_root / asset_id / "history.jsonl"
        if args.verbose and history.exists():
            for raw in history.read_text().splitlines():
                ev = json.loads(raw)
                if ev["event"] in ("terminal", "error", "fix_planned"):
                    lines.append(f"    {ev['event']}: " + json.dumps(
                        {k: v for k, v in ev.items()
                         if k not in ("t", "asset", "event")}))
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def _anthropic_client():
    import anthropic
    return anthropic.Anthropic()


def _vision_client_factory(cfg: dict):
    return lambda: _anthropic_client()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assetpipe",
                                     description="Autonomous game asset pipeline (spec 20.2)")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--config", help="pipeline.yaml overriding config/defaults.yaml")
        p.add_argument("--blender-bin", default="blender")

    p = sub.add_parser("generate", help="run one asset request through the full loop")
    common(p)
    p.add_argument("--request", required=True)
    p.add_argument("--out", default="runs")
    p.add_argument("--max-iterations", type=int, default=None)
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("batch", help="run a batch of asset requests")
    common(p)
    p.add_argument("--requests", required=True)
    p.add_argument("--out", default="runs")
    p.add_argument("--parallel", type=int, default=None)
    p.set_defaults(fn=cmd_batch)

    p = sub.add_parser("validate", help="standalone V1 GLB structural checks")
    common(p)
    p.add_argument("--glb", required=True)
    p.add_argument("--request", required=True)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("render", help="standalone render harness on a .glb")
    common(p)
    p.add_argument("--glb", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--request", default=None)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("inspect", help="standalone V2 vision inspection on renders/")
    common(p)
    p.add_argument("--renders", required=True)
    p.add_argument("--request", required=True)
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("deliver", help="deliver a run's assets via an engine adapter")
    p.add_argument("--run", required=True)
    p.add_argument("--adapter", default="godot")
    p.add_argument("--project", required=True)
    p.add_argument("--godot-bin", default="godot")
    p.set_defaults(fn=cmd_deliver)

    p = sub.add_parser("resume", help="resume a crashed run")
    common(p)
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("report", help="summarize a run")
    p.add_argument("--run", required=True)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
