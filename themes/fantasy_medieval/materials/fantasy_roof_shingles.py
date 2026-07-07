"""themes/fantasy_medieval/materials/fantasy_roof_shingles -- overlapping
shingle courses in the theme's oxblood accent (docs/TEXTURE_WAVE.md items
1+2): the painted course grid is scaled to MATCH the geometric course slabs
(env/house lays 7 rows per slope, ~0.25 m apart -- ``course_scale`` is now
courses-per-meter so painted and geometric rows agree instead of fighting),
with per-tile value jitter + oxblood/rust hue shift keyed to the course
cells (``matlib.nodes.cell_jitter``), darkened mortar/underside lines, and
painted edge highlights on the proud slab edges (``matlib.nodes.edge_wear``).
Discrete pattern: raw object coordinates, never the periodic domain -- see
the tiling gotcha in docs/NEXT_STEPS.md."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        # Courses per meter along the slope's plan direction. env/house's
        # geometric rows land ~0.25 m apart (7 rows / ~1.8 m half-span), so
        # the default 4.0 lines the painted grid up with the geometry.
        "course_scale": {"type": "number", "minimum": 2.0, "maximum": 12.0, "default": 4.0},
        "roughness": {"type": "number", "minimum": 0.5, "maximum": 0.95, "default": 0.8},
        "weathering": {"type": "number", "minimum": 0.0, "maximum": 0.6, "default": 0.3},
        "edge_highlight": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5},
        # Explicit course colors ("#RRGGBB"). Empty -> sampled from the
        # theme's accent group. Requests whose description names a color
        # (e.g. "red shingled roof") pin these via material_overrides until
        # description-driven selection exists (docs/HOUSE_ROADMAP.md phase 1).
        "color1_hex": {"type": "string", "default": ""},
        "color2_hex": {"type": "string", "default": ""},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

# Tile aspect within the course grid: shingles ~2.2x wider than tall.
TILE_W, TILE_H, TILE_OFFSET = 2.2, 1.0, 0.5


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    ra, ga, ba = (palette.hex_to_rgb(params["color1_hex"]) if params.get("color1_hex")
                  else palette.sample_palette_color(palette_dict, "accent", rng))
    rb, gb, bb = (palette.hex_to_rgb(params["color2_hex"]) if params.get("color2_hex")
                  else palette.sample_palette_color(palette_dict, "accent", rng))

    # Shingle courses: a brick texture with row offset reads as overlapping
    # shingles at game-texture distance. Courses are horizontal rows stacked
    # along the slope (object Y), so Row Height 1.0 at Scale=courses/meter.
    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.offset = TILE_OFFSET
    brick.inputs["Scale"].default_value = params["course_scale"]
    brick.inputs["Brick Width"].default_value = TILE_W
    brick.inputs["Row Height"].default_value = TILE_H
    brick.inputs["Mortar Size"].default_value = 0.03
    brick.inputs["Mortar"].default_value = (0.028, 0.014, 0.012, 1.0)
    brick.inputs["Color1"].default_value = (ra, ga, ba, 1.0)
    brick.inputs["Color2"].default_value = (ra * 0.75 + rb * 0.25, ga * 0.75 + gb * 0.25,
                                            ba * 0.75 + bb * 0.25, 1.0)
    nt.links.new(tex_coord.outputs["Object"], brick.inputs["Vector"])

    # Per-tile painted variation (item 1): jitter cells on the SAME grid as
    # the brick above -- brick scales its input vector, cell_jitter takes the
    # pre-scaled one.
    scaled = nt.nodes.new("ShaderNodeVectorMath")
    scaled.operation = "SCALE"
    nt.links.new(tex_coord.outputs["Object"], scaled.inputs[0])
    scaled.inputs["Scale"].default_value = params["course_scale"]
    cells = nt.nodes.new("ShaderNodeGroup")
    cells.node_tree = nodes.cell_jitter()
    nt.links.new(scaled.outputs[0], cells.inputs["Vector"])
    cells.inputs["Brick Width"].default_value = TILE_W
    cells.inputs["Row Height"].default_value = TILE_H
    cells.inputs["Offset"].default_value = TILE_OFFSET
    cell_rgb = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(cells.outputs["Color"], cell_rgb.inputs["Color"])

    # per-tile hue shift between oxblood and rust (item 2)
    rust_fac = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(cell_rgb.outputs["Red"], rust_fac.inputs["Value"])
    rust_fac.inputs["To Min"].default_value = 0.0
    rust_fac.inputs["To Max"].default_value = 0.55
    rusted = nt.nodes.new("ShaderNodeMix")
    rusted.data_type = "RGBA"
    nt.links.new(rust_fac.outputs["Result"], rusted.inputs[0])
    nt.links.new(brick.outputs["Color"], rusted.inputs[6])
    rusted.inputs[7].default_value = (min(1.0, ra * 1.35 + 0.08), ga * 0.78 + 0.05,
                                      ba * 0.55 + 0.02, 1.0)

    # per-tile value jitter
    value_jit = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(cells.outputs["Fac"], value_jit.inputs["Value"])
    value_jit.inputs["To Min"].default_value = 0.80
    value_jit.inputs["To Max"].default_value = 1.15
    valued = nt.nodes.new("ShaderNodeMix")
    valued.data_type = "RGBA"
    valued.blend_type = "MULTIPLY"
    valued.inputs[0].default_value = 1.0
    nt.links.new(rusted.outputs[2], valued.inputs[6])
    nt.links.new(value_jit.outputs["Result"], valued.inputs[7])

    # Underside shadow (item 2): each course darkens toward its down-slope
    # edge. Down-slope = increasing |Y| on BOTH slopes, so the within-row
    # fraction of |y|*scale rises 0->1 toward the row's lower edge.
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tex_coord.outputs["Object"], sep.inputs["Vector"])
    abs_y = nt.nodes.new("ShaderNodeMath")
    abs_y.operation = "ABSOLUTE"
    nt.links.new(sep.outputs["Y"], abs_y.inputs[0])
    row_pos = nt.nodes.new("ShaderNodeMath")
    row_pos.operation = "MULTIPLY"
    nt.links.new(abs_y.outputs[0], row_pos.inputs[0])
    row_pos.inputs[1].default_value = params["course_scale"]
    row_frac = nt.nodes.new("ShaderNodeMath")
    row_frac.operation = "FRACT"
    nt.links.new(row_pos.outputs[0], row_frac.inputs[0])
    shadow = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(row_frac.outputs[0], shadow.inputs["Value"])
    shadow.inputs["From Min"].default_value = 0.55
    shadow.inputs["From Max"].default_value = 0.97
    shadow.inputs["To Min"].default_value = 1.0
    shadow.inputs["To Max"].default_value = 0.55
    shadowed = nt.nodes.new("ShaderNodeMix")
    shadowed.data_type = "RGBA"
    shadowed.blend_type = "MULTIPLY"
    shadowed.inputs[0].default_value = 1.0
    nt.links.new(valued.outputs[2], shadowed.inputs[6])
    nt.links.new(shadow.outputs["Result"], shadowed.inputs[7])

    grime = nt.nodes.new("ShaderNodeGroup")
    grime.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], grime.inputs["Vector"])
    grime.inputs["Scale"].default_value = 4.0
    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs[0].default_value = params["weathering"]
    nt.links.new(shadowed.outputs[2], darken.inputs[6])
    nt.links.new(grime.outputs["Fac"], darken.inputs[7])

    # Painted edge highlights (item 1): the course slabs are real geometry,
    # so edge_wear catches every proud eave/edge -- tint them lighter.
    edges = nt.nodes.new("ShaderNodeGroup")
    edges.node_tree = nodes.edge_wear()
    edges.inputs["Radius"].default_value = 0.025
    edges.inputs["Sharpness"].default_value = 0.55
    edge_fac = nt.nodes.new("ShaderNodeMath")
    edge_fac.operation = "MULTIPLY"
    nt.links.new(edges.outputs["Fac"], edge_fac.inputs[0])
    edge_fac.inputs[1].default_value = params["edge_highlight"]
    highlight = nt.nodes.new("ShaderNodeMix")
    highlight.data_type = "RGBA"
    nt.links.new(edge_fac.outputs[0], highlight.inputs[0])
    nt.links.new(darken.outputs[2], highlight.inputs[6])
    highlight.inputs[7].default_value = (min(1.0, ra * 1.55 + 0.20),
                                         min(1.0, ga * 1.35 + 0.14),
                                         min(1.0, ba * 1.15 + 0.10), 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.22
    nt.links.new(brick.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(highlight.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
