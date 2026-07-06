"""themes/scifi_industrial/materials/scifi_hull_metal -- spec 10.2 example.

Desaturated blue-grey brushed hull plating sampled from the theme's
``primary`` palette group, built from ``matlib.nodes.metal_base``.
"""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.15, "maximum": 0.6, "default": 0.35},
        "noise_scale": {"type": "number", "minimum": 4.0, "maximum": 30.0, "default": 12.0},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    """Populate ``nt`` ending in a Principled BSDF wired to Material Output."""
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
    r, g, b = palette.sample_palette_color(palette_dict, "primary", rng)
    metal.inputs["Base Color"].default_value = (r, g, b, 1.0)

    wear = nt.nodes.new("ShaderNodeGroup")
    wear.node_tree = nodes.edge_wear()
    lighten = nt.nodes.new("ShaderNodeMix")
    lighten.data_type = "RGBA"
    lighten.blend_type = "MIX"
    nt.links.new(wear.outputs["Fac"], lighten.inputs[0])
    nt.links.new(metal.outputs["Color"], lighten.inputs[6])
    lighten.inputs[7].default_value = (0.85, 0.85, 0.85, 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.15
    nt.links.new(wear.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(lighten.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(metal.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs["Metallic"], bsdf.inputs["Metallic"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
