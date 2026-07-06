"""themes/scifi_industrial/materials/scifi_deck_plate -- a tiling
walkable-deck plating: raised-rivet panel grid over brushed metal, built in
4D-periodic mode via ``matlib.nodes.periodic_coords`` so it bakes seamlessly
onto a unit-plane tiling texture set (spec 10.2-10.3)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "grid_rows": {"type": "number", "minimum": 2.0, "maximum": 12.0, "default": 4.0},
        "roughness": {"type": "number", "minimum": 0.2, "maximum": 0.6, "default": 0.4},
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

    # Every texture node in a TILING recipe must route through
    # periodic_coords (spec 10.3) -- both the panel grid and the metal
    # breakup below consume its Vector/W outputs, never raw UV.
    periodic = nt.nodes.new("ShaderNodeGroup")
    periodic.node_tree = nodes.periodic_coords()
    nt.links.new(tex_coord.outputs["UV"], periodic.inputs["UV"])

    panels = nt.nodes.new("ShaderNodeGroup")
    panels.node_tree = nodes.panel_lines()
    nt.links.new(periodic.outputs["Vector"], panels.inputs["Vector"])
    panels.inputs["Rows"].default_value = params["grid_rows"]

    metal = nt.nodes.new("ShaderNodeGroup")
    metal.node_tree = nodes.metal_base()
    nt.links.new(periodic.outputs["Vector"], metal.inputs["Vector"])
    metal.inputs["Roughness"].default_value = params["roughness"]
    r, g, b = palette.sample_palette_color(palette_dict, "secondary", rng)
    metal.inputs["Base Color"].default_value = (r, g, b, 1.0)

    groove_darken = nt.nodes.new("ShaderNodeMix")
    groove_darken.data_type = "RGBA"
    groove_darken.blend_type = "MULTIPLY"
    nt.links.new(panels.outputs["Fac"], groove_darken.inputs[0])
    nt.links.new(metal.outputs["Color"], groove_darken.inputs[6])
    groove_darken.inputs[7].default_value = (0.4, 0.4, 0.4, 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    nt.links.new(panels.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(groove_darken.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(metal.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs["Metallic"], bsdf.inputs["Metallic"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
