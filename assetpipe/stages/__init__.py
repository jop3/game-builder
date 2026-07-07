"""``SubprocessStages``: the orchestrator's implementation of the
``loop.Stages`` protocol for one asset (spec 4.3, 9, 13, 14, 16, README item 2).

Every method spawns Blender subprocesses via :meth:`SubprocessStages._run_blender`
(``blender --background [<iter_dir>/asset.blend] --python <script> --
--args-json <path>``), one retry on nonzero exit / timeout, then
``loop.InfraError``. Stage code never retries iterations or decides to give
up -- it only ever succeeds, returns findings, or raises ``InfraError`` (the
README invariant "the loop owns stopping").

**Integration seam with `assetpipe/blender_scripts/` (documented deviation).**
That package is being written concurrently; at the time this module was
written, ``generate.py``, ``bake.py``, ``export_gltf.py`` and
``static_checks_mesh.py`` already existed (read for their real ``--args-json``
payload shape and adopted here verbatim: e.g. ``generate.py`` resolves+writes
its own ``params.json`` internally via ``blender_scripts.common.resolve_params``
using ``request["seed"]``, so this module does *not* pre-write params.json --
doing so would be immediately overwritten and risks silent divergence between
two independent implementations of the same pure function). ``render_views.py``
and ``fixes.py`` did not exist yet; their payload shapes below are a
reasonable convention derived from the spec and the sibling scripts' style
(``parse_args``/``write_result``, flat JSON-able payloads, outputs under the
iteration dir) and may need small key-name reconciliation once they land --
this module's own test suite (``test_stages.py``) drives a fake ``blender``
executable so it does not depend on that reconciliation to stay green.

(The payload shapes have since been reconciled: ``render_views.py`` and
``fixes.py`` accept this module's keys, ``bake.py`` takes the bare theme
material id + ``theme_id`` this module sends, and ``bake.py`` writes
``bake_result.json`` so ``generate.py``'s ``result.json`` survives for the
downstream stages that read it.)
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from assetpipe.blender_scripts import contact_sheets
from assetpipe.contracts import Contracts, stage_order
from assetpipe.fixes.apply import ApplyResult, FixContext, apply_fix_plan
from assetpipe.loop import InfraError, StageResult
from assetpipe.matlib.color_words import derive_material_colors
from assetpipe.rundir import HistoryLog, RunDir
from assetpipe.validation.image_checks import (check_backface_fraction, check_clipping,
                                               check_not_empty, check_silhouette_area)
from assetpipe.validation.static_gate import run_static_gate
from assetpipe.vision.inspector import inspect_asset
from assetpipe.vision.report import Finding

BLENDER_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "blender_scripts"

# Per-stage subprocess timeouts (spec 4.3: "default 600 s generate/bake, 900 s
# render"); overridable via config["stage_timeouts"] since config owns every
# threshold (README invariant), but defaults.yaml does not currently declare
# this table, so these are the spec-cited fallbacks.
_DEFAULT_TIMEOUTS = {
    "generate": 600, "bake": 600, "export": 600,
    "static_checks": 300, "render": 900, "fixes": 300,
}

_NUMERIC_TYPES = ("number", "integer")

_L1_VIEW_PREFIXES = ("turn_", "high_")
_L1_VIEW_EXACT = ("top", "close_034")


def _is_l1_view(stem: str) -> bool:
    return stem in _L1_VIEW_EXACT or stem.startswith(_L1_VIEW_PREFIXES)


# ---------------------------------------------------------------------------
# Parameter resolution (spec 9.3) -- pure, unit-testable.
# ---------------------------------------------------------------------------

def resolve_params(param_schema: dict, theme: dict, seed: int,
                   param_overrides: dict | None = None) -> dict:
    """Resolve a generator's final ``params.json`` (spec 9.3):

        recipe defaults -> theme clamps (``<param>_range`` in theme.json) ->
        seeded jitter (uniform +-10% on numeric params) -> ``param_overrides``
        (clamped to schema bounds).

    Deterministic given the same inputs: all randomness comes from
    ``random.Random(seed)``, iterating ``param_schema["properties"]`` in its
    declared (insertion) order.

    This mirrors ``assetpipe.blender_scripts.common.resolve_params`` (the
    in-Blender implementation ``generate.py`` actually calls) exactly, so a
    caller here (tests, ``assetpipe generate --dry-run``-style tooling, or
    resuming a plan without spawning Blender) gets the identical resolved
    dict Blender would have produced for the same seed/schema/theme/overrides.
    It is intentionally re-implemented (not imported) because
    ``blender_scripts`` is owned by a different agent's concurrent work and
    this module must not depend on that package's internal layout changing
    out from under it.
    """
    props = param_schema.get("properties", {})
    params: dict = {name: spec["default"] for name, spec in props.items() if "default" in spec}

    theme = theme or {}
    for name, spec in props.items():
        if spec.get("type") not in _NUMERIC_TYPES or name not in params:
            continue
        range_key = f"{name}_range"
        if range_key in theme:
            lo, hi = theme[range_key]
            params[name] = min(max(params[name], lo), hi)

    rng = random.Random(seed)
    for name, spec in props.items():
        if spec.get("type") not in _NUMERIC_TYPES or name not in params:
            continue
        lo = spec.get("minimum", float("-inf"))
        hi = spec.get("maximum", float("inf"))
        jittered = params[name] * (1.0 + rng.uniform(-0.10, 0.10))
        jittered = min(max(jittered, lo), hi)
        params[name] = int(round(jittered)) if spec["type"] == "integer" else jittered

    for name, value in (param_overrides or {}).items():
        if name not in props:
            continue
        spec = props[name]
        if spec.get("type") in _NUMERIC_TYPES and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            lo = spec.get("minimum", float("-inf"))
            hi = spec.get("maximum", float("inf"))
            value = min(max(value, lo), hi)
            if spec["type"] == "integer":
                value = int(round(value))
        params[name] = value

    return params


def _finding_from_check(check_id: str, defect_type: str, check: dict, view_id: str) -> Finding:
    return Finding(check_id=check_id, defect_type=defect_type, severity=check["severity"],
                  verdict="fail", confidence=1.0, evidence_views=[view_id], location=view_id,
                  description=check.get("details", ""))


@dataclass
class SubprocessStages:
    """Implements ``loop.Stages`` for one asset by spawning Blender
    subprocesses and running the orchestrator-side checks in between."""

    request: dict
    run_dir: RunDir
    contracts: Contracts
    config: dict
    theme: dict = field(default_factory=dict)
    param_schema: dict = field(default_factory=dict)
    registry: object = None
    blender_bin: str = "blender"
    runner: Callable = subprocess.run
    vision_client: object = None
    llm_patch_fn: Callable | None = None
    history: HistoryLog | None = None

    def __post_init__(self) -> None:
        self.asset_id = self.request["asset_id"]
        # A1-A3 blocker findings from render(); if non-empty, inspect() must
        # return them without calling the vision API (see render()'s docstring).
        self._a_blockers: list[Finding] = []
        self._a_warns: list[Finding] = []

    # ---------- shared plumbing ----------

    def _timeout(self, stage: str) -> float:
        return self.config.get("stage_timeouts", {}).get(stage, _DEFAULT_TIMEOUTS[stage])

    def _log(self, event: str, iteration: int | None = None, **fields) -> None:
        if self.history is not None:
            self.history.event(event, self.asset_id, iter=iteration, **fields)

    def _profile(self) -> dict:
        return self.contracts.profile(self.request["platform_profile"])

    def _material_recipe(self) -> str | None:
        """Material recipe id for the bake payload. theme.json's ``materials``
        is a *list* of recipe ids legal for the theme (spec 7); generators may
        pick per-slot materials themselves, so the stage-level default is the
        list's first entry unless the request overrides it. Tiling requests
        need a TILING-capable recipe (spec 10.2), so for them the default is
        the theme's first recipe declaring ``TILING = True`` -- the list's
        first entry is usually a mesh material whose bake cannot be seamless."""
        override = self.request.get("material_recipe")
        if override:
            return override
        materials = self.theme.get("materials")
        if isinstance(materials, dict):  # tolerated legacy/test shape
            return materials.get(self.request["category"])
        if not (isinstance(materials, list) and materials):
            return None
        if self.request["category"] == "tiling_texture_set":
            tiling_id = self._first_tiling_material(materials)
            if tiling_id is not None:
                return tiling_id
        return materials[0]

    def _material_recipes(self, iter_dir: Path) -> list | None:
        """Per-slot material recipe list for multi-material assets
        (spec 10.2: "generators may pick per-slot materials"). The
        generator's resolved params (params.json, written by generate.py)
        carry a ``materials`` list matching the recipe's face
        ``material_index`` assignments; when present and non-empty it is the
        bake's slot list. Entries are either bare recipe id strings or
        slot-scoped ``{"recipe": id, "params": {...}}`` objects
        (docs/TEXTURE_WAVE.md item 6) -- normalized here so bake.py sees only
        those two shapes. None -> single ``_material_recipe()`` applies, and
        a malformed list falls back the same way rather than half-applying.

        Description-derived colors (docs/COLOR_WAVE.md item 1) are merged in
        here, as slot params, via ``matlib.color_words.derive_material_colors``
        -- pure and deterministic given (description, materials, palette,
        seed), so the generate() and apply_fix() bake payloads agree."""
        try:
            params = json.loads((iter_dir / "params.json").read_text())
        except (OSError, json.JSONDecodeError, FileNotFoundError):
            return None
        materials = params.get("materials")
        if not (isinstance(materials, list) and materials):
            return None
        normalized: list = []
        for entry in materials:
            if isinstance(entry, str) and entry:
                normalized.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("recipe"), str) \
                    and entry["recipe"]:
                normalized.append({"recipe": entry["recipe"],
                                   "params": dict(entry.get("params") or {})})
            else:
                return None
        return derive_material_colors(
            self.request.get("description", ""), normalized,
            self.theme.get("palette", {}) or {},
            seed=int(self.request.get("seed", 0)),
            request_overrides=self.request.get("material_overrides") or {})

    def _first_tiling_material(self, materials: list) -> str | None:
        from assetpipe.themes_io import ThemeIOError, load_material_recipe
        themes_root = self.config.get("themes_root") or \
            Path(__file__).resolve().parent.parent.parent / "themes"
        for material_id in materials:
            try:
                module = load_material_recipe(themes_root, self.request.get("theme"),
                                              material_id)
            except ThemeIOError:
                continue
            if getattr(module, "TILING", False):
                return material_id
        return None

    def _run_blender(self, script_name: str, iter_dir: Path, payload: dict, log_stem: str,
                     blend_path: Path | None = None) -> None:
        """Spawn one ``blender --background [<blend>] --python <script> --
        --args-json <path>`` subprocess; one retry on nonzero exit/timeout,
        then ``InfraError`` (spec 4.3)."""
        logs_dir = iter_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        args_path = logs_dir / f"{log_stem}.args.json"
        args_path.write_text(json.dumps(payload, indent=2, default=str))

        script_path = BLENDER_SCRIPTS_DIR / script_name
        cmd = [self.blender_bin]
        if blend_path is not None:
            cmd.append(str(blend_path))
        # --python-exit-code 1: without it Blender exits 0 even when the stage
        # script raises, and the failure surfaces later as a confusing
        # missing-artifact error in the *next* stage (verified against real
        # Blender 4.2).
        cmd += ["--background", "--python-exit-code", "1",
                "--python", str(script_path), "--", "--args-json", str(args_path)]

        timeout = self._timeout(log_stem)
        self._log("stage_start", iteration=payload.get("iteration"), stage=log_stem)

        failure = None
        for attempt in range(2):
            out_path = logs_dir / f"{log_stem}.out.txt"
            err_path = logs_dir / f"{log_stem}.err.txt"
            try:
                proc = self.runner(cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                out_path.write_text(exc.stdout or "" if isinstance(exc.stdout, str) else "")
                err_path.write_text(exc.stderr or "" if isinstance(exc.stderr, str) else "")
                failure = f"timeout after {timeout}s (attempt {attempt + 1})"
                continue
            out_path.write_text(proc.stdout or "")
            err_path.write_text(proc.stderr or "")
            if proc.returncode == 0:
                self._log("stage_end", iteration=payload.get("iteration"), stage=log_stem,
                          verdict="ok")
                return
            # Blender writes tracebacks to stderr but its own errors (missing
            # .blend, unreadable file) to stdout -- surface whichever has content.
            tail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            failure = f"exit {proc.returncode} (attempt {attempt + 1}): {tail[-500:]}"

        self._log("error", iteration=payload.get("iteration"), stage=log_stem, error=failure)
        raise InfraError(f"{script_name} failed after retry: {failure}")

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text()) if path.exists() else {}

    # ---------- G+M+X ----------

    def generate(self, iteration: int, seed: int) -> None:
        iter_dir = self.run_dir.iter_dir(self.asset_id, iteration)
        profile = self._profile()
        category = self.request["category"]
        request_for_stage = {**self.request, "seed": seed}

        self._run_blender("generate.py", iter_dir,
                          {"request": request_for_stage, "theme": self.theme, "profile": profile,
                           "generator": self.request.get("generator"), "out_dir": str(iter_dir),
                           "iteration": iteration}, "generate")
        gen_result = self._read_json(iter_dir / "result.json")
        root_object = gen_result.get("root_object")

        texture_budget = profile.get("textures", {}).get(category, {}).get("albedo", 1024)
        material_recipe = self._material_recipe()
        self._run_blender("bake.py", iter_dir,
                          {"object_name": root_object, "material_recipe": material_recipe,
                           "material_recipes": self._material_recipes(iter_dir),
                           "theme_id": self.request.get("theme"), "theme": self.theme,
                           "material_params": self.request.get("material_overrides", {}),
                           "palette": self.theme.get("palette", {}), "seed": seed,
                           "asset_dir": str(iter_dir), "out_dir": str(iter_dir),
                           "texture_resolution": texture_budget,
                           "texture_resolutions": profile.get("textures", {}).get(category, {}),
                           "tiling": category == "tiling_texture_set",
                           "iteration": iteration},
                          "bake", blend_path=iter_dir / "asset.blend")

        maps = {name: str(iter_dir / "maps" / f"{name}.png") for name in
                ("albedo", "normal", "orm", "emissive") if (iter_dir / "maps" / f"{name}.png").exists()}
        self._run_blender("export_gltf.py", iter_dir,
                          {"request": request_for_stage, "asset_dir": str(iter_dir), "maps": maps,
                           "profile": profile, "validation": self.config.get("validation", {}),
                           "root_object": root_object,
                           "lod_ratios": profile.get("lod_ratios", []), "iteration": iteration},
                          "export", blend_path=iter_dir / "asset.blend")

    # ---------- fix application ----------

    def apply_fix(self, iteration: int, fix_plan: dict) -> None:
        prev_dir = self.run_dir.iter_dir(self.asset_id, iteration - 1)
        iter_dir = self.run_dir.iter_dir(self.asset_id, iteration)
        resume = fix_plan["resume_stage"]
        resume_idx = stage_order(resume)

        shutil.copy2(prev_dir / "params.json", iter_dir / "params.json")
        if resume_idx > stage_order("G"):
            for name in ("asset.blend", "result.json"):
                # result.json is generate's record (root_object above all);
                # static_validate/render of THIS iteration read it, and
                # generate won't re-run to rewrite it (found when a resume-X
                # iteration crashed static checks with object_name=None on
                # real Blender).
                if (prev_dir / name).exists():
                    shutil.copy2(prev_dir / name, iter_dir / name)
        if resume_idx > stage_order("M"):
            if (prev_dir / "maps").exists():
                shutil.copytree(prev_dir / "maps", iter_dir / "maps", dirs_exist_ok=True)
            if (prev_dir / "bake_result.json").exists():
                shutil.copy2(prev_dir / "bake_result.json", iter_dir / "bake_result.json")
        prev_result = self._read_json(prev_dir / "result.json")
        root_object = prev_result.get("root_object")

        ctx = FixContext(iter_dir=iter_dir, request=self.request, contracts=self.contracts,
                         config=self.config, param_schema=self.param_schema,
                         llm_patch_fn=self.llm_patch_fn)

        # Pure-python MAP fixes operate on maps/ -- the artifacts a rebake
        # (re-run below when resume <= M) would immediately overwrite, and
        # which don't even exist yet in this iter dir unless resume is X.
        # Split them out and apply them right before export instead.
        def _is_map_fix(action: dict) -> bool:
            if action.get("type") != "table_fix":
                return False
            fix = self.contracts.fixes.get(action.get("fix_id"), {})
            return str(fix.get("implementation", "")).startswith("assetpipe.fixes.map_fixes.")

        map_actions = [a for a in fix_plan["actions"] if _is_map_fix(a)]
        pre_actions = [a for a in fix_plan["actions"] if not _is_map_fix(a)]
        if pre_actions:
            result = apply_fix_plan({**fix_plan, "actions": pre_actions}, ctx)
        else:
            result = ApplyResult()
        self._log("fix_applied", iteration=iteration, applied=len(result.applied),
                  failed=len(result.failed), params_changed=result.params_changed,
                  blender_actions=len(result.blender_actions),
                  map_fixes_deferred_to_pre_export=len(map_actions))

        if result.blender_actions and resume_idx <= stage_order("G"):
            # A resume at G regenerates the mesh and re-runs M+X, so blender-
            # side table fixes aimed at the previous iteration's state are
            # superseded -- and the .blend they would operate on was
            # deliberately not copied above. Running fixes.py here would open
            # a nonexistent file (verified against real Blender 4.2).
            self._log("fix_actions_skipped", iteration=iteration,
                      reason="resume==G regenerates; blender-side actions superseded",
                      skipped=[a.get("fix_id") for a in result.blender_actions])
        elif result.blender_actions:
            # fixes.py passes this payload straight to the fix functions as
            # their ctx, and the rebake-family fixes hand it to
            # bake.bake_all_maps -- so it must carry the full bake context,
            # not just the mesh-fix keys (verified against real Blender).
            texture_budget = (self._profile().get("textures", {})
                              .get(self.request["category"], {}).get("albedo", 1024))
            self._run_blender("fixes.py", iter_dir,
                              {"asset_id": self.asset_id, "iteration": iteration,
                               "actions": result.blender_actions, "asset_dir": str(iter_dir),
                               "request": self.request, "object_name": root_object,
                               "material_recipe": self._material_recipe(),
                               "material_recipes": self._material_recipes(iter_dir),
                               "theme_id": self.request.get("theme"), "theme": self.theme,
                               "material_params": self.request.get("material_overrides", {}),
                               "palette": self.theme.get("palette", {}),
                               "seed": self.request["seed"],
                               "texture_resolution": texture_budget,
                               "texture_resolutions": (self._profile().get("textures", {})
                                                       .get(self.request["category"], {})),
                               "tiling": self.request["category"] == "tiling_texture_set"},
                              "fixes", blend_path=iter_dir / "asset.blend")

        category = self.request["category"]
        profile = self._profile()
        request_for_stage = self.request
        if resume == "G":
            seed = self.request["seed"]
            self._run_blender("generate.py", iter_dir,
                              {"request": {**request_for_stage, "seed": seed}, "theme": self.theme,
                               "profile": profile, "generator": self.request.get("generator"),
                               "out_dir": str(iter_dir), "iteration": iteration}, "generate")
            gen_result = self._read_json(iter_dir / "result.json")
            root_object = gen_result.get("root_object")
        if resume in ("G", "M"):
            texture_budget = profile.get("textures", {}).get(category, {}).get("albedo", 1024)
            material_recipe = self._material_recipe()
            self._run_blender("bake.py", iter_dir,
                              {"object_name": root_object, "material_recipe": material_recipe,
                               "material_recipes": self._material_recipes(iter_dir),
                               "theme_id": self.request.get("theme"), "theme": self.theme,
                               "material_params": self.request.get("material_overrides", {}),
                               "palette": self.theme.get("palette", {}),
                               "seed": self.request["seed"], "asset_dir": str(iter_dir),
                               "out_dir": str(iter_dir), "texture_resolution": texture_budget,
                               "texture_resolutions": profile.get("textures", {}).get(category, {}),
                               "tiling": category == "tiling_texture_set", "iteration": iteration},
                              "bake", blend_path=iter_dir / "asset.blend")
        if map_actions:
            map_result = apply_fix_plan({**fix_plan, "actions": map_actions}, ctx)
            self._log("map_fixes_applied", iteration=iteration,
                      applied=len(map_result.applied), failed=len(map_result.failed))

        maps = {name: str(iter_dir / "maps" / f"{name}.png") for name in
                ("albedo", "normal", "orm", "emissive") if (iter_dir / "maps" / f"{name}.png").exists()}
        self._run_blender("export_gltf.py", iter_dir,
                          {"request": request_for_stage, "asset_dir": str(iter_dir), "maps": maps,
                           "profile": profile, "validation": self.config.get("validation", {}),
                           "root_object": root_object,
                           "lod_ratios": profile.get("lod_ratios", []), "iteration": iteration},
                          "export", blend_path=iter_dir / "asset.blend")

    # ---------- V1 ----------

    def _expected_inventory(self, iter_dir: Path) -> dict:
        export_result = self._read_json(iter_dir / "export_result.json")
        lods = export_result.get("lods", [])
        # Prefer the exporter's own record of what it wrote (root name carries
        # the collision suffix; result.json's root_object predates it).
        exported = export_result.get("exported_objects")
        if exported:
            return {"mesh_names": list(exported), "lod_names": lods}
        gen_result = self._read_json(iter_dir / "result.json")
        root_object = gen_result.get("root_object")
        if not lods and root_object is None:
            return {}
        mesh_names = ([root_object] if root_object else []) + list(lods)
        return {"mesh_names": mesh_names, "lod_names": lods} if mesh_names else {}

    def static_validate(self, iteration: int) -> StageResult:
        iter_dir = self.run_dir.iter_dir(self.asset_id, iteration)
        gen_result = self._read_json(iter_dir / "result.json")
        root_object = gen_result.get("root_object")

        self._run_blender("static_checks_mesh.py", iter_dir,
                          {"object_name": root_object, "validation": self.config.get("validation", {}),
                           "topology": self.request.get("topology", "closed"),
                           "bbox_range": self.request.get("bbox_range"),
                           "budget": self._profile().get("triangles", {}).get(self.request["category"]),
                           "is_kit": self.request["category"] == "modular_kit_piece",
                           "is_character": self.request["category"] in
                                          ("character_primary", "character_background"),
                           "expected_origin": (0.0, 0.0, 0.0),
                           "out_path": str(iter_dir / "mesh_report.json"),
                           "asset_id": self.asset_id, "iteration": iteration},
                          "static_checks", blend_path=iter_dir / "asset.blend")

        expected = self._expected_inventory(iter_dir)
        result, checks = run_static_gate(iter_dir, {**self.request, "_iteration": iteration},
                                         self.contracts, self.config, expected, runner=self.runner)
        self._log("stage_end", iteration=iteration, stage="V1",
                  verdict="pass" if result.passed else "fail",
                  blockers=[list(f.key()) for f in result.blockers])
        return result

    # ---------- R + pre-vision analytics ----------

    def _run_a_checks(self, renders_dir: Path) -> tuple[list[Finding], list[Finding]]:
        v = self.config.get("validation", {})
        blockers: list[Finding] = []
        warns: list[Finding] = []
        for path in sorted(renders_dir.glob("*.png")):
            if path.stem.startswith("contact_sheet"):
                continue
            arr = np.asarray(Image.open(path).convert("RGB")).astype(np.float64) / 255.0

            # lit_dark_* is dim BY DESIGN (spec 14.2 / the vision prompt's
            # "dark regions there are EXPECTED"); its mean sits well under
            # A1's default 1% floor on real renders, so only the std term
            # (rim-lit edge present) meaningfully gates that view.
            mean_lo = (v.get("a1_dark_view_mean_lo", 0.0005)
                       if path.stem.startswith("lit_dark_")
                       else v.get("a1_mean_lo", 0.01))
            r = check_not_empty(arr, min_std=v.get("a1_min_std", 0.0078), mean_lo=mean_lo)
            if r["verdict"] == "fail":
                blockers.append(_finding_from_check("A1", "RENDER_EMPTY", r, path.stem))

            if path.stem.startswith("normals_"):
                r = check_backface_fraction(arr, max_fraction=v.get("a2_max_backface_fraction", 0.001))
                if r["verdict"] == "fail":
                    blockers.append(_finding_from_check("A2", "INVERTED_NORMALS", r, path.stem))

            if path.stem.startswith("silhouette_"):
                lo, hi = v.get("a3_silhouette_range", [0.05, 0.85])
                r = check_silhouette_area(arr, lo, hi)
                if r["verdict"] == "fail":
                    blockers.append(_finding_from_check("A3", "SCALE_IMPLAUSIBLE", r, path.stem))

            if _is_l1_view(path.stem):
                r = check_clipping(arr, max_fraction=v.get("a4_max_clipped_fraction", 0.02))
                if r["verdict"] == "fail":
                    warns.append(_finding_from_check("A4", "CLIPPED_EMISSIVE", r, path.stem))
        return blockers, warns

    def render(self, iteration: int) -> None:
        """Runs R then the A1-A4 pre-vision analytics (spec 14.5). ``render()``
        has no return value per the Stages protocol, so any A1-A3 *blocker*
        failure is stashed on ``self`` and prepended to whatever ``inspect()``
        returns next -- cheap scripted catches must gate before a vision call
        is ever made (spec: "vision is the second net, not the only one"), so
        if any are present ``inspect()`` skips the API call entirely. A4 is
        warn-only and always rides along with whatever the final StageResult
        is, vision call or not.
        """
        iter_dir = self.run_dir.iter_dir(self.asset_id, iteration)
        glb_path = iter_dir / f"{self.asset_id}.glb"
        if not glb_path.exists():
            alt = iter_dir / "asset.glb"
            glb_path = alt if alt.exists() else glb_path
        self._run_blender("render_views.py", iter_dir,
                          {"request": self.request, "glb_path": str(glb_path),
                           "out_dir": str(iter_dir / "renders"),
                           "render_config": self.config.get("render", {}),
                           "iter_dir": str(iter_dir), "iteration": iteration}, "render")

        # Contact sheets (spec 14.3) are composed here, not inside Blender:
        # composition is Pillow-based and Blender's bundled Python has no
        # Pillow. The subprocess's result.json lists the rendered view ids.
        renders_dir = iter_dir / "renders"
        render_result = self._read_json(renders_dir / "result.json")
        view_ids = render_result.get("views", []) or \
            sorted(p.stem for p in renders_dir.glob("*.png")
                   if not p.stem.startswith("contact_sheet"))
        contact_sheets.compose_all(renders_dir, view_ids, renders_dir)

        self._a_blockers, self._a_warns = self._run_a_checks(renders_dir)

    # ---------- V2 ----------

    def _bbox_range(self) -> str:
        gen_id = self.request.get("generator")
        if gen_id and self.registry is not None and gen_id in self.registry:
            bbox = getattr(self.registry.get(gen_id), "BBOX_RANGE", None)
            if bbox:
                return str(bbox)
        return self.request.get("bbox_range") and str(self.request["bbox_range"]) or "unspecified"

    def inspect(self, iteration: int) -> StageResult:
        iter_dir = self.run_dir.iter_dir(self.asset_id, iteration)

        if self._a_blockers:
            blockers, warns = self._a_blockers, self._a_warns
            self._a_blockers, self._a_warns = [], []
            self._log("stage_end", iteration=iteration, stage="V2",
                      verdict="fail(pre-vision A-checks)",
                      blockers=[list(f.key()) for f in blockers])
            return StageResult(passed=False, blockers=blockers, warns=warns)

        renders_dir = iter_dir / "renders"
        contact_sheets = sorted(renders_dir.glob("contact_sheet_*.png"))
        log_path = iter_dir / "logs" / "vision_call.json"
        result = inspect_asset(self.vision_client, request=self.request, theme=self.theme,
                               bbox_range=self._bbox_range(), contact_sheets=contact_sheets,
                               renders_dir=renders_dir, iteration=iteration, contracts=self.contracts,
                               config=self.config, log_path=log_path)

        warns = self._a_warns + result.warns
        self._a_warns = []
        final = StageResult(passed=result.passed, blockers=result.blockers, warns=warns)
        (iter_dir / "vision_report.json").write_text(json.dumps({
            "asset_id": self.asset_id, "iteration": iteration,
            "blockers": [f.__dict__ for f in final.blockers],
            "warns": [f.__dict__ for f in final.warns],
        }, indent=2, default=str))
        self._log("stage_end", iteration=iteration, stage="V2",
                  verdict="pass" if final.passed else "fail",
                  blockers=[list(f.key()) for f in final.blockers])
        return final
