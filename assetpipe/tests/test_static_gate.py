"""Orchestrator-side V1 static gate: S14-S20 checks, defect mapping, and
static_report.json shape (spec 13.3-13.6)."""
import json
import struct

import numpy as np
import pytest
from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.validation import glb as glb_mod
from assetpipe.validation.static_gate import run_static_gate

C = Contracts.load()

REQUEST = {
    "asset_id": "crate_01", "category": "prop_small", "theme": "scifi_industrial",
    "platform_profile": "web", "seed": 1, "description": "a small crate",
}


def make_glb(gltf: dict, bin_chunk: bytes = b"") -> bytes:
    payload = json.dumps(gltf).encode()
    payload += b" " * (-len(payload) % 4)
    chunks = struct.pack("<II", len(payload), glb_mod.CHUNK_JSON) + payload
    if bin_chunk:
        bin_chunk += b"\x00" * (-len(bin_chunk) % 4)
        chunks += struct.pack("<II", len(bin_chunk), glb_mod.CHUNK_BIN) + bin_chunk
    header = struct.pack("<III", glb_mod.GLB_MAGIC, 2, 12 + len(chunks))
    return header + chunks


GOOD_GLTF = {
    "asset": {"version": "2.0"},
    "extensionsUsed": [],
    "materials": [{"name": "m", "normalTexture": {"index": 0}}],
    "meshes": [{"name": "crate_01", "primitives": [
        {"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2, "TANGENT": 3},
         "material": 0}]}],
    "images": [{"name": "albedo"}, {"name": "normal"}, {"name": "orm"}],
}


def write_glb(iter_dir, gltf=None, bin_chunk=b"\x00" * 32, asset_id="crate_01"):
    data = make_glb(gltf if gltf is not None else GOOD_GLTF, bin_chunk)
    (iter_dir / f"{asset_id}.glb").write_bytes(data)


def write_noisy_png(path, base, size=64, noise=20):
    rng = np.random.default_rng(0)
    arr = np.clip(base + rng.integers(-noise, noise, size=(size, size, 3)), 0, 255).astype("uint8")
    Image.fromarray(arr, mode="RGB").save(path)


def write_flat_png(path, color, size=64):
    arr = np.full((size, size, 3), color, dtype="uint8")
    Image.fromarray(arr, mode="RGB").save(path)


def write_maps(iter_dir, albedo_color=(120, 120, 120), noisy_albedo=True):
    maps_dir = iter_dir / "maps"
    maps_dir.mkdir(exist_ok=True)
    if noisy_albedo:
        write_noisy_png(maps_dir / "albedo.png", albedo_color)
    else:
        write_flat_png(maps_dir / "albedo.png", albedo_color)
    write_flat_png(maps_dir / "normal.png", (128, 128, 255))
    write_flat_png(maps_dir / "orm.png", (255, 128, 0))


def missing_binary_runner(cmd, **kwargs):
    raise FileNotFoundError(cmd[0])


def make_mesh_report(iter_dir, checks=None):
    if checks is None:
        checks = [{"check_id": "S1", "verdict": "pass", "severity": "blocker",
                  "measured": 0, "threshold": 0, "details": ""}]
    (iter_dir / "mesh_report.json").write_text(json.dumps(checks))


# ---------- happy path ----------

def test_all_pass_when_maps_glb_and_mesh_report_are_good(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)

    assert result.passed, [c for c in checks if c["verdict"] == "fail"]
    report = json.loads((iter_dir / "static_report.json").read_text())
    assert report["stage"] == "V1" and report["verdict"] == "pass"
    assert report["asset_id"] == "crate_01"
    assert "checks" in report and "timings_s" in report
    assert any(c["check_id"] == "S20a" and c["verdict"] == "skip" for c in checks)


def test_static_report_written_even_on_failure(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir)
    make_mesh_report(iter_dir, checks=[
        {"check_id": "S1", "verdict": "fail", "severity": "blocker", "measured": 2,
         "threshold": 0, "details": "2 non-manifold edges", "defect": "NON_MANIFOLD"}])

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    assert not result.passed
    assert [f.defect_type for f in result.blockers] == ["NON_MANIFOLD"]
    report = json.loads((iter_dir / "static_report.json").read_text())
    assert report["verdict"] == "fail"


def test_mesh_report_entry_missing_defect_falls_back_to_check_id_map(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir)
    make_mesh_report(iter_dir, checks=[
        {"check_id": "S4", "verdict": "fail", "severity": "blocker", "measured": 1,
         "threshold": 0, "details": "loose vertex"}])  # no "defect" key

    result, _ = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                runner=missing_binary_runner)
    assert [f.defect_type for f in result.blockers] == ["LOOSE_GEOMETRY"]


# ---------- S16 black albedo ----------

def test_black_albedo_fails_s16_black_surface(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir, albedo_color=(0, 0, 0), noisy_albedo=False)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    assert not result.passed
    s16 = next(c for c in checks if c["check_id"] == "S16")
    assert s16["verdict"] == "fail"
    assert any(f.check_id == "S16" and f.defect_type == "BLACK_SURFACE" for f in result.blockers)


def test_flat_color_declared_skips_variance_requirement(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir, albedo_color=(120, 120, 120), noisy_albedo=False)  # zero variance
    write_glb(iter_dir)
    make_mesh_report(iter_dir)
    request = {**REQUEST, "material_overrides": {"flat_color": True}}

    result, checks = run_static_gate(iter_dir, request, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    s16 = next(c for c in checks if c["check_id"] == "S16")
    assert s16["verdict"] == "pass"


# ---------- S14 resolution ----------

def test_non_power_of_two_texture_fails_s14(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_flat_png(iter_dir / "maps" / "albedo.png", (120, 120, 120), size=100)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    s14 = [c for c in checks if c["check_id"] == "S14"]
    assert any(c["verdict"] == "fail" for c in s14)
    assert any(f.defect_type == "TEX_RESOLUTION_INVALID" for f in result.blockers)


# ---------- S19 tiling (presence-driven by category) ----------

def test_tiling_checks_only_run_for_tiling_texture_set(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir, noisy_albedo=False)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    _, checks_non_tiling = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                           runner=missing_binary_runner)
    assert not any(c["check_id"] in ("S19a", "S19b") for c in checks_non_tiling)

    tiling_request = {**REQUEST, "category": "tiling_texture_set"}
    _, checks_tiling = run_static_gate(iter_dir, tiling_request, C, {"validation": {}}, expected={},
                                       runner=missing_binary_runner)
    assert any(c["check_id"] == "S19a" for c in checks_tiling)
    assert any(c["check_id"] == "S19b" for c in checks_tiling)
    # uniform flat texture tiles trivially (zero gradient everywhere)
    assert all(c["verdict"] == "pass" for c in checks_tiling if c["check_id"] in ("S19a", "S19b"))


def test_loop_x_layer_flag_runs_only_x_axis_s19a(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir, noisy_albedo=False)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)
    request = {**REQUEST, "material_overrides": {"layers": [{"loop_x": True}]}}

    _, checks = run_static_gate(iter_dir, request, C, {"validation": {}}, expected={},
                                runner=missing_binary_runner)
    s19a = [c for c in checks if c["check_id"] == "S19a"]
    # one axis per present map (albedo/normal/orm = 3), not two
    assert len(s19a) == 3
    assert not any(c["check_id"] == "S19b" for c in checks)


# ---------- S20a gltf_validator ----------

def test_gltf_validator_missing_binary_recorded_as_skip_not_fail(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    s20a = next(c for c in checks if c["check_id"] == "S20a")
    assert s20a["verdict"] == "skip"
    assert result.passed  # a skip never gates


def test_gltf_validator_runs_and_reports_errors(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"issues": {"numErrors": 1, "numWarnings": 0}})
        stderr = ""

    def fake_runner(cmd, **kwargs):
        return FakeProc()

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=fake_runner)
    s20a = next(c for c in checks if c["check_id"] == "S20a")
    assert s20a["verdict"] == "fail"
    assert not result.passed
    assert any(f.check_id == "S20a" and f.defect_type == "GLTF_INVALID" for f in result.blockers)


def test_gltf_validator_clean_report_passes(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"issues": {"numErrors": 0, "numWarnings": 0}})
        stderr = ""

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=lambda cmd, **kw: FakeProc())
    s20a = next(c for c in checks if c["check_id"] == "S20a")
    assert s20a["verdict"] == "pass"
    assert result.passed


# ---------- S20b-d via glb.py reuse ----------

def test_draco_extension_fails_s20b_extension_forbidden(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    bad = dict(GOOD_GLTF, extensionsUsed=["KHR_draco_mesh_compression"])
    write_glb(iter_dir, gltf=bad)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    s20b = next(c for c in checks if c["check_id"] == "S20b")
    assert s20b["verdict"] == "fail"
    assert any(f.defect_type == "GLTF_EXTENSION_FORBIDDEN" for f in result.blockers)


def test_file_too_large_fails_s20d(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    write_glb(iter_dir, bin_chunk=b"\x00" * (5 * 1024 * 1024))
    make_mesh_report(iter_dir)
    request = {**REQUEST, "budget_overrides": {"max_file_bytes": 1024}}

    result, checks = run_static_gate(iter_dir, request, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    s20d = next(c for c in checks if c["check_id"] == "S20d")
    assert s20d["verdict"] == "fail"
    assert any(f.defect_type == "FILE_TOO_LARGE" for f in result.blockers)


def test_missing_glb_reports_container_failure(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    assert not result.passed
    assert any(c["check_id"] == "S20a_container" for c in checks)


# ---------- presence-driven maps ----------

def test_missing_emissive_map_is_not_penalized(tmp_path):
    iter_dir = tmp_path / "iter_01"
    iter_dir.mkdir()
    write_maps(iter_dir)  # no emissive.png written
    write_glb(iter_dir)
    make_mesh_report(iter_dir)

    result, checks = run_static_gate(iter_dir, REQUEST, C, {"validation": {}}, expected={},
                                     runner=missing_binary_runner)
    assert result.passed
    assert not any("emissive" in str(c.get("measured", "")) for c in checks if c["check_id"] == "S14")
