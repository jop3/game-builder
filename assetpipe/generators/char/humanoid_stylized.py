"""char/humanoid_stylized -- category ``character_primary``.

Assembles a stylized humanoid from parameterized modular part primitives
(head/torso/limbs, bmesh boxes and capsule-like cylinders scaled by
proportion params) and rigs them onto a fixed armature using the
Godot/glTF humanoid bone names (``Hips, Spine, Chest, Neck, Head,
Left/RightUpperArm...`` per Godot's ``SkeletonProfileHumanoid``), automatic
weights, then weight cleanup (max 4 influences, normalized) -- spec 9.1.
"""
from __future__ import annotations

# Godot SkeletonProfileHumanoid bone names used for the rest-pose armature.
BONE_NAMES = (
    "Hips", "Spine", "Chest", "UpperChest", "Neck", "Head",
    "LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
    "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand",
    "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes",
    "RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes",
)

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "height_m": {"type": "number", "minimum": 1.6, "maximum": 2.0, "default": 1.75},
        "head_scale": {"type": "number", "minimum": 0.8, "maximum": 1.6, "default": 1.2},
        "shoulder_width_m": {"type": "number", "minimum": 0.35, "maximum": 0.55, "default": 0.42},
        "limb_thickness": {"type": "number", "minimum": 0.6, "maximum": 1.3, "default": 1.0},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
CATEGORY = "character_primary"
KEYWORDS = ["humanoid", "character", "npc", "biped", "person"]

# Real-world scale, character_primary (spec 9.4): 1.6-2.0 m tall, feet origin.
BBOX_RANGE = {"min": [0.4, 0.3, 1.6], "max": [0.7, 0.5, 2.0]}


def _add_part_box(bm, size, center):
    import bmesh

    part = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=part["verts"], vec=size)
    bmesh.ops.translate(bm, verts=part["verts"], vec=center)
    return part


def _add_part_capsule(bm, radius, length, center, segments=8):
    import bmesh

    part = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                                  radius1=radius, radius2=radius, depth=length)
    bmesh.ops.translate(bm, verts=part["verts"], vec=center)
    return part


def _proportions(height_m: float) -> dict:
    """Stylized (slightly big-headed) humanoid proportion breakdown, all as
    fractions of ``height_m`` -- purely parametric, no rng needed for the
    skeletal proportions themselves.
    """
    return {
        "leg_h": height_m * 0.48,
        "hip_h": height_m * 0.02,
        "torso_h": height_m * 0.28,
        "neck_h": height_m * 0.04,
        "head_h": height_m * 0.18,
        "arm_h": height_m * 0.42,
    }


def generate(params: dict, rng, theme: dict):
    """Build the mesh, rig it onto a Godot-humanoid-named armature with
    automatic weights, clean up weights, and return the root object.

    Determinism: proportions are purely parametric; ``rng`` is reserved for
    future stylization variance (e.g. asymmetric detail) and is accepted
    but not required for the base silhouette.
    """
    import bmesh
    import bpy
    from mathutils import Vector

    from assetpipe.generators import common

    height = params["height_m"]
    prop = _proportions(height)
    shoulder_w = params["shoulder_width_m"]
    thick = params["limb_thickness"]

    bm = bmesh.new()

    leg_r = 0.07 * thick
    z = prop["leg_h"] / 2.0
    _add_part_capsule(bm, leg_r, prop["leg_h"], (-shoulder_w * 0.22, 0.0, z))
    _add_part_capsule(bm, leg_r, prop["leg_h"], (shoulder_w * 0.22, 0.0, z))

    hips_z = prop["leg_h"]
    _add_part_box(bm, (shoulder_w * 0.7, 0.22, prop["hip_h"] + prop["torso_h"] * 0.15),
                  (0.0, 0.0, hips_z + (prop["hip_h"] + prop["torso_h"] * 0.15) / 2.0))

    torso_z = hips_z + prop["hip_h"] + prop["torso_h"] * 0.15
    _add_part_box(bm, (shoulder_w, 0.24, prop["torso_h"]),
                  (0.0, 0.0, torso_z + prop["torso_h"] / 2.0))

    arm_r = 0.05 * thick
    arm_z = torso_z + prop["torso_h"] * 0.85 - prop["arm_h"] / 2.0
    _add_part_capsule(bm, arm_r, prop["arm_h"], (-shoulder_w * 0.62 - arm_r, 0.0, arm_z))
    _add_part_capsule(bm, arm_r, prop["arm_h"], (shoulder_w * 0.62 + arm_r, 0.0, arm_z))

    neck_z = torso_z + prop["torso_h"]
    _add_part_capsule(bm, 0.05 * thick, prop["neck_h"], (0.0, 0.0, neck_z + prop["neck_h"] / 2.0))

    head_r = 0.09 * thick * params["head_scale"]
    head_z = neck_z + prop["neck_h"] + head_r
    head = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=head_r)
    bmesh.ops.translate(bm, verts=head["verts"], vec=(0.0, 0.0, head_z))

    common.feet_origin(bm)
    common.finishing_pass(bm)
    obj = common.emit_object(bm, "humanoid_stylized")
    common.freeze_transform(obj)
    common.decimate_to_budget(obj, budget=30000)
    common.smart_uv_project(obj, texture_resolution=2048)

    _rig(obj, height, prop, shoulder_w)
    return obj


def _rig(obj, height, prop, shoulder_w):
    """Build the armature with Godot humanoid bone names, parent the mesh
    with automatic weights, then enforce the glTF weight contract (max 4
    influences, normalized) -- spec 9.1 / 13.1.
    """
    import bpy

    armature_data = bpy.data.armatures.new("humanoid_armature")
    armature_obj = bpy.data.objects.new("Armature", armature_data)
    collection = bpy.data.collections.get("EXPORT") or bpy.context.collection
    collection.objects.link(armature_obj)

    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")
    eb = armature_data.edit_bones

    hips_z = prop["leg_h"]
    torso_z = hips_z + prop["hip_h"] + prop["torso_h"] * 0.15
    neck_z = torso_z + prop["torso_h"]
    head_z = neck_z + prop["neck_h"]

    chain = [
        ("Hips", (0, 0, hips_z), (0, 0, torso_z)),
        ("Spine", (0, 0, hips_z), (0, 0, torso_z * 0.6 + hips_z * 0.4)),
        ("Chest", (0, 0, torso_z * 0.6 + hips_z * 0.4), (0, 0, torso_z)),
        ("UpperChest", (0, 0, torso_z), (0, 0, neck_z)),
        ("Neck", (0, 0, neck_z), (0, 0, neck_z + prop["neck_h"])),
        ("Head", (0, 0, head_z), (0, 0, head_z + prop["head_h"])),
        ("LeftUpperLeg", (-shoulder_w * 0.22, 0, hips_z), (-shoulder_w * 0.22, 0, hips_z * 0.5)),
        ("LeftLowerLeg", (-shoulder_w * 0.22, 0, hips_z * 0.5), (-shoulder_w * 0.22, 0, 0.0)),
        ("LeftFoot", (-shoulder_w * 0.22, 0, 0.0), (-shoulder_w * 0.22, 0.1, 0.0)),
        ("RightUpperLeg", (shoulder_w * 0.22, 0, hips_z), (shoulder_w * 0.22, 0, hips_z * 0.5)),
        ("RightLowerLeg", (shoulder_w * 0.22, 0, hips_z * 0.5), (shoulder_w * 0.22, 0, 0.0)),
        ("RightFoot", (shoulder_w * 0.22, 0, 0.0), (shoulder_w * 0.22, 0.1, 0.0)),
        ("LeftShoulder", (-shoulder_w * 0.3, 0, neck_z), (-shoulder_w * 0.62, 0, neck_z)),
        ("LeftUpperArm", (-shoulder_w * 0.62, 0, neck_z), (-shoulder_w * 0.62, 0, torso_z)),
        ("LeftLowerArm", (-shoulder_w * 0.62, 0, torso_z), (-shoulder_w * 0.62, 0, hips_z)),
        ("LeftHand", (-shoulder_w * 0.62, 0, hips_z), (-shoulder_w * 0.62, 0, hips_z * 0.85)),
        ("RightShoulder", (shoulder_w * 0.3, 0, neck_z), (shoulder_w * 0.62, 0, neck_z)),
        ("RightUpperArm", (shoulder_w * 0.62, 0, neck_z), (shoulder_w * 0.62, 0, torso_z)),
        ("RightLowerArm", (shoulder_w * 0.62, 0, torso_z), (shoulder_w * 0.62, 0, hips_z)),
        ("RightHand", (shoulder_w * 0.62, 0, hips_z), (shoulder_w * 0.62, 0, hips_z * 0.85)),
    ]
    bones = {}
    for name, head, tail in chain:
        bone = eb.new(name)
        bone.head = head
        bone.tail = tail
        bones[name] = bone

    parent_of = {
        "Spine": "Hips", "Chest": "Spine", "UpperChest": "Chest", "Neck": "UpperChest",
        "Head": "Neck",
        "LeftUpperLeg": "Hips", "LeftLowerLeg": "LeftUpperLeg", "LeftFoot": "LeftLowerLeg",
        "RightUpperLeg": "Hips", "RightLowerLeg": "RightUpperLeg", "RightFoot": "RightLowerLeg",
        "LeftShoulder": "UpperChest", "LeftUpperArm": "LeftShoulder",
        "LeftLowerArm": "LeftUpperArm", "LeftHand": "LeftLowerArm",
        "RightShoulder": "UpperChest", "RightUpperArm": "RightShoulder",
        "RightLowerArm": "RightUpperArm", "RightHand": "RightLowerArm",
    }
    for child, parent in parent_of.items():
        bones[child].parent = bones[parent]

    bpy.ops.object.mode_set(mode="OBJECT")

    # Parent mesh to armature with automatic weights (needs both objects
    # visible in the view layer -- headless caveat per the
    # blender-procedural-geometry skill).
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")

    # glTF/game weight contract: max 4 influences per vertex, renormalized.
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.vertex_group_limit_total(limit=4)
    bpy.ops.object.vertex_group_normalize_all(lock_active=False)
