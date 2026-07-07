"""Stage R -- headless render harness (spec 14).

Imports the **exported ``.glb``** into a clean scene (never the authoring
``.blend`` -- spec 14.1: "this is how export-time texture/tangent bugs become
visible"), scenes the standard furniture (18% grey ground, labeled 1 m
reference cube), and renders the full spec 14.2 view set per asset category.

The view table, lighting-rig specs, and bbox-based camera-framing math live
in the bpy-free :mod:`assetpipe.blender_scripts.views` module (unit-tested
without Blender). Contact-sheet composition (spec 14.3) is Pillow-based and
Blender's bundled Python has no Pillow, so the *orchestrator* composes the
sheets from this script's per-view PNGs after the subprocess returns (see
``SubprocessStages.render`` / ``cli.cmd_render``).
"""
from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Vector

# Blender's bundled Python does not have this repo on sys.path when a stage
# script is launched via `blender --background --python <this file>`; bootstrap
# the repo root (two levels up) so `import assetpipe` works. Kept dependency-
# free (os, not pathlib) and inserted before the first assetpipe import.
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

from assetpipe.blender_scripts import common, views
from assetpipe.blender_scripts.generate import deterministic_scene_settings

_BLACKBODY_RGB_TABLE = {4500: (1.0, 0.79, 0.63)}  # see setup_lighting_rig() note


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path: Path) -> list["bpy.types.Object"]:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.data.objects if o not in before]


def setup_render_settings(scene, resolution: int = views.RESOLUTION_PX,
                          samples: int = views.CYCLES_SAMPLES) -> None:
    """Pins everything spec 14.1/14.4 requires for pixel-identical renders on
    the same ``.glb`` + harness config: CPU Cycles (from
    ``deterministic_scene_settings``), fixed sample count, OIDN denoise,
    AgX view transform, 1024x1024 PNG, opaque film."""
    deterministic_scene_settings(scene)
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = 'OPENIMAGEDENOISE'
    scene.view_settings.view_transform = 'AgX'
    scene.view_settings.look = 'None'
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.image_settings.file_format = 'PNG'
    scene.render.film_transparent = False


def add_ground_plane(grey: float = views.GROUND_GREY) -> "bpy.types.Object":
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    plane = bpy.context.active_object
    plane.name = "DBG_ground"
    mat = bpy.data.materials.new("DBG_ground_mat")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (grey, grey, grey, 1.0)
    plane.data.materials.append(mat)
    return plane


def add_reference_cube(offset_m: float = views.REFERENCE_CUBE_OFFSET_M) -> "bpy.types.Object":
    """A matte, mid-grey, 1 m cube ``offset_m`` to the asset's left -- the
    scale-plausibility check (vision R6) is judged against this (spec
    14.2)."""
    size = views.REFERENCE_CUBE_SIZE_M
    bpy.ops.mesh.primitive_cube_add(size=size, location=(-offset_m, 0, size / 2))
    cube = bpy.context.active_object
    cube.name = "DBG_reference_cube_1m"
    mat = bpy.data.materials.new("DBG_reference_cube_mat")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    cube.data.materials.append(mat)
    return cube


def setup_camera() -> "bpy.types.Object":
    cam_data = bpy.data.cameras.new("RenderCam")
    cam_obj = bpy.data.objects.new("RenderCam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    return cam_obj


def compute_bbox(objects: list["bpy.types.Object"]) -> tuple[Vector, Vector]:
    mins = Vector((float("inf"),) * 3)
    maxs = Vector((float("-inf"),) * 3)
    found = False
    for obj in objects:
        if obj.type != 'MESH':
            continue
        found = True
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            mins = Vector(min(mins[i], world[i]) for i in range(3))
            maxs = Vector(max(maxs[i], world[i]) for i in range(3))
    if not found:
        return Vector((-0.5, -0.5, -0.5)), Vector((0.5, 0.5, 0.5))
    return mins, maxs


def apply_view_camera(cam_obj, bbox_min: Vector, bbox_max: Vector, view: dict) -> None:
    fill = views.fill_fraction_for_view(view)
    fov = cam_obj.data.angle
    location, target = views.camera_transform(
        tuple(bbox_min), tuple(bbox_max), view["azimuth_deg"], view["elevation_deg"], fov, fill)
    cam_obj.location = Vector(location)
    look = Vector(target) - cam_obj.location
    cam_obj.rotation_euler = look.to_track_quat('-Z', 'Y').to_euler()


def _blackbody_rgb(kelvin: float) -> tuple[float, float, float]:
    """Approximate blackbody RGB for the fixed rig temperatures this
    pipeline uses (spec 14.2's "4500K via blackbody node" intent). A real
    ``ShaderNodeBlackbody`` only exists inside a material node tree; rather
    than build and bake a throwaway node tree per light, this pipeline uses a
    small fixed lookup table for the temperatures the rig spec actually
    needs -- documented deviation, see final report."""
    return _BLACKBODY_RGB_TABLE.get(int(kelvin), (1.0, 1.0, 1.0))


def setup_lighting_rig(rig_id: str) -> None:
    """Switch to lighting rig L1/L2/L3/none (spec 14.2)."""
    for light in [o for o in bpy.data.objects if o.type == 'LIGHT']:
        bpy.data.objects.remove(light, do_unlink=True)

    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    # Pin the dome color: a fresh world's Background node defaults to 0.05
    # grey, which under AgX leaves even the L1 "neutral studio" renders murky
    # (max pixel ~0.24 measured on real Blender 4.2). White dome; the rig's
    # Strength input is the single exposure knob.
    bg.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)

    if rig_id == "L1":
        bg.inputs["Strength"].default_value = 1.0
    elif rig_id == "L2":
        bg.inputs["Strength"].default_value = 0.1
        rig = views.LIGHTING_RIGS["L2"]
        sun_data = bpy.data.lights.new("WarmSun", type='SUN')
        sun = bpy.data.objects.new("WarmSun", sun_data)
        bpy.context.scene.collection.objects.link(sun)
        sun.rotation_euler = (math.radians(90 - rig["elevation_deg"]), 0, math.radians(45))
        sun_data.color = _blackbody_rgb(rig["color_temp_k"])
        sun_data.energy = 3.0
    elif rig_id == "L3":
        bg.inputs["Strength"].default_value = 0.0
        rim_data = bpy.data.lights.new("DimBlueRim", type='SUN')
        rim = bpy.data.objects.new("DimBlueRim", rim_data)
        bpy.context.scene.collection.objects.link(rim)
        rim.rotation_euler = (math.radians(60), 0, math.radians(200))
        rim_data.color = (0.4, 0.5, 1.0)
        rim_data.energy = 0.5
    else:  # "none" -- silhouette/debug-material passes
        bg.inputs["Strength"].default_value = 0.0


def _silhouette_material() -> "bpy.types.Material":
    mat = bpy.data.materials.new("DBG_silhouette")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    emit = nt.nodes.new('ShaderNodeEmission')
    emit.inputs['Color'].default_value = (1, 1, 1, 1)
    nt.links.new(emit.outputs['Emission'], out.inputs['Surface'])
    return mat


def _normals_material() -> "bpy.types.Material":
    """Debug override (spec 14.2): surface normal as RGB; ``Backfacing`` ->
    pure red emission.

    The normal is remapped ``0.5*n + 0.5`` before display (documented
    deviation from the spec's bare "normal->RGB"): with raw normals a
    legitimate +X-facing surface emits (1,0,0) -- byte-identical to the
    backface marker -- so A2's pure-red pixel count and vision R3 would
    false-positive on every +X face. After the remap, +X renders
    (1,0.5,0.5) and pure red can only mean backfacing (found by the first
    real vision-tier verification, 2026-07)."""
    mat = bpy.data.materials.new("DBG_normals")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    geo = nt.nodes.new('ShaderNodeNewGeometry')
    red = nt.nodes.new('ShaderNodeEmission')
    red.inputs['Color'].default_value = (1, 0, 0, 1)
    remap = nt.nodes.new('ShaderNodeVectorMath')
    remap.operation = 'MULTIPLY_ADD'
    remap.inputs[1].default_value = (0.5, 0.5, 0.5)
    remap.inputs[2].default_value = (0.5, 0.5, 0.5)
    nt.links.new(geo.outputs['Normal'], remap.inputs[0])
    nrm = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(remap.outputs['Vector'], nrm.inputs['Color'])
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(geo.outputs['Backfacing'], mix.inputs['Fac'])
    nt.links.new(nrm.outputs['Emission'], mix.inputs[1])
    nt.links.new(red.outputs['Emission'], mix.inputs[2])
    nt.links.new(mix.outputs['Shader'], out.inputs['Surface'])
    return mat


def _uvcheck_material() -> "bpy.types.Material":
    mat = bpy.data.materials.new("DBG_uvcheck")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    checker = nt.nodes.new('ShaderNodeTexChecker')
    checker.inputs['Scale'].default_value = 8.0
    nt.links.new(checker.outputs['Color'], bsdf.inputs['Base Color'])
    return mat


_DEBUG_MATERIAL_BUILDERS = {
    "silhouette": _silhouette_material,
    "normals": _normals_material,
    "uvcheck": _uvcheck_material,
}


def debug_material_override(kind: str | None) -> "bpy.types.Material | None":
    if kind is None:
        return None
    builder = _DEBUG_MATERIAL_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"unknown debug material {kind!r}")
    return builder()


def render_view(scene, cam_obj, bbox_min: Vector, bbox_max: Vector, view: dict, out_path: Path) -> None:
    apply_view_camera(cam_obj, bbox_min, bbox_max, view)
    setup_lighting_rig(view["rig"])

    override_kind = view.get("debug_material")
    scene.render.film_transparent = override_kind == "silhouette"
    if override_kind == "silhouette" and scene.world is not None:
        scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0

    mat = debug_material_override(override_kind)
    bpy.context.view_layer.material_override = mat
    scene.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)
    bpy.context.view_layer.material_override = None


def render_mesh_views(scene, category: str, out_dir: Path) -> list[str]:
    # The framing bbox is the ASSET's alone (spec 14.1: asset fills 55-75% of
    # frame height), so capture the object list BEFORE the scene furniture is
    # added -- framing over all objects lets the 20 m ground plane dominate
    # and the asset renders at ~0.1% of the frame (found by the first real
    # vision-tier verification, 2026-07).
    asset_objects = list(bpy.data.objects)
    # LOD siblings ride along in the export (spec 8.4) co-located with the
    # root mesh -- rendering them too makes the decimated LOD surface z-fight
    # and occlude the full-res mesh (found by the house vision run: black
    # walls and phantom shadows; the crate's "diagonal split face" was the
    # same collision). Hide them and keep them out of the framing bbox.
    lods = [o for o in asset_objects if "_LOD" in o.name]
    for lod in lods:
        lod.hide_render = True
    asset_objects = [o for o in asset_objects if o not in lods]
    ground = add_ground_plane()
    bbox_min, bbox_max = compute_bbox(asset_objects)
    # The 1 m reference cube stands 1.5 m to the asset's LEFT (spec 14.2) --
    # measured from the asset's bbox face, not from the origin, or any asset
    # wider than 3 m swallows the cube (the house did).
    ref_cube = add_reference_cube(
        offset_m=abs(float(bbox_min.x)) + views.REFERENCE_CUBE_OFFSET_M
        + views.REFERENCE_CUBE_SIZE_M / 2.0)
    cam_obj = setup_camera()
    ref_min, ref_max = compute_bbox([ref_cube])
    # R6 (scale plausibility) is judged against the reference cube in
    # turn_000/turn_090 (spec 14.2), so those two views widen the framing to
    # the asset+cube union -- otherwise a small asset framed at spec fill
    # leaves the cube out of frame entirely. Documented deviation from the
    # flat 55-75% fill for exactly these two views.
    union_min = Vector(min(bbox_min[i], ref_min[i]) for i in range(3))
    union_max = Vector(max(bbox_max[i], ref_max[i]) for i in range(3))

    rendered = []
    for view in views.view_set_for_category(category):
        wide = view["view_id"] in ("turn_000", "turn_090")
        fmin, fmax = (union_min, union_max) if wide else (bbox_min, bbox_max)
        # Silhouette views are "emission-white ASSET on black" (spec 14.2):
        # the furniture must not render or the plane fills the silhouette
        # and A3 measures the plane instead of the asset.
        hide_furniture = view.get("debug_material") == "silhouette"
        ground.hide_render = hide_furniture
        ref_cube.hide_render = hide_furniture
        render_view(scene, cam_obj, fmin, fmax, view, out_dir / f"{view['view_id']}.png")
        rendered.append(view["view_id"])
    ground.hide_render = False
    ref_cube.hide_render = False
    return rendered


def render_skybox_views(scene, out_dir: Path, exr_path: Path) -> list[str]:
    """Skyboxes have no mesh bbox to frame -- the camera stays fixed at the
    world origin and only rotates through the 6 axis views + poles (spec
    11, 14.2)."""
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    env_tex = world.node_tree.nodes.new('ShaderNodeTexEnvironment')
    env_tex.image = bpy.data.images.load(str(exr_path))
    world.node_tree.links.new(env_tex.outputs['Color'],
                              world.node_tree.nodes['Background'].inputs['Color'])

    cam_data = bpy.data.cameras.new("SkyCam")
    cam_data.lens_unit = 'FOV'
    cam_data.angle = math.radians(90)
    cam_obj = bpy.data.objects.new("SkyCam", cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    cam_obj.location = (0, 0, 0)

    rendered = []
    for view in views.SKYBOX_VIEW_SET:
        az, el = math.radians(view["azimuth_deg"]), math.radians(view["elevation_deg"])
        direction = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
        cam_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = str(out_dir / f"{view['view_id']}.png")
        bpy.ops.render.render(write_still=True)
        rendered.append(view["view_id"])
    return rendered


def render_tiling_views(scene, out_dir: Path, assign_material_fn) -> list[str]:
    """Tiling texture sets: 3x3-tiled plane + 1x1 flat view + a shaded sphere
    per lighting rig (spec 14.2). ``assign_material_fn(obj, tiles)`` wires the
    baked material at the requested tile repeat count."""
    bpy.ops.mesh.primitive_plane_add(size=3)
    plane = bpy.context.active_object
    plane.name = "TilingPlane"
    assign_material_fn(plane, 3)

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 0.5))
    sphere = bpy.context.active_object
    sphere.name = "TilingSphere"
    assign_material_fn(sphere, 1)

    cam_obj = setup_camera()
    rendered = []
    for view in views.TILING_VIEW_SET:
        target = sphere if view["kind"] == "sphere" else plane
        setup_lighting_rig(view["rig"])
        bbox_min, bbox_max = compute_bbox([target])
        apply_view_camera(cam_obj, bbox_min, bbox_max, view)
        scene.render.filepath = str(out_dir / f"{view['view_id']}.png")
        bpy.ops.render.render(write_still=True)
        rendered.append(view["view_id"])
    return rendered


def main() -> None:
    payload = common.parse_args()
    # The orchestrator's payload nests the request and names the glb
    # "glb_path"; standalone/manual invocations may pass flat keys.
    request = payload.get("request", {})
    glb_path = Path(payload.get("glb") or payload["glb_path"])
    out_dir = Path(payload["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    category = payload.get("category") or request.get("category", "prop_small")

    clear_scene()
    scene = bpy.context.scene
    setup_render_settings(scene)

    if category == "skybox":
        # skybox assets deliver an .exr instead of a .glb; tolerate either key
        exr = payload.get("exr") or payload.get("glb") or payload.get("glb_path")
        rendered = render_skybox_views(scene, out_dir, Path(exr))
    else:
        import_glb(glb_path)
        if category == "tiling_texture_set":
            def _assign(obj, tiles):
                # material re-application for tiling previews is a Stage-R
                # concern handled by the caller-supplied callback; the glb
                # import above already brought in the baked material, so the
                # default path just reuses whatever material index 0 is.
                if obj.data.materials:
                    return
                if bpy.data.materials:
                    obj.data.materials.append(list(bpy.data.materials)[0])

            rendered = render_tiling_views(scene, out_dir, _assign)
        else:
            rendered = render_mesh_views(scene, category, out_dir)

    common.write_result(out_dir / "result.json", {
        "stage": "R",
        "views": rendered,
    })


if __name__ == "__main__":
    main()
