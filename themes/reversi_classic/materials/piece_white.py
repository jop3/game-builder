"""themes/reversi_classic/materials/piece_white -- glossy near-pure-white
Reversi disc. Bright, clean white with a low roughness sheen for maximum
contrast against piece_black. flat_color declared by the disc generator.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.05, "maximum": 0.3, "default": 0.13},
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
    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 5.0
    breakup.inputs["Contrast"].default_value = 0.7

    # Bright neutral white, a hair below the S16 blown ceiling (0.98).
    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.90
    band.inputs["To Max"].default_value = 0.96
    grey = nt.nodes.new("ShaderNodeCombineColor")
    for ch in ("Red", "Green", "Blue"):
        nt.links.new(band.outputs["Result"], grey.inputs[ch])
    nt.links.new(grey.outputs["Color"], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.03
    nt.links.new(breakup.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
