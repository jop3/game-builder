"""themes/fantasy_medieval/materials/fantasy_iron_trim -- hand-forged iron
banding/hinges with the painted look (docs/COLOR_WAVE.md item 5): a DARK
desaturated iron tone tinted by the ``accent`` draw (the raw accent gold
through ``metal_base``'s white-mixing breakup bleached thin hoops to cream
-- verified on the standalone barrel's renders), per-rivet/per-segment value
jitter keyed to a coarse cell grid (``matlib.nodes.cell_jitter``), and
bare-steel edge highlights (``matlib.nodes.edge_wear``, tight radius + high
sharpness so thin bands don't read fully worn). ``metal_base`` supplies only
roughness/metallic; the color chain is built here. Discrete pattern: raw
object coordinates, never the periodic domain (docs/NEXT_STEPS.md)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.3, "maximum": 0.7, "default": 0.5},
        # Painted edge-highlight intensity (0 disables the read entirely) --
        # same convention as fantasy_aged_wood / fantasy_roof_shingles.
        "edge_highlight": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                           "default": 0.6},
        # Explicit metal color ("#RRGGBB"), e.g. description-driven
        # (docs/COLOR_WAVE.md item 1). Empty -> sampled from ``accent``.
        "color1_hex": {"type": "string", "default": ""},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

# Jitter cell size in meters: iron trim reads as ~8 cm forged segments/rivet
# spans; square cells (offset rows would imply coursed masonry, not metal).
CELL_W, CELL_H, CELL_OFFSET = 0.08, 0.08, 0.0


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    # metal_base supplies roughness variation + metallic only; its Color
    # output mixes toward WHITE with the breakup noise (fine for brushed
    # sci-fi hull, bleaches forged iron) and is deliberately unused.
    metal = nt.nodes.new("ShaderNodeGroup")
    metal.node_tree = nodes.metal_base()
    nt.links.new(tex_coord.outputs["Object"], metal.inputs["Vector"])
    metal.inputs["Roughness"].default_value = params["roughness"]

    # Forged-iron albedo: near-black neutral grey with only a SUBTLE tint
    # from the accent draw (or the description-derived color1_hex) -- a
    # 0.16x tint read khaki-olive under neutral light; forged iron must
    # stay within a hair of neutral.
    r, g, b = (palette.hex_to_rgb(params["color1_hex"]) if params.get("color1_hex")
               else palette.sample_palette_color(palette_dict, "accent", rng))
    iron = (0.082 + r * 0.055, 0.080 + g * 0.055, 0.082 + b * 0.062)
    iron_rgb = nt.nodes.new("ShaderNodeRGB")
    iron_rgb.outputs[0].default_value = (iron[0], iron[1], iron[2], 1.0)

    # Hammered unevenness: low-frequency value variation over the dark base.
    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = nodes.noise_breakup()
    nt.links.new(tex_coord.outputs["Object"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 9.0
    hammer_var = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], hammer_var.inputs["Value"])
    hammer_var.inputs["To Min"].default_value = 0.85
    hammer_var.inputs["To Max"].default_value = 1.12
    hammered = nt.nodes.new("ShaderNodeMix")
    hammered.data_type = "RGBA"
    hammered.blend_type = "MULTIPLY"
    hammered.inputs[0].default_value = 1.0
    nt.links.new(iron_rgb.outputs[0], hammered.inputs[6])
    nt.links.new(hammer_var.outputs["Result"], hammered.inputs[7])

    # Per-rivet/per-segment value jitter (item 5): one stable random value
    # per cell of a grid in raw object space, multiplied through the metal
    # color -- neighboring segments read as separately forged pieces.
    cells = nt.nodes.new("ShaderNodeGroup")
    cells.node_tree = nodes.cell_jitter()
    nt.links.new(tex_coord.outputs["Object"], cells.inputs["Vector"])
    cells.inputs["Brick Width"].default_value = CELL_W
    cells.inputs["Row Height"].default_value = CELL_H
    cells.inputs["Offset"].default_value = CELL_OFFSET
    value_jit = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(cells.outputs["Fac"], value_jit.inputs["Value"])
    value_jit.inputs["To Min"].default_value = 0.82
    value_jit.inputs["To Max"].default_value = 1.12
    valued = nt.nodes.new("ShaderNodeMix")
    valued.data_type = "RGBA"
    valued.blend_type = "MULTIPLY"
    valued.inputs[0].default_value = 1.0
    nt.links.new(hammered.outputs[2], valued.inputs[6])
    nt.links.new(value_jit.outputs["Result"], valued.inputs[7])

    # Bare-steel edge highlights: neutral worn metal, NOT a tint of the
    # base (tinted 1.8x highlights bleached whole thin hoops to cream).
    # Tight radius + high sharpness keep the mask to actual edges on small
    # bands; the factor is additionally halved so even full-mask texels
    # stay metal-grey.
    wear = nt.nodes.new("ShaderNodeGroup")
    wear.node_tree = nodes.edge_wear()
    wear.inputs["Radius"].default_value = 0.008
    wear.inputs["Sharpness"].default_value = 0.72
    edge_fac = nt.nodes.new("ShaderNodeMath")
    edge_fac.operation = "MULTIPLY"
    nt.links.new(wear.outputs["Fac"], edge_fac.inputs[0])
    edge_fac.inputs[1].default_value = params["edge_highlight"] * 0.55
    lighten = nt.nodes.new("ShaderNodeMix")
    lighten.data_type = "RGBA"
    nt.links.new(edge_fac.outputs[0], lighten.inputs[0])
    nt.links.new(valued.outputs[2], lighten.inputs[6])
    lighten.inputs[7].default_value = (0.55, 0.54, 0.52, 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    nt.links.new(wear.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(lighten.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(metal.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs["Metallic"], bsdf.inputs["Metallic"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
