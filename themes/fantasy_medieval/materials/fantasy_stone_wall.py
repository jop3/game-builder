"""themes/fantasy_medieval/materials/fantasy_stone_wall -- rough-hewn
dressed stone blocks, built from ``matlib.nodes.stone_base`` sampled from
``primary`` (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "cell_scale": {"type": "number", "minimum": 3.0, "maximum": 10.0, "default": 5.0},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = True


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    periodic = nt.nodes.new("ShaderNodeGroup")
    periodic.node_tree = nodes.periodic_coords()
    nt.links.new(tex_coord.outputs["UV"], periodic.inputs["UV"])

    stone = nt.nodes.new("ShaderNodeGroup")
    stone.node_tree = nodes.stone_base()
    nt.links.new(periodic.outputs["Vector"], stone.inputs["Vector"])
    stone.inputs["Cell Scale"].default_value = params["cell_scale"]
    r, g, b = palette.sample_palette_color(palette_dict, "primary", rng)
    stone.inputs["Base Color"].default_value = (r, g, b, 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.3
    nt.links.new(stone.outputs["Color"], bump.inputs["Height"])

    nt.links.new(stone.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(stone.outputs["Roughness"], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
