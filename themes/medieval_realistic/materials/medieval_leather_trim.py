"""themes/medieval_realistic/materials/medieval_leather_trim -- tanned
leather strapping/trim, sampled from ``secondary`` (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "grain_scale": {"type": "number", "minimum": 20.0, "maximum": 80.0, "default": 45.0},
        "roughness": {"type": "number", "minimum": 0.4, "maximum": 0.8, "default": 0.6},
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

    grain = nt.nodes.new("ShaderNodeGroup")
    grain.node_tree = nodes.noise_breakup()
    nt.links.new(tex_coord.outputs["Object"], grain.inputs["Vector"])
    grain.inputs["Scale"].default_value = params["grain_scale"]

    r, g, b = palette.sample_palette_color(palette_dict, "secondary", rng)
    base = nt.nodes.new("ShaderNodeRGB")
    base.outputs[0].default_value = (r, g, b, 1.0)

    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 0.35
    nt.links.new(base.outputs[0], tint.inputs[6])
    nt.links.new(grain.outputs["Fac"], tint.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.1
    nt.links.new(grain.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(tint.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
