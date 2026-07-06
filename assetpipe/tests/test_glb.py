"""GLB parser + structural checks against synthetic in-memory GLBs (spec 13.5)."""
import json
import struct

import pytest

from assetpipe.validation import glb


def make_glb(gltf: dict, bin_chunk: bytes = b"") -> bytes:
    payload = json.dumps(gltf).encode()
    payload += b" " * (-len(payload) % 4)                 # 4-byte alignment
    chunks = struct.pack("<II", len(payload), glb.CHUNK_JSON) + payload
    if bin_chunk:
        bin_chunk += b"\x00" * (-len(bin_chunk) % 4)
        chunks += struct.pack("<II", len(bin_chunk), glb.CHUNK_BIN) + bin_chunk
    header = struct.pack("<III", glb.GLB_MAGIC, 2, 12 + len(chunks))
    return header + chunks


GOOD = {
    "asset": {"version": "2.0"},
    "extensionsUsed": ["KHR_materials_emissive_strength"],
    "materials": [{"name": "m", "normalTexture": {"index": 0}}],
    "meshes": [
        {"name": "crate", "primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2, "TANGENT": 3},
             "material": 0}]},
        {"name": "crate_LOD1", "primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2, "TANGENT": 3},
             "material": 0}]},
    ],
    "images": [{"name": "albedo"}, {"name": "normal"}, {"name": "orm"}],
}


@pytest.fixture
def good_path(tmp_path):
    p = tmp_path / "good.glb"
    p.write_bytes(make_glb(GOOD, b"\x00" * 64))
    return p


def test_parse_roundtrip(good_path):
    assert glb.parse_glb(good_path)["meshes"][0]["name"] == "crate"


def test_parse_rejects_malformed(tmp_path):
    for name, data in {
        "short.glb": b"xx",
        "magic.glb": b"NOPE" + b"\x00" * 32,
        "truncated.glb": make_glb(GOOD)[:-8],
    }.items():
        p = tmp_path / name
        p.write_bytes(data)
        with pytest.raises(glb.GlbParseError):
            glb.parse_glb(p)


def test_full_check_run_passes_good_asset(good_path):
    results = glb.run_glb_checks(
        good_path,
        expected={"mesh_names": ["crate", "crate_LOD1"], "material_count": 1,
                  "image_count": 3, "lod_names": ["crate_LOD1"]},
        max_bytes=2 * 1024 * 1024)
    assert all(r["verdict"] == "pass" for r in results), results


def test_draco_extension_rejected(tmp_path):
    bad = dict(GOOD, extensionsUsed=["KHR_draco_mesh_compression"])
    p = tmp_path / "draco.glb"; p.write_bytes(make_glb(bad))
    r = glb.check_extension_whitelist(glb.parse_glb(p))
    assert r["verdict"] == "fail" and "KHR_draco_mesh_compression" in r["measured"]


def test_missing_tangents_flagged_only_when_normal_mapped(tmp_path):
    bad = json.loads(json.dumps(GOOD))
    del bad["meshes"][0]["primitives"][0]["attributes"]["TANGENT"]
    p = tmp_path / "notan.glb"; p.write_bytes(make_glb(bad))
    assert glb.check_tangents_present(glb.parse_glb(p))["verdict"] == "fail"
    # without a normal map, tangents are optional
    nonormal = json.loads(json.dumps(bad))
    nonormal["materials"][0].pop("normalTexture")
    p2 = tmp_path / "nonormal.glb"; p2.write_bytes(make_glb(nonormal))
    assert glb.check_tangents_present(glb.parse_glb(p2))["verdict"] == "pass"


def test_inventory_mismatch_and_size_cap(good_path):
    r = glb.check_inventory(glb.parse_glb(good_path),
                            {"mesh_names": ["crate"], "image_count": 5})
    assert r["verdict"] == "fail" and "image count" in r["details"]
    assert glb.check_file_size(good_path, max_bytes=10)["verdict"] == "fail"


def test_container_failure_reported_not_raised(tmp_path):
    p = tmp_path / "junk.glb"; p.write_bytes(b"garbage")
    results = glb.run_glb_checks(p, expected={}, max_bytes=1024)
    assert results[0]["check_id"] == "S20a_container"
    assert results[0]["verdict"] == "fail"
