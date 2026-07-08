"""themes/fantasy_tabletop/materials/tabletop_wood -- warm oak for the board
surface + base. Warmer and cleaner than fantasy_medieval's aged wood: honeyed
oak with legible ring grain, moderate roughness, so the board reads as a
cared-for game board rather than a weathered plank.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.35, "maximum": 0.7, "default": 0.52},
        "ring_scale": {"type": "number", "minimum": 2.0, "maximum": 8.0, "default": 4.5},
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

    grain = nt.nodes.new("ShaderNodeGroup")
    grain.node_tree = nodes.wood_grain()
    nt.links.new(tex.outputs["Object"], grain.inputs["Vector"])
    grain.inputs["Color A"].default_value = (0.28, 0.155, 0.070, 1.0)   # honey oak
    grain.inputs["Color B"].default_value = (0.165, 0.085, 0.038, 1.0)  # darker ring
    grain.inputs["Ring Scale"].default_value = params["ring_scale"]
    nt.links.new(grain.outputs["Color"], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.14
    nt.links.new(grain.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
