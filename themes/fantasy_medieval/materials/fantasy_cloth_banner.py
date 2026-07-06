"""themes/fantasy_medieval/materials/fantasy_cloth_banner -- dyed cloth
banner/tabard fabric: matte, noise-broken-up weave, sampled from ``accent``
(spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "weave_scale": {"type": "number", "minimum": 30.0, "maximum": 120.0, "default": 60.0},
        "roughness": {"type": "number", "minimum": 0.5, "maximum": 0.95, "default": 0.8},
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

    weave = nt.nodes.new("ShaderNodeGroup")
    weave.node_tree = nodes.noise_breakup()
    nt.links.new(tex_coord.outputs["Object"], weave.inputs["Vector"])
    weave.inputs["Scale"].default_value = params["weave_scale"]

    r, g, b = palette.sample_palette_color(palette_dict, "accent", rng)
    base = nt.nodes.new("ShaderNodeRGB")
    base.outputs[0].default_value = (r, g, b, 1.0)

    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 0.2
    nt.links.new(base.outputs[0], tint.inputs[6])
    nt.links.new(weave.outputs["Fac"], tint.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    nt.links.new(weave.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(tint.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
