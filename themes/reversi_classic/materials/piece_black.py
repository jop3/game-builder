"""themes/reversi_classic/materials/piece_black -- glossy near-pure-black
Reversi disc. Higher contrast than the fantasy obsidian: a very dark base
(just above the S16 luminance floor) with a low roughness so it shows the
sharp bright specular dot of a real polished plastic/stone counter. The disc
generator declares flat_color, so S16 only enforces the luminance floor.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.04, "maximum": 0.25, "default": 0.08},
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
    breakup.inputs["Scale"].default_value = 6.0
    breakup.inputs["Contrast"].default_value = 1.0

    # Near-pure black, kept just above the S16 luminance floor (0.02).
    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.021
    band.inputs["To Max"].default_value = 0.030
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
