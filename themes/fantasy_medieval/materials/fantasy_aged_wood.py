"""themes/fantasy_medieval/materials/fantasy_aged_wood -- weathered timber
planking with painted-in lighting (docs/TEXTURE_WAVE.md items 1+3): streaky
vertical grain, per-plank value/hue jitter keyed to the plank grid
(``matlib.nodes.cell_jitter``), occasional near-black boards, grunge and a
value gradient that both strengthen toward the ground, and painted edge
highlights (``matlib.nodes.edge_wear``) on every corner/chamfer."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "ring_scale": {"type": "number", "minimum": 8.0, "maximum": 30.0, "default": 16.0},
        "roughness": {"type": "number", "minimum": 0.4, "maximum": 0.85, "default": 0.65},
        # Painted edge-highlight intensity (0 disables the read entirely).
        "edge_highlight": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.55},
        # Per-plank white-noise channel threshold below which a board goes
        # near-black (the reference's occasional tarred/shadow boards).
        "dark_board_chance": {"type": "number", "minimum": 0.0, "maximum": 0.35,
                              "default": 0.12},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

# Plank grid constants shared by the seam brick texture and the per-plank
# jitter cells -- they MUST match or the jitter bleeds across seams.
PLANK_W, PLANK_ROW_H, PLANK_OFFSET = 0.34, 6.0, 0.5


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tex_coord.outputs["Object"], sep.inputs["Vector"])

    # Plank seams: a stretched brick grid in a swizzled object space gives
    # vertical boards with sparse horizontal joints on every wall
    # orientation -- discrete pattern on raw coordinates (docs/NEXT_STEPS.md:
    # never route brick/grid through the periodic domain).
    across = nt.nodes.new("ShaderNodeMath")
    across.operation = "ADD"
    nt.links.new(sep.outputs["X"], across.inputs[0])
    nt.links.new(sep.outputs["Y"], across.inputs[1])
    swizzle = nt.nodes.new("ShaderNodeCombineXYZ")
    nt.links.new(across.outputs[0], swizzle.inputs["X"])
    nt.links.new(sep.outputs["Z"], swizzle.inputs["Y"])
    planks = nt.nodes.new("ShaderNodeTexBrick")
    planks.offset = PLANK_OFFSET
    planks.inputs["Scale"].default_value = 1.0
    planks.inputs["Brick Width"].default_value = PLANK_W
    planks.inputs["Row Height"].default_value = PLANK_ROW_H
    planks.inputs["Mortar Size"].default_value = 0.012
    planks.inputs["Mortar"].default_value = (0.02, 0.012, 0.008, 1.0)
    planks.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    planks.inputs["Color2"].default_value = (0.82, 0.78, 0.74, 1.0)
    nt.links.new(swizzle.outputs["Vector"], planks.inputs["Vector"])

    # Both color draws from ``primary`` (the theme's dark browns): the old
    # primary/secondary mix let the light grey dominate and walls rendered
    # plaster-pale next to the reference's chocolate planks.
    ra, ga, ba = palette.sample_palette_color(palette_dict, "primary", rng)
    rb, gb, bb = palette.sample_palette_color(palette_dict, "primary", rng)

    # Streaky vertical grain (TEXTURE_WAVE item 3): noise sampled in the
    # swizzled plank domain with Z compressed hard, so features elongate
    # along the boards instead of the old isotropic mid-brown mush.
    streak_vec = nt.nodes.new("ShaderNodeVectorMath")
    streak_vec.operation = "MULTIPLY"
    streak_vec.inputs[1].default_value = (1.0, 0.13, 1.0)
    nt.links.new(swizzle.outputs["Vector"], streak_vec.inputs[0])
    streaks = nt.nodes.new("ShaderNodeTexNoise")
    streaks.inputs["Scale"].default_value = params["ring_scale"] * 0.5
    streaks.inputs["Detail"].default_value = 5.0
    streaks.inputs["Roughness"].default_value = 0.62
    nt.links.new(streak_vec.outputs[0], streaks.inputs["Vector"])
    streak_ramp = nt.nodes.new("ShaderNodeValToRGB")
    streak_ramp.color_ramp.elements[0].position = 0.32
    streak_ramp.color_ramp.elements[1].position = 0.68
    nt.links.new(streaks.outputs["Fac"], streak_ramp.inputs["Fac"])

    wood = nt.nodes.new("ShaderNodeMix")
    wood.data_type = "RGBA"
    nt.links.new(streak_ramp.outputs["Color"], wood.inputs[0])
    wood.inputs[6].default_value = (ra * 0.5, ga * 0.5, ba * 0.5, 1.0)
    wood.inputs[7].default_value = (rb, gb, bb, 1.0)

    # Per-plank painted variation (item 1): one white-noise draw per plank
    # cell -- same grid constants as the seam brick above.
    cells = nt.nodes.new("ShaderNodeGroup")
    cells.node_tree = nodes.cell_jitter()
    nt.links.new(swizzle.outputs["Vector"], cells.inputs["Vector"])
    cells.inputs["Brick Width"].default_value = PLANK_W
    cells.inputs["Row Height"].default_value = PLANK_ROW_H
    cells.inputs["Offset"].default_value = PLANK_OFFSET
    cell_rgb = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(cells.outputs["Color"], cell_rgb.inputs["Color"])

    # warm/cool alternation between neighboring boards
    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.inputs[6].default_value = (1.07, 0.99, 0.90, 1.0)  # warm
    tint.inputs[7].default_value = (0.92, 0.99, 1.06, 1.0)  # cool
    nt.links.new(cell_rgb.outputs["Red"], tint.inputs[0])
    tinted = nt.nodes.new("ShaderNodeMix")
    tinted.data_type = "RGBA"
    tinted.blend_type = "MULTIPLY"
    tinted.inputs[0].default_value = 1.0
    nt.links.new(wood.outputs[2], tinted.inputs[6])
    nt.links.new(tint.outputs[2], tinted.inputs[7])

    # per-plank value jitter
    value_jit = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(cells.outputs["Fac"], value_jit.inputs["Value"])
    value_jit.inputs["To Min"].default_value = 0.78
    value_jit.inputs["To Max"].default_value = 1.14
    valued = nt.nodes.new("ShaderNodeMix")
    valued.data_type = "RGBA"
    valued.blend_type = "MULTIPLY"
    valued.inputs[0].default_value = 1.0
    nt.links.new(tinted.outputs[2], valued.inputs[6])
    nt.links.new(value_jit.outputs["Result"], valued.inputs[7])

    # occasional near-black boards
    dark_pick = nt.nodes.new("ShaderNodeMath")
    dark_pick.operation = "LESS_THAN"
    nt.links.new(cell_rgb.outputs["Green"], dark_pick.inputs[0])
    dark_pick.inputs[1].default_value = params["dark_board_chance"]
    dark_boards = nt.nodes.new("ShaderNodeMix")
    dark_boards.data_type = "RGBA"
    dark_boards.blend_type = "MULTIPLY"
    nt.links.new(dark_pick.outputs[0], dark_boards.inputs[0])
    nt.links.new(valued.outputs[2], dark_boards.inputs[6])
    dark_boards.inputs[7].default_value = (0.30, 0.28, 0.27, 1.0)

    # plank seam lines
    seamed = nt.nodes.new("ShaderNodeMix")
    seamed.data_type = "RGBA"
    seamed.blend_type = "MULTIPLY"
    seamed.inputs[0].default_value = 1.0
    nt.links.new(dark_boards.outputs[2], seamed.inputs[6])
    nt.links.new(planks.outputs["Color"], seamed.inputs[7])

    # Height mask: 1 at the ground, fading out by ~1.1 m up -- drives the
    # bottom-third grunge boost AND the painted darker-toward-ground gradient.
    ground_mask = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(sep.outputs["Z"], ground_mask.inputs["Value"])
    ground_mask.inputs["From Min"].default_value = 0.0
    ground_mask.inputs["From Max"].default_value = 1.1
    ground_mask.inputs["To Min"].default_value = 1.0
    ground_mask.inputs["To Max"].default_value = 0.0

    grime = nt.nodes.new("ShaderNodeGroup")
    grime.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], grime.inputs["Vector"])
    grime.inputs["Scale"].default_value = 5.0
    grime_fac = nt.nodes.new("ShaderNodeMath")
    grime_fac.operation = "MULTIPLY_ADD"
    nt.links.new(ground_mask.outputs["Result"], grime_fac.inputs[0])
    grime_fac.inputs[1].default_value = 0.45
    grime_fac.inputs[2].default_value = 0.30
    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    nt.links.new(grime_fac.outputs[0], darken.inputs[0])
    nt.links.new(seamed.outputs[2], darken.inputs[6])
    nt.links.new(grime.outputs["Fac"], darken.inputs[7])

    # painted value gradient: darker toward the ground even where grunge is thin
    grad_value = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(ground_mask.outputs["Result"], grad_value.inputs["Value"])
    grad_value.inputs["To Min"].default_value = 1.0
    grad_value.inputs["To Max"].default_value = 0.68
    graded = nt.nodes.new("ShaderNodeMix")
    graded.data_type = "RGBA"
    graded.blend_type = "MULTIPLY"
    graded.inputs[0].default_value = 1.0
    nt.links.new(darken.outputs[2], graded.inputs[6])
    nt.links.new(grad_value.outputs["Result"], graded.inputs[7])

    # Painted edge highlights (item 1): bright warm tint through the
    # edge_wear convex-edge mask, applied LAST so it reads over the grime.
    edges = nt.nodes.new("ShaderNodeGroup")
    edges.node_tree = nodes.edge_wear()
    edges.inputs["Radius"].default_value = 0.02
    edges.inputs["Sharpness"].default_value = 0.55
    edge_fac = nt.nodes.new("ShaderNodeMath")
    edge_fac.operation = "MULTIPLY"
    nt.links.new(edges.outputs["Fac"], edge_fac.inputs[0])
    edge_fac.inputs[1].default_value = params["edge_highlight"]
    highlight = nt.nodes.new("ShaderNodeMix")
    highlight.data_type = "RGBA"
    nt.links.new(edge_fac.outputs[0], highlight.inputs[0])
    nt.links.new(graded.outputs[2], highlight.inputs[6])
    highlight.inputs[7].default_value = (min(1.0, rb * 1.7 + 0.24),
                                         min(1.0, gb * 1.6 + 0.20),
                                         min(1.0, bb * 1.4 + 0.14), 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    nt.links.new(streaks.outputs["Fac"], bump.inputs["Height"])
    plank_bump = nt.nodes.new("ShaderNodeBump")
    plank_bump.inputs["Strength"].default_value = 0.3
    nt.links.new(planks.outputs["Fac"], plank_bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], plank_bump.inputs["Normal"])

    nt.links.new(highlight.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(plank_bump.outputs["Normal"], bsdf.inputs["Normal"])
