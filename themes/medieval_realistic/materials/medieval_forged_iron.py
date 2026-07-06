"""themes/medieval_realistic/materials/medieval_forged_iron -- pitted,
hand-hammered ironwork with heavy edge wear, sampled from ``accent``
(spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.4, "maximum": 0.8, "default": 0.6},
        "pitting": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
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

    pits = nt.nodes.new("ShaderNodeGroup")
    pits.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], pits.inputs["Vector"])
    pits.inputs["Scale"].default_value = 25.0

    pit_darken = nt.nodes.new("ShaderNodeMix")
    pit_darken.data_type = "RGBA"
    pit_darken.blend_type = "MULTIPLY"
    pit_darken.inputs[0].default_value = params["pitting"]
    nt.links.new(metal.outputs["Color"], pit_darken.inputs[6])
    nt.links.new(pits.outputs["Fac"], pit_darken.inputs[7])

    wear = nt.nodes.new("ShaderNodeGroup")
    wear.node_tree = nodes.edge_wear()
    wear.inputs["Radius"].default_value = 0.02

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.3
    nt.links.new(pits.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(pit_darken.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(metal.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs["Metallic"], bsdf.inputs["Metallic"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
