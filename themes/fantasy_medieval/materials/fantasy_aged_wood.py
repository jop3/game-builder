"""themes/fantasy_medieval/materials/fantasy_aged_wood -- weathered timber
planking, built from ``matlib.nodes.wood_grain`` sampled from ``primary`` /
``secondary`` (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "ring_scale": {"type": "number", "minimum": 8.0, "maximum": 30.0, "default": 16.0},
        "roughness": {"type": "number", "minimum": 0.4, "maximum": 0.85, "default": 0.65},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    wood = nt.nodes.new("ShaderNodeGroup")
    wood.node_tree = nodes.wood_grain()
    nt.links.new(tex_coord.outputs["Object"], wood.inputs["Vector"])
    wood.inputs["Ring Scale"].default_value = params["ring_scale"]
    # Both draws from ``primary`` (the theme's dark browns): the old
    # primary/secondary mix let the light grey dominate and walls rendered
    # plaster-pale next to the reference's chocolate planks.
    ra, ga, ba = palette.sample_palette_color(palette_dict, "primary", rng)
    rb, gb, bb = palette.sample_palette_color(palette_dict, "primary", rng)
    wood.inputs["Color A"].default_value = (ra * 0.55, ga * 0.55, ba * 0.55, 1.0)
    wood.inputs["Color B"].default_value = (rb, gb, bb, 1.0)

    # Plank seams: a stretched brick grid in a swizzled object space gives
    # vertical boards with sparse horizontal joints on every wall
    # orientation -- discrete pattern on raw coordinates (docs/NEXT_STEPS.md:
    # never route brick/grid through the periodic domain).
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tex_coord.outputs["Object"], sep.inputs["Vector"])
    across = nt.nodes.new("ShaderNodeMath")
    across.operation = "ADD"
    nt.links.new(sep.outputs["X"], across.inputs[0])
    nt.links.new(sep.outputs["Y"], across.inputs[1])
    swizzle = nt.nodes.new("ShaderNodeCombineXYZ")
    nt.links.new(across.outputs[0], swizzle.inputs["X"])
    nt.links.new(sep.outputs["Z"], swizzle.inputs["Y"])
    planks = nt.nodes.new("ShaderNodeTexBrick")
    planks.offset = 0.5
    planks.inputs["Scale"].default_value = 1.0
    planks.inputs["Brick Width"].default_value = 0.34
    planks.inputs["Row Height"].default_value = 6.0
    planks.inputs["Mortar Size"].default_value = 0.012
    planks.inputs["Mortar"].default_value = (0.02, 0.012, 0.008, 1.0)
    planks.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    planks.inputs["Color2"].default_value = (0.78, 0.74, 0.7, 1.0)
    nt.links.new(swizzle.outputs["Vector"], planks.inputs["Vector"])

    board_tint = nt.nodes.new("ShaderNodeMix")
    board_tint.data_type = "RGBA"
    board_tint.blend_type = "MULTIPLY"
    board_tint.inputs[0].default_value = 1.0
    nt.links.new(wood.outputs["Color"], board_tint.inputs[6])
    nt.links.new(planks.outputs["Color"], board_tint.inputs[7])

    grime = nt.nodes.new("ShaderNodeGroup")
    grime.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], grime.inputs["Vector"])
    grime.inputs["Scale"].default_value = 5.0

    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs[0].default_value = 0.45
    nt.links.new(board_tint.outputs[2], darken.inputs[6])
    nt.links.new(grime.outputs["Fac"], darken.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    nt.links.new(wood.outputs["Fac"], bump.inputs["Height"])
    plank_bump = nt.nodes.new("ShaderNodeBump")
    plank_bump.inputs["Strength"].default_value = 0.3
    nt.links.new(planks.outputs["Fac"], plank_bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], plank_bump.inputs["Normal"])

    nt.links.new(darken.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(plank_bump.outputs["Normal"], bsdf.inputs["Normal"])
