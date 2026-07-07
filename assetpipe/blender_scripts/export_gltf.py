"""Stage X -- glTF 2.0 export (spec 12).

Runs inside Blender on the baked ``asset.blend``. In order: generate LODs
(spec 8.4), re-validating each against the mesh checks (decimation is the
classic source of degenerate triangles); apply collision name suffixes (spec
19.2); re-wire baked maps to a clean Principled BSDF (spec 12.2, the
procedural node graph itself is never exported); export the canonical
uncompressed ``.glb`` with the exact normative parameter set (spec 12.1).
"""
from __future__ import annotations

from pathlib import Path

import bpy

# Blender's bundled Python does not have this repo on sys.path when a stage
# script is launched via `blender --background --python <this file>`; bootstrap
# the repo root (two levels up) so `import assetpipe` works. Kept dependency-
# free (os, not pathlib) and inserted before the first assetpipe import.
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

from assetpipe.blender_scripts import common, static_checks_mesh

COLLISION_SUFFIX = {"convex": "-convcol", "static": "-col", "none": ""}
COLLISION_DEFAULT_BY_CATEGORY = {
    "prop_small": "convex", "prop_hero": "convex",
    "modular_kit_piece": "static", "environment_piece": "static",
    "character_primary": "none", "character_background": "none",
}


# ---------------------------------------------------------------------------
# Material re-assignment (spec 12.2)
# ---------------------------------------------------------------------------

def build_export_material(name: str, maps: dict[str, str]) -> "bpy.types.Material":
    """Re-assign baked maps to a CLEAN Principled BSDF (spec 12.2): the
    procedural node graph used for baking is never exported -- only the
    baked PNGs cross the glTF boundary."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

    if "albedo" in maps:
        img = bpy.data.images.load(maps["albedo"], check_existing=True)
        img.colorspace_settings.name = 'sRGB'
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])

    orm_tex = None
    if "orm" in maps:
        img = bpy.data.images.load(maps["orm"], check_existing=True)
        img.colorspace_settings.name = 'Non-Color'
        orm_tex = nt.nodes.new('ShaderNodeTexImage')
        orm_tex.image = img
        sep = nt.nodes.new('ShaderNodeSeparateColor')
        nt.links.new(orm_tex.outputs['Color'], sep.inputs['Color'])
        nt.links.new(sep.outputs['Green'], bsdf.inputs['Roughness'])
        nt.links.new(sep.outputs['Blue'], bsdf.inputs['Metallic'])
        # Occlusion (R channel): Principled BSDF has no native AO input: the
        # Blender glTF I/O addon reads occlusion from a dedicated
        # "glTF Material output" node group's "Occlusion" socket (its
        # documented convention for round-tripping occlusionTexture, spec
        # 12.2). Reuse an existing group if this .blend already has one.
        gltf_settings = _ensure_gltf_settings_group()
        settings_node = nt.nodes.new('ShaderNodeGroup')
        settings_node.node_tree = gltf_settings
        settings_node.name = settings_node.label = "glTF Settings"
        nt.links.new(sep.outputs['Red'], settings_node.inputs['Occlusion'])

    if "normal" in maps:
        img = bpy.data.images.load(maps["normal"], check_existing=True)
        img.colorspace_settings.name = 'Non-Color'
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        nmap = nt.nodes.new('ShaderNodeNormalMap')
        nt.links.new(tex.outputs['Color'], nmap.inputs['Color'])
        nt.links.new(nmap.outputs['Normal'], bsdf.inputs['Normal'])

    if "emissive" in maps:
        img = bpy.data.images.load(maps["emissive"], check_existing=True)
        img.colorspace_settings.name = 'sRGB'
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Emission Color'])
        bsdf.inputs['Emission Strength'].default_value = 1.0

    return mat


def _ensure_gltf_settings_group():
    name = "glTF Material output"
    nt = bpy.data.node_groups.get(name)
    if nt is not None:
        return nt
    nt = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    nt.interface.new_socket("Occlusion", in_out='INPUT', socket_type='NodeSocketFloat')
    nt.nodes.new('NodeGroupInput')
    nt.nodes.new('NodeGroupOutput')
    return nt


def assign_export_material(obj: "bpy.types.Object", maps: dict[str, str]) -> None:
    """Replace ALL slots with the single baked-maps material. Multi-material
    assets (per-slot bake materials, spec 10.2) are already fully captured in
    the shared atlas maps; leaving extra slots would export one glTF material
    per slot, each re-pointing at the same textures. Face material_index
    values are reset to slot 0 explicitly rather than trusting Blender's
    out-of-range clamping."""
    mat = build_export_material(f"{obj.name}_export", maps)
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    if len(obj.data.polygons):
        import numpy as np
        obj.data.polygons.foreach_set(
            "material_index", np.zeros(len(obj.data.polygons), dtype=np.int32))
        obj.data.update()


# ---------------------------------------------------------------------------
# LOD generation (spec 8.4)
# ---------------------------------------------------------------------------

def generate_lods(obj: "bpy.types.Object", ratios: list[float], asset_id: str,
                   budget: dict, thresholds: dict, topology: str = "closed") -> list["bpy.types.Object"]:
    """Duplicate + decimate per profile ratio, name ``<asset_id>_LOD{n}``,
    then re-run the mesh validity checks on each LOD (spec 8.4/13.1 --
    decimation is the classic source of degenerate triangles). Raises if a
    LOD fails a blocker check; siblings live in the same ``EXPORT``
    collection (spec 8.4's names-not-extensions rationale)."""
    export_coll = bpy.data.collections["EXPORT"]
    lods = []
    for i, ratio in enumerate(ratios, start=1):
        lod_obj = obj.copy()
        lod_obj.data = obj.data.copy()
        lod_obj.name = f"{asset_id}_LOD{i}"
        export_coll.objects.link(lod_obj)

        # Planar decimate first: on boxy/architectural assets it reaches the
        # ratio by merging coplanar faces without ever collapsing thin parts
        # (window frames, mullions) into non-manifold geometry -- collapse
        # runs after, only for whatever reduction is still missing.
        import math as _math

        lod_obj.data.calc_loop_triangles()
        orig_tris = len(lod_obj.data.loop_triangles)
        planar = lod_obj.modifiers.new(f"PlanarLOD{i}", 'DECIMATE')
        planar.decimate_type = 'DISSOLVE'
        planar.angle_limit = _math.radians(8.0)
        deps = bpy.context.evaluated_depsgraph_get()
        lod_obj.data = bpy.data.meshes.new_from_object(lod_obj.evaluated_get(deps))
        lod_obj.modifiers.clear()
        lod_obj.data.calc_loop_triangles()
        if len(lod_obj.data.loop_triangles) > ratio * orig_tris:
            mod = lod_obj.modifiers.new(f"DecimateLOD{i}", 'DECIMATE')
            mod.ratio = ratio * orig_tris / len(lod_obj.data.loop_triangles)
            mod.use_collapse_triangulate = True
            deps = bpy.context.evaluated_depsgraph_get()
            lod_obj.data = bpy.data.meshes.new_from_object(lod_obj.evaluated_get(deps))
            lod_obj.modifiers.clear()

        # Decimation is the top producer of degenerate slivers and
        # non-manifold edges (blender-procedural-geometry skill) -- run the
        # standard cleanup pass on the LOD before judging it, and fill any
        # hole the sliver-dissolve opened in a closed mesh. Collapsing many
        # thin parts (window frames, mullions) made LOD1 fail S1 without
        # this (house, phase 2).
        import bmesh
        lbm = bmesh.new()
        lbm.from_mesh(lod_obj.data)
        bmesh.ops.remove_doubles(lbm, verts=lbm.verts, dist=1e-4)
        bmesh.ops.dissolve_degenerate(lbm, edges=lbm.edges, dist=1e-5)
        if topology == "closed":
            boundary = [e for e in lbm.edges if e.is_boundary]
            if boundary:
                bmesh.ops.holes_fill(lbm, edges=boundary, sides=0)
        bmesh.ops.recalc_face_normals(lbm, faces=lbm.faces)
        bmesh.ops.triangulate(lbm, faces=lbm.faces, quad_method="BEAUTY",
                              ngon_method="BEAUTY")
        lbm.to_mesh(lod_obj.data)
        lbm.free()
        lod_obj.data.update()

        results = static_checks_mesh.run_all_checks(
            lod_obj, thresholds, topology=topology, budget=budget, lod_ratio=ratio)
        failures = [r for r in results if r["verdict"] == "fail" and r["severity"] == "blocker"]
        if failures:
            raise RuntimeError(f"{lod_obj.name} failed mesh checks after decimation: {failures}")
        lods.append(lod_obj)
    return lods


# ---------------------------------------------------------------------------
# Collision suffixes (spec 19.2)
# ---------------------------------------------------------------------------

def collision_mode(request: dict) -> str:
    for tag in request.get("tags", []):
        if tag.startswith("collision:"):
            return tag.split(":", 1)[1]
    return COLLISION_DEFAULT_BY_CATEGORY.get(request.get("category"), "none")


def apply_collision_suffix(obj: "bpy.types.Object", mode: str) -> None:
    suffix = COLLISION_SUFFIX.get(mode, "")
    if suffix and not obj.name.endswith(suffix):
        obj.name = f"{obj.name}{suffix}"


# ---------------------------------------------------------------------------
# Exporter invocation (spec 12.1, normative parameter set -- verbatim)
# ---------------------------------------------------------------------------

def export(ctx: dict, out_glb: Path) -> Path:
    """Run the canonical exporter invocation. ``use_selection=False`` because
    the scene contains only the ``EXPORT`` collection (spec 9.4/12.1)."""
    bpy.ops.export_scene.gltf(
        filepath=str(out_glb),
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_texcoords=True,
        export_normals=True,
        export_tangents=True,
        export_materials='EXPORT',
        export_image_format='AUTO',
        export_cameras=False,
        export_lights=False,
        export_animations=False,
        export_skins=True,
        export_draco_mesh_compression_enable=False,
    )
    return out_glb


def main() -> None:
    payload = common.parse_args()
    request = payload["request"]
    asset_dir = Path(payload["asset_dir"])
    maps = payload["maps"]  # {"albedo": "/path/albedo.png", ...}
    profile = payload.get("profile", {})
    thresholds = payload.get("validation", {})
    category = request["category"]

    root = bpy.data.objects[payload["root_object"]]
    assign_export_material(root, maps)

    ratios = payload.get("lod_ratios", profile.get("lod_ratios", []))
    budget = profile.get("triangles", {}).get(category, {})
    lods = []
    if ratios and request.get("lods", "auto") != "none":
        lods = generate_lods(root, ratios, request["asset_id"], budget, thresholds,
                             topology=request.get("topology", "closed"))
        for lod in lods:
            assign_export_material(lod, maps)

    mode = collision_mode(request)
    for obj in [root, *lods]:
        apply_collision_suffix(obj, mode)
        # glTF names meshes after the mesh datablock; sync it to the (possibly
        # suffixed) object name so the glb's mesh inventory matches what this
        # result records (S20c compares the two).
        obj.data.name = obj.name

    out_glb = asset_dir / f"{request['asset_id']}.glb"
    export({"request": request}, out_glb)

    common.write_result(asset_dir / "export_result.json", {
        "stage": "X", "glb": str(out_glb),
        "lods": [o.name for o in lods],
        "collision_mode": mode,
        # The exact object/mesh names that went into the glb (root first) --
        # the orchestrator's expected-inventory source of truth. root.name
        # differs from result.json's root_object once the collision suffix
        # is applied, so the orchestrator must not reconstruct this itself.
        "exported_objects": [root.name, *(o.name for o in lods)],
    })


if __name__ == "__main__":
    main()
