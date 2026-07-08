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

    # Two-scale fiber noise: a fine weave over a coarser mottle, so the felt has
    # visible texture rather than reading as a flat green card.
    fine = nt.nodes.new("ShaderNodeGroup")
    fine.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], fine.inputs["Vector"])
    fine.inputs["Scale"].default_value = 95.0        # tight weave
    fine.inputs["Contrast"].default_value = 1.3
    coarse = nt.nodes.new("ShaderNodeGroup")
    coarse.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], coarse.inputs["Vector"])
    coarse.inputs["Scale"].default_value = 22.0      # broad nap variation
    coarse.inputs["Contrast"].default_value = 1.0
    # blend the two scales (average) so both the tight weave and the broad nap
    # contribute spread, rather than multiplying (which concentrates to dark).
    tex_mix = nt.nodes.new("ShaderNodeMix")
    tex_mix.data_type = "FLOAT"
    tex_mix.inputs[0].default_value = 0.5
    nt.links.new(fine.outputs["Fac"], tex_mix.inputs[2])
    nt.links.new(coarse.outputs["Fac"], tex_mix.inputs[3])

    green = nt.nodes.new("ShaderNodeRGB")
    green.outputs[0].default_value = (0.022, 0.125, 0.050, 1.0)   # deep billiard green
    # wide value band for pronounced, visible weave texture
    val = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(tex_mix.outputs[0], val.inputs["Value"])
    val.inputs["To Min"].default_value = 0.45
    val.inputs["To Max"].default_value = 1.5
    mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    nt.links.new(green.outputs[0], mul.inputs[6])
    nt.links.new(val.outputs["Result"], mul.inputs[7])
    nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])

    # felt fuzz: a stronger bump keyed to the fine weave
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.18
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
