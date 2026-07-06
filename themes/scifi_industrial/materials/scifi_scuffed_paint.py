"""themes/scifi_industrial/materials/scifi_scuffed_paint -- painted panel
finish over metal, sampled from ``secondary``, with grunge-driven scuffs
revealing the metal base beneath (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.3, "maximum": 0.8, "default": 0.5},
        "scuff_amount": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
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

    paint_r, paint_g, paint_b = palette.sample_palette_color(palette_dict, "secondary", rng)
    metal_r, metal_g, metal_b = palette.sample_palette_color(palette_dict, "primary", rng)

    grunge = nt.nodes.new("ShaderNodeGroup")
    grunge.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], grunge.inputs["Vector"])
    grunge.inputs["Scale"].default_value = 6.0

    scuff_mask = nt.nodes.new("ShaderNodeMath")
    scuff_mask.operation = "LESS_THAN"
    nt.links.new(grunge.outputs["Fac"], scuff_mask.inputs[0])
    scuff_mask.inputs[1].default_value = params["scuff_amount"]

    color_mix = nt.nodes.new("ShaderNodeMix")
    color_mix.data_type = "RGBA"
    nt.links.new(scuff_mask.outputs[0], color_mix.inputs[0])
    color_mix.inputs[6].default_value = (paint_r, paint_g, paint_b, 1.0)
    color_mix.inputs[7].default_value = (metal_r, metal_g, metal_b, 1.0)

    rough_mix = nt.nodes.new("ShaderNodeMix")
    rough_mix.data_type = "FLOAT"
    nt.links.new(scuff_mask.outputs[0], rough_mix.inputs[0])
    rough_mix.inputs[2].default_value = params["roughness"]
    rough_mix.inputs[3].default_value = 0.3

    metallic_mix = nt.nodes.new("ShaderNodeMix")
    metallic_mix.data_type = "FLOAT"
    nt.links.new(scuff_mask.outputs[0], metallic_mix.inputs[0])
    metallic_mix.inputs[2].default_value = 0.0
    metallic_mix.inputs[3].default_value = 1.0

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.08
    nt.links.new(grunge.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(color_mix.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(rough_mix.outputs[0], bsdf.inputs["Roughness"])
    nt.links.new(metallic_mix.outputs[0], bsdf.inputs["Metallic"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
