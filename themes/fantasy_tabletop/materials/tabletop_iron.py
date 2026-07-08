"""themes/fantasy_tabletop/materials/tabletop_iron -- dark forged iron for the
board's grid inlay + border rim. Near-neutral dark metal with subtle hammered
value variation and a faint edge lift, metallic, moderate roughness so it
reads as forged iron catching the candlelight.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.3, "maximum": 0.6, "default": 0.42},
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
    breakup.inputs["Scale"].default_value = 8.0
    breakup.inputs["Contrast"].default_value = 1.0

    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.045
    band.inputs["To Max"].default_value = 0.085
    grey = nt.nodes.new("ShaderNodeCombineColor")
    nt.links.new(band.outputs["Result"], grey.inputs["Red"])
    nt.links.new(band.outputs["Result"], grey.inputs["Green"])
    nt.links.new(band.outputs["Result"], grey.inputs["Blue"])
    nt.links.new(grey.outputs["Color"], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    nt.links.new(breakup.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.85
