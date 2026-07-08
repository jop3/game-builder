"""themes/reversi_classic/materials/frame_black -- glossy black plastic for the
board's frame rim + grid lines. Near-black with a low-ish roughness so the
frame catches a plastic sheen, like the moulded bezel of a real Reversi set.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.1, "maximum": 0.5, "default": 0.25},
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
    breakup.inputs["Scale"].default_value = 7.0
    breakup.inputs["Contrast"].default_value = 1.0

    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.022
    band.inputs["To Max"].default_value = 0.038
    grey = nt.nodes.new("ShaderNodeCombineColor")
    for ch in ("Red", "Green", "Blue"):
        nt.links.new(band.outputs["Result"], grey.inputs[ch])
    nt.links.new(grey.outputs["Color"], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.04
    nt.links.new(breakup.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
