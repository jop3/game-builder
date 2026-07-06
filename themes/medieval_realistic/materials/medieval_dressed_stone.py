"""themes/medieval_realistic/materials/medieval_dressed_stone -- tooled
stone blocks with heavier grunge/wear than the fantasy theme's version,
sampled from ``primary`` (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "cell_scale": {"type": "number", "minimum": 3.0, "maximum": 8.0, "default": 4.5},
        "grime_amount": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5},
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

    grime = nt.nodes.new("ShaderNodeGroup")
    grime.node_tree = nodes.grunge()
    nt.links.new(periodic.outputs["Vector"], grime.inputs["Vector"])

    grime_mix = nt.nodes.new("ShaderNodeMix")
    grime_mix.data_type = "RGBA"
    grime_mix.blend_type = "MULTIPLY"
    grime_mix.inputs[0].default_value = params["grime_amount"]
    nt.links.new(stone.outputs["Color"], grime_mix.inputs[6])
    nt.links.new(grime.outputs["Fac"], grime_mix.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    nt.links.new(stone.outputs["Color"], bump.inputs["Height"])

    nt.links.new(grime_mix.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(stone.outputs["Roughness"], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
