"""Godot 4.3+ engine adapter (spec §19; patterns from
`.claude/skills/godot-asset-import/SKILL.md`).

Delivers canonical `final/` artifacts into a Godot project using the layout in
§19.1, installs the bundled post-import + verification scripts (§19.3, §19.4),
and wires the project-level `[importer_defaults]` entry so per-asset behavior
is data-driven (sibling manifest + glTF name-suffix conventions) rather than
hand-authored `.import` sidecars, whose keys drift across Godot versions.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from assetpipe.adapters.base import AdapterReport, DeliveryRecord

_PACKAGE_DIR = Path(__file__).parent
POST_IMPORT_SCRIPT_PATH = _PACKAGE_DIR / "post_import.gd"
VERIFY_IMPORT_SCRIPT_PATH = _PACKAGE_DIR / "verify_import.gd"

RES_PIPELINE_DIR = "res://assets/generated/_pipeline"
POST_IMPORT_RES_PATH = f"{RES_PIPELINE_DIR}/post_import.gd"
VERIFY_IMPORT_RES_PATH = f"{RES_PIPELINE_DIR}/verify_import.gd"

# spec §19.4: "Timeouts (600s) and a single retry ... for the --import step."
IMPORT_TIMEOUT_S = 600
VERIFY_TIMEOUT_S = 600
IMPORT_MAX_ATTEMPTS = 2  # first try + one retry


class GodotAdapter:
    """EngineAdapter (assetpipe/adapters/base.py) for Godot 4.3+ (spec §19)."""

    name = "godot"

    def __init__(
        self,
        project_path: Path = Path("."),
        godot_bin: str = "godot",
        use_pipeline_lods: bool = False,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        # `project_path` defaults to "." so `get_adapter("godot")` (no config)
        # is always constructible; real callers pass the configured
        # `delivery.godot.project_path` (config/defaults.yaml).
        self.project_path = Path(project_path)
        self.godot_bin = godot_bin
        self.use_pipeline_lods = use_pipeline_lods
        self._run = run

    # ================= deliver() : spec §19.1-§19.3 =================

    def deliver(self, asset_dir: Path, manifest: dict, target_root: Path) -> DeliveryRecord:
        asset_dir = Path(asset_dir)
        target_root = Path(target_root)
        final_dir = asset_dir / "final"

        asset_id = manifest["asset_id"]
        category = manifest["category"]
        theme = manifest.get("theme", "")
        container = manifest.get("container", "glb")

        dest_dir = self._dest_dir(target_root, category, theme, asset_id)
        # Idempotent delivery: clear any previous delivery for this asset
        # before writing so re-delivery never leaves stale files behind.
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if category == "skybox":
            delivered = self._deliver_skybox(final_dir, manifest, dest_dir, target_root)
        elif category == "background_2d":
            delivered = self._deliver_background(final_dir, manifest, dest_dir)
        else:
            delivered = self._deliver_mesh(final_dir, manifest, dest_dir, asset_id)

        self._install_pipeline_scripts(target_root)
        self._ensure_project_config(target_root / "project.godot")

        return DeliveryRecord(
            asset_id=asset_id,
            category=category,
            theme=theme,
            container=container,
            delivered_paths=delivered,
            target_root=target_root,
            manifest=manifest,
            asset_dir=asset_dir,
        )

    @staticmethod
    def _dest_dir(target_root: Path, category: str, theme: str, asset_id: str) -> Path:
        """Spec §19.1 placement rules."""
        if category == "skybox":
            return target_root / "assets" / "generated" / "skies" / asset_id
        if category == "background_2d":
            return target_root / "assets" / "generated" / "backgrounds" / asset_id
        return target_root / "assets" / "generated" / theme / category / asset_id

    def _deliver_mesh(self, final_dir: Path, manifest: dict, dest_dir: Path,
                       asset_id: str) -> list[Path]:
        asset_file = manifest.get("files", {}).get("asset", "asset.glb")
        glb_dst = dest_dir / f"{asset_id}.glb"
        shutil.copy2(final_dir / asset_file, glb_dst)
        manifest_dst = dest_dir / f"{asset_id}.manifest.json"
        manifest_dst.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        return [glb_dst, manifest_dst]

    def _deliver_skybox(self, final_dir: Path, manifest: dict, dest_dir: Path,
                         target_root: Path) -> list[Path]:
        asset_id = manifest["asset_id"]
        asset_file = manifest.get("files", {}).get("asset", "skybox.exr")
        exr_dst = dest_dir / f"{asset_id}.exr"
        shutil.copy2(final_dir / asset_file, exr_dst)
        manifest_dst = dest_dir / f"{asset_id}.manifest.json"
        manifest_dst.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        # spec §19.5: PanoramaSkyMaterial resource generated by the adapter,
        # not hand-authored — a stable, diff-friendly text resource.
        tres_dst = dest_dir / f"{asset_id}.tres"
        exr_res_path = self._res_path(exr_dst, target_root)
        tres_dst.write_text(_panorama_tres(exr_res_path))
        return [exr_dst, manifest_dst, tres_dst]

    def _deliver_background(self, final_dir: Path, manifest: dict, dest_dir: Path) -> list[Path]:
        asset_id = manifest["asset_id"]
        delivered: list[Path] = []
        layer_files = sorted(final_dir.glob("layer_*.png")) if final_dir.exists() else []
        for layer_src in layer_files:
            layer_dst = dest_dir / layer_src.name
            shutil.copy2(layer_src, layer_dst)
            delivered.append(layer_dst)

        background_json_src = final_dir / "background.json"
        background_json_dst = dest_dir / "background.json"
        if background_json_src.exists():
            shutil.copy2(background_json_src, background_json_dst)
        else:
            # Fall back to layer/viewport data carried directly on the
            # manifest (spec §11.3 shape) when no standalone file exists yet.
            payload = {
                "layers": manifest.get("layers", []),
                "viewport_design_size": manifest.get("viewport_design_size", [1920, 1080]),
            }
            background_json_dst.write_text(json.dumps(payload, indent=2))
        delivered.append(background_json_dst)

        manifest_dst = dest_dir / f"{asset_id}.manifest.json"
        manifest_dst.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        delivered.append(manifest_dst)
        return delivered

    def _install_pipeline_scripts(self, target_root: Path) -> None:
        pipeline_dir = target_root / "assets" / "generated" / "_pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_dir / "post_import.gd").write_text(POST_IMPORT_SCRIPT_PATH.read_text())
        (pipeline_dir / "verify_import.gd").write_text(VERIFY_IMPORT_SCRIPT_PATH.read_text())

    def _ensure_project_config(self, project_godot_path: Path) -> None:
        """Idempotently ensure `project.godot` wires the post-import script
        (§19.3) and the `assetpipe/use_pipeline_lods` project setting the
        script reads (see post_import.gd's docstring for that design choice).
        Conservative edit: create file/section/key only if missing, otherwise
        leave the rest of the file untouched.
        """
        text = project_godot_path.read_text() if project_godot_path.exists() else ""
        text = _ensure_importer_defaults(text, POST_IMPORT_RES_PATH)
        text = _ensure_setting(
            text, "assetpipe", "use_pipeline_lods",
            "true" if self.use_pipeline_lods else "false")
        project_godot_path.write_text(text)

    # ================= verify() : spec §19.4 =================

    def verify(self, record: DeliveryRecord) -> AdapterReport:
        errors: list[str] = []
        details: dict = {}

        import_ok, import_details = self._run_import(record)
        details["import"] = import_details
        errors.extend(import_details["relevant_errors"])

        if record.category == "background_2d":
            # spec §19.4 defines a PackedScene/skybox verify contract; there is
            # no analogous headless check for a set of parallax layer PNGs +
            # background.json, so this branch is a structural presence check
            # only (no Godot invocation).
            script_ok, script_details = self._verify_background(record)
        else:
            script_ok, script_details = self._run_verify_script(record)
        details["verify"] = script_details
        errors.extend(script_details.get("errors", []))

        passed = import_ok and script_ok
        report_payload = {"passed": passed, "errors": errors, "details": details}
        self._write_report(record, report_payload)
        return AdapterReport(passed=passed, errors=errors, details=details)

    def _invoke(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        try:
            return self._run(cmd, cwd=str(self.project_path), capture_output=True,
                              text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                cmd, returncode=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + "\nERROR: godot invocation timed out")

    def _run_import(self, record: DeliveryRecord) -> tuple[bool, dict]:
        cmd = [self.godot_bin, "--headless", "--path", str(self.project_path), "--import"]
        attempts = 0
        result = None
        for attempts in range(1, IMPORT_MAX_ATTEMPTS + 1):
            result = self._invoke(cmd, timeout=IMPORT_TIMEOUT_S)
            if result.returncode == 0:
                break
        stderr = (result.stderr or "") if result is not None else ""
        error_lines = [ln for ln in stderr.splitlines() if "ERROR:" in ln]
        needles = [p.name for p in record.delivered_paths] + [record.asset_id]
        relevant_errors = [ln for ln in error_lines if any(n in ln for n in needles)]
        ok = (result is not None and result.returncode == 0) and not relevant_errors
        return ok, {
            "attempts": attempts,
            "returncode": result.returncode if result is not None else None,
            "all_stderr_errors": error_lines,
            "relevant_errors": relevant_errors,
        }

    def _run_verify_script(self, record: DeliveryRecord) -> tuple[bool, dict]:
        res_path = self._verify_target_res_path(record)
        cmd = [self.godot_bin, "--headless", "--path", str(self.project_path),
               "--script", VERIFY_IMPORT_RES_PATH, "--", res_path]
        result = self._invoke(cmd, timeout=VERIFY_TIMEOUT_S)
        report = _parse_last_json_line(result.stdout or "")
        errors: list[str] = []
        if report is None:
            errors.append(
                f"verify_import.gd produced no parseable JSON report "
                f"(returncode={result.returncode}); stdout={result.stdout!r} "
                f"stderr={result.stderr!r}")
            return False, {"report": None, "returncode": result.returncode, "errors": errors}
        ok = bool(report.get("pass", False)) and result.returncode == 0
        if not ok:
            errors.append(f"verify_import.gd reported failure: {report}")
        return ok, {"report": report, "returncode": result.returncode, "errors": errors}

    def _verify_background(self, record: DeliveryRecord) -> tuple[bool, dict]:
        missing = [str(p) for p in record.delivered_paths if not p.exists()]
        ok = not missing
        errors = [f"missing delivered file: {m}" for m in missing]
        return ok, {"report": {"pass": ok, "checked": [str(p) for p in record.delivered_paths]},
                    "errors": errors}

    def _verify_target_res_path(self, record: DeliveryRecord) -> str:
        if record.category == "skybox":
            candidates = [p for p in record.delivered_paths if p.suffix == ".tres"]
        else:
            candidates = [p for p in record.delivered_paths if p.suffix == ".glb"]
        path = candidates[0] if candidates else record.delivered_paths[0]
        return self._res_path(path, record.target_root)

    def _write_report(self, record: DeliveryRecord, payload: dict) -> None:
        if record.asset_dir is None:
            return
        final_dir = record.asset_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        (final_dir / "godot_report.json").write_text(json.dumps(payload, indent=2))

    @staticmethod
    def _res_path(dst: Path, root: Path) -> str:
        return "res://" + str(dst.relative_to(root)).replace("\\", "/")


# ================= file/text helpers (module-level, no Godot state) =================


def _panorama_tres(exr_res_path: str) -> str:
    """Spec §19.5: PanoramaSkyMaterial `.tres` pointing at the delivered EXR."""
    return (
        '[gd_resource type="PanoramaSkyMaterial" load_steps=2 format=3]\n\n'
        f'[ext_resource type="Texture2D" path="{exr_res_path}" id="1"]\n\n'
        '[resource]\n'
        'panorama = ExtResource("1")\n'
    )


def _ensure_importer_defaults(text: str, script_res_path: str) -> str:
    """Idempotently ensure `[importer_defaults]` contains a `scene` dict with
    `"nodes/import_script/path": "<script_res_path>"` (spec §19.3), creating
    the section/key only if missing and never touching anything else the
    project already had in that section.
    """
    desired_entry = f'"nodes/import_script/path": "{script_res_path}"'
    header_re = re.compile(r'^\[importer_defaults\]\s*$', re.MULTILINE)
    m = header_re.search(text)
    if m is None:
        addition = f'[importer_defaults]\n\nscene={{\n{desired_entry}\n}}\n'
        if text.strip():
            return text.rstrip("\n") + "\n\n" + addition
        return addition

    start = m.end()
    next_header = re.search(r'^\[', text[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(text)
    body = text[start:end]

    if desired_entry in body:
        return text  # already wired, nothing to do (idempotent no-op)

    scene_re = re.search(r'scene\s*=\s*\{', body)
    if scene_re is None:
        new_body = f'\n\nscene={{\n{desired_entry}\n}}\n' + body.lstrip("\n")
        return text[:start] + new_body + text[end:]

    # A `scene` dict already exists but lacks our key: insert it right after
    # the opening brace, leaving every other key untouched.
    insert_at = start + scene_re.end()
    return text[:insert_at] + f'\n{desired_entry},' + text[insert_at:]


def _ensure_setting(text: str, section: str, key: str, value: str) -> str:
    """Idempotently ensure a plain `key=value` line exists under `[section]`
    in a project.godot-style text, creating the section if missing and
    updating the key in place if its value differs.
    """
    header_re = re.compile(rf'^\[{re.escape(section)}\]\s*$', re.MULTILINE)
    m = header_re.search(text)
    line = f"{key}={value}"
    if m is None:
        addition = f'[{section}]\n\n{line}\n'
        if text.strip():
            return text.rstrip("\n") + "\n\n" + addition
        return addition

    start = m.end()
    next_header = re.search(r'^\[', text[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(text)
    body = text[start:end]

    key_re = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
    km = key_re.search(body)
    if km:
        if body[km.start():km.end()] == line:
            return text  # already correct
        new_body = body[:km.start()] + line + body[km.end():]
        return text[:start] + new_body + text[end:]

    new_body = (body.rstrip("\n") + f"\n{line}\n") if body.strip() else f"\n{line}\n"
    return text[:start] + new_body + text[end:]


def _parse_last_json_line(stdout: str) -> dict | None:
    """Tolerant of interleaved Godot log noise on stdout: take the last line
    that parses as JSON (spec §19.4 / skill: "one JSON line on stdout is the
    report contract")."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None
