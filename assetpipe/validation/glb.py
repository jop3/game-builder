"""GLB structural checks without external dependencies (spec 13.5 S20b-S20d).

Parses the GLB container directly (12-byte header + chunks; chunk 0 is the glTF
JSON) — no Node toolchain needed for inventory checks. Khronos glTF-Validator
(S20a) remains a separate subprocess; these checks complement, not replace, it.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

GLB_MAGIC = 0x46546C67          # 'glTF'
CHUNK_JSON = 0x4E4F534A         # 'JSON'
CHUNK_BIN = 0x004E4942          # 'BIN\0'

CANONICAL_EXTENSION_WHITELIST = frozenset({"KHR_materials_emissive_strength"})


class GlbParseError(Exception):
    pass


def parse_glb(path: Path) -> dict:
    """Return the glTF JSON dict from a .glb file. Raises GlbParseError on any
    container-level malformation (which is itself a validation failure)."""
    data = Path(path).read_bytes()
    if len(data) < 20:
        raise GlbParseError("file too small to be a GLB")
    magic, version, total_len = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise GlbParseError("bad magic: not a GLB container")
    if version != 2:
        raise GlbParseError(f"unsupported GLB version {version}")
    if total_len != len(data):
        raise GlbParseError(f"header length {total_len} != file size {len(data)}")
    clen, ctype = struct.unpack_from("<II", data, 12)
    if ctype != CHUNK_JSON:
        raise GlbParseError("first chunk is not JSON")
    if 20 + clen > len(data):
        raise GlbParseError("JSON chunk overruns file")
    return json.loads(data[20:20 + clen])


def _result(check_id: str, passed: bool, measured, threshold, details: str = "") -> dict:
    return {"check_id": check_id, "verdict": "pass" if passed else "fail",
            "severity": "blocker", "measured": measured, "threshold": threshold,
            "details": details}


def check_extension_whitelist(gltf: dict,
                              whitelist: frozenset = CANONICAL_EXTENSION_WHITELIST) -> dict:
    """S20b: only whitelisted extensions in the canonical file (Godot has no
    Draco/KTX2 support — compression belongs to adapters)."""
    used = set(gltf.get("extensionsUsed", []))
    illegal = sorted(used - whitelist)
    return _result("S20b", not illegal, illegal, sorted(whitelist),
                   details=f"extensionsUsed={sorted(used)}")


def materials_with_normal_texture(gltf: dict) -> set[int]:
    return {i for i, m in enumerate(gltf.get("materials", []))
            if "normalTexture" in m}


def check_tangents_present(gltf: dict) -> dict:
    """S20c (partial): every primitive whose material has a normal map must
    export TANGENT — engines regenerating tangents differently causes seams."""
    need = materials_with_normal_texture(gltf)
    missing = []
    for mi, mesh in enumerate(gltf.get("meshes", [])):
        for pi, prim in enumerate(mesh.get("primitives", [])):
            if prim.get("material") in need and "TANGENT" not in prim.get("attributes", {}):
                missing.append(f"mesh{mi}/prim{pi}")
    return _result("S20c_tangents", not missing, missing, [],
                   details="primitives with normal-mapped material lacking TANGENT")


def check_inventory(gltf: dict, expected: dict) -> dict:
    """S20c: counts/names vs params.json expectations. `expected` keys (all
    optional): mesh_names (exact set), material_count, image_count, lod_names
    (must be a subset of mesh names)."""
    problems = []
    names = {m.get("name", "") for m in gltf.get("meshes", [])}
    if "mesh_names" in expected and names != set(expected["mesh_names"]):
        problems.append(f"mesh names {sorted(names)} != expected {sorted(expected['mesh_names'])}")
    if "material_count" in expected and len(gltf.get("materials", [])) != expected["material_count"]:
        problems.append(f"material count {len(gltf.get('materials', []))} != {expected['material_count']}")
    if "image_count" in expected and len(gltf.get("images", [])) != expected["image_count"]:
        problems.append(f"image count {len(gltf.get('images', []))} != {expected['image_count']}")
    if "lod_names" in expected:
        missing = set(expected["lod_names"]) - names
        if missing:
            problems.append(f"missing LOD meshes: {sorted(missing)}")
    return _result("S20c", not problems, problems, [], details="; ".join(problems))


def check_file_size(path: Path, max_bytes: int) -> dict:
    size = Path(path).stat().st_size
    return _result("S20d", size <= max_bytes, size, max_bytes)


def run_glb_checks(path: Path, expected: dict, max_bytes: int,
                   whitelist: frozenset = CANONICAL_EXTENSION_WHITELIST) -> list[dict]:
    """All structural checks for one canonical .glb. A container parse failure
    is reported as a failed S20a-container check rather than an exception, so
    the fix loop can route it (GLTF_INVALID -> reexport)."""
    try:
        gltf = parse_glb(path)
    except GlbParseError as exc:
        return [_result("S20a_container", False, str(exc), "well-formed GLB")]
    return [
        check_extension_whitelist(gltf, whitelist),
        check_tangents_present(gltf),
        check_inventory(gltf, expected),
        check_file_size(path, max_bytes),
    ]
