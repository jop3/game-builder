"""themes/reversi_classic/materials/felt_green -- the green felt play surface
of a classic Reversi board. Matte deep green with fine fiber value noise so it
reads as felt (and clears the S16 not-flat variance test naturally, no
flat_color needed).
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.7, "maximum": 1.0, "default": 0.9},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex = nt.nodes.new("ShaderNodeTexCoord")
    # fine fiber noise for felt
    fibers = nt.nodes.new("ShaderNodeGroup")
    fibers.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], fibers.inputs["Vector"])
    fibers.inputs["Scale"].default_value = 40.0
    fibers.inputs["Contrast"].default_value = 1.0

    green = nt.nodes.new("ShaderNodeRGB")
    green.outputs[0].default_value = (0.045, 0.205, 0.075, 1.0)   # billiard/board green
    # multiply green by a value band derived from the fibers (0.8..1.15)
    val = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(fibers.outputs["Fac"], val.inputs["Value"])
    val.inputs["To Min"].default_value = 0.78
    val.inputs["To Max"].default_value = 1.18
    mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    nt.links.new(green.outputs[0], mul.inputs[6])
    nt.links.new(val.outputs["Result"], mul.inputs[7])
    nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    nt.links.new(fibers.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
