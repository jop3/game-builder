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
    ra, ga, ba = palette.sample_palette_color(palette_dict, "primary", rng)
    rb, gb, bb = palette.sample_palette_color(palette_dict, "secondary", rng)
    wood.inputs["Color A"].default_value = (ra, ga, ba, 1.0)
    wood.inputs["Color B"].default_value = (rb, gb, bb, 1.0)

    grime = nt.nodes.new("ShaderNodeGroup")
    grime.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], grime.inputs["Vector"])
    grime.inputs["Scale"].default_value = 5.0

    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs[0].default_value = 0.3
    nt.links.new(wood.outputs["Color"], darken.inputs[6])
    nt.links.new(grime.outputs["Fac"], darken.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    nt.links.new(wood.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(darken.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
