"""themes/fantasy_medieval/materials/fantasy_iron_trim -- hand-forged iron
banding/hinges: dark metal with heavy edge wear, sampled from ``accent``
(spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.3, "maximum": 0.7, "default": 0.5},
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

    metal = nt.nodes.new("ShaderNodeGroup")
    metal.node_tree = nodes.metal_base()
    nt.links.new(tex_coord.outputs["Object"], metal.inputs["Vector"])
    metal.inputs["Roughness"].default_value = params["roughness"]
    r, g, b = palette.sample_palette_color(palette_dict, "accent", rng)
    metal.inputs["Base Color"].default_value = (r, g, b, 1.0)

    wear = nt.nodes.new("ShaderNodeGroup")
    wear.node_tree = nodes.edge_wear()
    wear.inputs["Radius"].default_value = 0.015
    lighten = nt.nodes.new("ShaderNodeMix")
    lighten.data_type = "RGBA"
    lighten.blend_type = "MIX"
    nt.links.new(wear.outputs["Fac"], lighten.inputs[0])
    nt.links.new(metal.outputs["Color"], lighten.inputs[6])
    lighten.inputs[7].default_value = (0.6, 0.55, 0.5, 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    nt.links.new(wear.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(lighten.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(metal.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs["Metallic"], bsdf.inputs["Metallic"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
