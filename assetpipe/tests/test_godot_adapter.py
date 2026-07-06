"""Godot adapter tests (spec 18-19) against a fake `godot` executable — no real
Godot install needed. The fake is a small python script written into
`tmp_path`, made executable, that:
  - appends its argv to a log file (FAKE_GODOT_LOG) so tests can assert on
    invocation counts;
  - on `--import` exits with $FAKE_GODOT_IMPORT_EXIT (default 0) and, if
    $FAKE_GODOT_IMPORT_STDERR is set, prints it to stderr (simulating the
    "ERROR: ..." lines the adapter scans for);
  - on `--script ... verify_import.gd` prints a line of harmless log noise
    then $FAKE_GODOT_VERIFY_STDOUT (a JSON report line) and exits with
    $FAKE_GODOT_VERIFY_EXIT.
"""
from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from assetpipe.adapters import get_adapter
from assetpipe.adapters.base import AdapterReport, DeliveryRecord
from assetpipe.adapters.godot.adapter import GodotAdapter, POST_IMPORT_RES_PATH

FAKE_GODOT_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys

    log_path = os.environ.get("FAKE_GODOT_LOG")
    if log_path:
        with open(log_path, "a") as f:
            f.write(json.dumps(sys.argv[1:]) + "\\n")

    argv = sys.argv[1:]
    if "--import" in argv:
        stderr_msg = os.environ.get("FAKE_GODOT_IMPORT_STDERR", "")
        if stderr_msg:
            print(stderr_msg, file=sys.stderr)
        sys.exit(int(os.environ.get("FAKE_GODOT_IMPORT_EXIT", "0")))
    elif "--script" in argv:
        print("Godot Engine v4.3.stable.official - log noise")
        print(os.environ.get("FAKE_GODOT_VERIFY_STDOUT",
                              '{"asset": "x", "checks": [], "pass": true}'))
        sys.exit(int(os.environ.get("FAKE_GODOT_VERIFY_EXIT", "0")))
    sys.exit(0)
    """
)


@pytest.fixture
def fake_godot(tmp_path):
    script = tmp_path / "fake_godot.py"
    script.write_text(FAKE_GODOT_SRC)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log_path = tmp_path / "godot_invocations.log"
    os.environ["FAKE_GODOT_LOG"] = str(log_path)
    for key in ("FAKE_GODOT_IMPORT_EXIT", "FAKE_GODOT_IMPORT_STDERR",
                "FAKE_GODOT_VERIFY_STDOUT", "FAKE_GODOT_VERIFY_EXIT"):
        os.environ.pop(key, None)
    yield str(script), log_path
    for key in ("FAKE_GODOT_LOG", "FAKE_GODOT_IMPORT_EXIT", "FAKE_GODOT_IMPORT_STDERR",
                "FAKE_GODOT_VERIFY_STDOUT", "FAKE_GODOT_VERIFY_EXIT"):
        os.environ.pop(key, None)


def invocation_count(log_path: Path, marker: str) -> int:
    if not log_path.exists():
        return 0
    count = 0
    for line in log_path.read_text().splitlines():
        argv = json.loads(line)
        if marker in argv:
            count += 1
    return count


def make_mesh_asset(tmp_path, asset_id="crate_small_01", category="prop_small",
                     theme="scifi_industrial"):
    asset_dir = tmp_path / "run" / asset_id
    final_dir = asset_dir / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "asset.glb").write_bytes(b"glTF-fake-bytes")
    manifest = {
        "asset_id": asset_id, "status": "validated", "category": category,
        "theme": theme, "platform_profile": "web", "seed": 1,
        "iterations_used": 1, "container": "glb",
        "files": {"asset": "asset.glb", "previews": ["preview_turn_045.png"]},
        "stats": {"triangles": 100, "textures": {"albedo": "1024"}},
        "collision": "convex", "tags": ["layer:3"],
        "checks_passed": [], "remaining_defects": [],
    }
    (final_dir / "manifest.json").write_text(json.dumps(manifest))
    return asset_dir, manifest


def make_skybox_asset(tmp_path, asset_id="nebula_sky_01"):
    asset_dir = tmp_path / "run" / asset_id
    final_dir = asset_dir / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "skybox.exr").write_bytes(b"exr-fake-bytes")
    manifest = {
        "asset_id": asset_id, "status": "validated", "category": "skybox",
        "theme": "scifi_industrial", "platform_profile": "web", "seed": 2,
        "iterations_used": 1, "container": "exr",
        "files": {"asset": "skybox.exr", "previews": ["preview.png"]},
    }
    return asset_dir, manifest


# ---------------------------------------------------------------- registry --

def test_get_adapter_godot_works():
    adapter = get_adapter("godot")
    assert adapter.name == "godot"
    assert isinstance(adapter, GodotAdapter)


def test_get_adapter_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("unreal")


# ----------------------------------------------------------------- deliver --

def test_deliver_places_mesh_files_at_spec_paths(tmp_path):
    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project)

    record = adapter.deliver(asset_dir, manifest, project)

    expected_dir = project / "assets" / "generated" / "scifi_industrial" / "prop_small" / "crate_small_01"
    assert (expected_dir / "crate_small_01.glb").read_bytes() == b"glTF-fake-bytes"
    manifest_copy = json.loads((expected_dir / "crate_small_01.manifest.json").read_text())
    assert manifest_copy == manifest
    assert record.asset_id == "crate_small_01"
    assert record.category == "prop_small"
    assert record.asset_dir == asset_dir
    assert set(record.delivered_paths) == {
        expected_dir / "crate_small_01.glb", expected_dir / "crate_small_01.manifest.json"}


def test_deliver_installs_pipeline_scripts(tmp_path):
    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project)

    adapter.deliver(asset_dir, manifest, project)

    pipeline_dir = project / "assets" / "generated" / "_pipeline"
    post_import = (pipeline_dir / "post_import.gd").read_text()
    verify_import = (pipeline_dir / "verify_import.gd").read_text()
    assert "EditorScenePostImport" in post_import
    assert "MeshInstance3D" in verify_import


def test_deliver_writes_project_godot_importer_defaults(tmp_path):
    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project)

    adapter.deliver(asset_dir, manifest, project)

    text = (project / "project.godot").read_text()
    assert "[importer_defaults]" in text
    assert POST_IMPORT_RES_PATH in text
    assert "[assetpipe]" in text
    assert "use_pipeline_lods=false" in text


def test_deliver_is_idempotent_no_duplicated_sections(tmp_path):
    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    # Pre-seed a project.godot with unrelated content that must survive.
    (project / "project.godot").write_text(
        '[application]\n\nconfig/name="Demo"\n')
    adapter = GodotAdapter(project_path=project)

    adapter.deliver(asset_dir, manifest, project)
    first_text = (project / "project.godot").read_text()
    first_tree = sorted(str(p.relative_to(project)) for p in project.rglob("*") if p.is_file())

    adapter.deliver(asset_dir, manifest, project)
    second_text = (project / "project.godot").read_text()
    second_tree = sorted(str(p.relative_to(project)) for p in project.rglob("*") if p.is_file())

    assert first_text == second_text
    assert first_text.count("[importer_defaults]") == 1
    assert first_text.count("[assetpipe]") == 1
    assert 'config/name="Demo"' in first_text
    assert first_tree == second_tree


def test_deliver_skybox_layout(tmp_path):
    asset_dir, manifest = make_skybox_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project)

    record = adapter.deliver(asset_dir, manifest, project)

    expected_dir = project / "assets" / "generated" / "skies" / "nebula_sky_01"
    assert (expected_dir / "nebula_sky_01.exr").read_bytes() == b"exr-fake-bytes"
    tres_text = (expected_dir / "nebula_sky_01.tres").read_text()
    assert "PanoramaSkyMaterial" in tres_text
    assert "nebula_sky_01.exr" in tres_text
    assert record.category == "skybox"
    assert record.container == "exr"


# ------------------------------------------------------------------ verify --

def test_verify_success_parses_report_and_writes_godot_report(tmp_path, fake_godot):
    fake_bin, log_path = fake_godot
    os.environ["FAKE_GODOT_VERIFY_STDOUT"] = json.dumps(
        {"asset": "x", "checks": [{"id": "has_mesh_instance", "ok": True}], "pass": True})

    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project, godot_bin=fake_bin)
    record = adapter.deliver(asset_dir, manifest, project)

    report = adapter.verify(record)

    assert isinstance(report, AdapterReport)
    assert report.passed is True
    assert report.errors == []
    godot_report_path = asset_dir / "final" / "godot_report.json"
    assert godot_report_path.exists()
    payload = json.loads(godot_report_path.read_text())
    assert payload["passed"] is True
    assert payload["details"]["verify"]["report"]["pass"] is True


def test_verify_fails_when_verify_script_reports_fail(tmp_path, fake_godot):
    fake_bin, log_path = fake_godot
    os.environ["FAKE_GODOT_VERIFY_STDOUT"] = json.dumps(
        {"asset": "x", "checks": [{"id": "has_mesh_instance", "ok": False}], "pass": False})
    os.environ["FAKE_GODOT_VERIFY_EXIT"] = "1"

    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project, godot_bin=fake_bin)
    record = adapter.deliver(asset_dir, manifest, project)

    report = adapter.verify(record)

    assert report.passed is False
    assert report.errors
    payload = json.loads((asset_dir / "final" / "godot_report.json").read_text())
    assert payload["passed"] is False


def test_verify_fails_when_import_stderr_mentions_delivered_path(tmp_path, fake_godot):
    fake_bin, log_path = fake_godot
    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project, godot_bin=fake_bin)
    record = adapter.deliver(asset_dir, manifest, project)

    os.environ["FAKE_GODOT_IMPORT_EXIT"] = "1"
    os.environ["FAKE_GODOT_IMPORT_STDERR"] = "ERROR: Failed to import crate_small_01.glb: bad mesh"

    report = adapter.verify(record)

    assert report.passed is False
    assert any("crate_small_01" in e for e in report.errors)


def test_verify_retries_import_exactly_once_on_nonzero_exit(tmp_path, fake_godot):
    fake_bin, log_path = fake_godot
    os.environ["FAKE_GODOT_IMPORT_EXIT"] = "1"

    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project, godot_bin=fake_bin)
    record = adapter.deliver(asset_dir, manifest, project)

    adapter.verify(record)

    assert invocation_count(log_path, "--import") == 2


def test_verify_does_not_retry_on_success(tmp_path, fake_godot):
    fake_bin, log_path = fake_godot

    asset_dir, manifest = make_mesh_asset(tmp_path)
    project = tmp_path / "godot_project"
    project.mkdir()
    adapter = GodotAdapter(project_path=project, godot_bin=fake_bin)
    record = adapter.deliver(asset_dir, manifest, project)

    adapter.verify(record)

    assert invocation_count(log_path, "--import") == 1


# --------------------------------------------------------------- gd scripts --

def test_gd_scripts_contain_key_class_markers():
    post_import = (Path(__file__).parent.parent / "adapters" / "godot" / "post_import.gd").read_text()
    verify_import = (Path(__file__).parent.parent / "adapters" / "godot" / "verify_import.gd").read_text()

    assert "@tool" in post_import
    assert "extends EditorScenePostImport" in post_import
    assert "GeometryInstance3D" in post_import

    assert "extends SceneTree" in verify_import
    assert "MeshInstance3D" in verify_import
    assert "PanoramaSkyMaterial" in verify_import
    assert "quit(" in verify_import
