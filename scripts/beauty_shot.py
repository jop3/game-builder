"""Presentation render: the reference-style dark beauty shot (roadmap phase 5).

Standalone, OUT of the validation render set on purpose -- no rubric check or
A-check ever judges it, so it can break every harness convention (black
world, no ground plane, no reference cube, warm key light, emissives
dominant).

    blender --background --python scripts/beauty_shot.py -- \
        --glb runs/<id>/<asset>/final/asset.glb --out beauty.png \
        [--azimuth 215] [--elevation 14] [--resolution 1536]
"""
import argparse
import math
import sys

import bpy
from mathutils import Vector

_REPO = __file__.rsplit("/", 2)[0]
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from assetpipe.blender_scripts import views
from assetpipe.blender_scripts.render_views import (apply_view_camera, compute_bbox,
                                                    setup_camera, setup_render_settings)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--azimuth", type=float, default=215.0)
    ap.add_argument("--elevation", type=float, default=14.0)
    ap.add_argument("--resolution", type=int, default=1536)
    args = ap.parse_args(argv)

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    bpy.ops.import_scene.gltf(filepath=args.glb)
    for obj in list(bpy.data.objects):
        if "_LOD" in obj.name:
            obj.hide_render = True

    scene = bpy.context.scene
    setup_render_settings(scene, resolution=args.resolution, samples=192)

    # black world with a whisper of ambient so unlit faces keep silhouette
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.01, 0.011, 0.014, 1.0)
    bg.inputs["Strength"].default_value = 0.35

    # warm key from high front-left, cool dim rim from behind
    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", 'SUN'))
    key.data.energy = 1.6
    key.data.color = (1.0, 0.82, 0.6)
    key.rotation_euler = (math.radians(55), 0.0, math.radians(args.azimuth + 35))
    scene.collection.objects.link(key)
    rim = bpy.data.objects.new("Rim", bpy.data.lights.new("Rim", 'SUN'))
    rim.data.energy = 0.5
    rim.data.color = (0.5, 0.62, 0.9)
    rim.rotation_euler = (math.radians(60), 0.0, math.radians(args.azimuth + 200))
    scene.collection.objects.link(rim)

    cam = setup_camera()
    bbox_min, bbox_max = compute_bbox([o for o in bpy.data.objects
                                       if o.type == 'MESH' and not o.hide_render])
    view = {"view_id": "beauty_dark", "azimuth_deg": args.azimuth,
            "elevation_deg": args.elevation, "rig": "none", "kind": "beauty"}
    apply_view_camera(cam, Vector(bbox_min), Vector(bbox_max), view)

    scene.render.filepath = args.out
    bpy.ops.render.render(write_still=True)
    print("BEAUTY_SHOT", args.out)


if __name__ == "__main__":
    main()
