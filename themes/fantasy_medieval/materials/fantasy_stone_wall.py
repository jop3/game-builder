"""themes/fantasy_medieval/materials/fantasy_stone_wall -- grey cobblestone
with visible cells, dark grout and a moss accent (docs/TEXTURE_WAVE.md item
5). The old build routed everything through ``matlib.nodes.stone_base``,
whose distance-to-edge darkening is strongest at cell CENTERS (fac ~= 0 at
the edges), so grout never read and the plinth rendered flat and sandy; this
recipe builds its own voronoi graph instead: explicit grout mask, per-cell
value/hue jitter from the voronoi cell color, and a noise-masked mix toward
a desaturated green derived from the sampled stone grey (the palette has no
green group -- deriving from ``secondary`` keeps it subtle and traceable).
Still TILING: every pattern input routes through the periodic domain
(spec 10.3)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "cell_scale": {"type": "number", "minimum": 3.0, "maximum": 14.0, "default": 6.0},
        "moss": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
        # Explicit stone color ("#RRGGBB"), e.g. description-driven
        # (docs/COLOR_WAVE.md item 1). Empty -> sampled from ``secondary``.
        # (No color2_hex: this recipe derives its own darker tones.)
        "color1_hex": {"type": "string", "default": ""},
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

    # ``secondary`` holds the theme's stone greys; ``primary`` is the timber
    # browns and made stonework read as warm sand (house plinth, phase 4).
    # Darkened toward the reference's grey cobbles.
    r, g, b = (palette.hex_to_rgb(params["color1_hex"]) if params.get("color1_hex")
               else palette.sample_palette_color(palette_dict, "secondary", rng))
    base = (r * 0.72, g * 0.74, b * 0.78)

    # Cobble cells: distance-to-edge -> explicit grout mask (1 IN the grout).
    vor_edge = nt.nodes.new("ShaderNodeTexVoronoi")
    vor_edge.feature = "DISTANCE_TO_EDGE"
    nt.links.new(periodic.outputs["Vector"], vor_edge.inputs["Vector"])
    vor_edge.inputs["Scale"].default_value = params["cell_scale"]
    grout_mask = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(vor_edge.outputs["Distance"], grout_mask.inputs["Value"])
    # From Max 0.085 (was 0.07): the grout thinned to invisibility after the
    # web shrink pass -- slightly wider lines survive it (COLOR_WAVE item 3).
    grout_mask.inputs["From Min"].default_value = 0.01
    grout_mask.inputs["From Max"].default_value = 0.085
    grout_mask.inputs["To Min"].default_value = 1.0
    grout_mask.inputs["To Max"].default_value = 0.0

    # Per-cell value + subtle hue variation: F1 voronoi's Color output is one
    # stable random color per cell (same scale -> same cells as the edges).
    vor_cell = nt.nodes.new("ShaderNodeTexVoronoi")
    vor_cell.feature = "F1"
    nt.links.new(periodic.outputs["Vector"], vor_cell.inputs["Vector"])
    vor_cell.inputs["Scale"].default_value = params["cell_scale"]
    cell_rgb = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(vor_cell.outputs["Color"], cell_rgb.inputs["Color"])

    value_jit = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(cell_rgb.outputs["Red"], value_jit.inputs["Value"])
    value_jit.inputs["To Min"].default_value = 0.72
    value_jit.inputs["To Max"].default_value = 1.18
    stone_color = nt.nodes.new("ShaderNodeMix")
    stone_color.data_type = "RGBA"
    stone_color.blend_type = "MULTIPLY"
    stone_color.inputs[0].default_value = 1.0
    stone_color.inputs[6].default_value = (base[0], base[1], base[2], 1.0)
    nt.links.new(value_jit.outputs["Result"], stone_color.inputs[7])

    # warm/cool alternation between neighboring cobbles (painted-look)
    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.inputs[6].default_value = (1.06, 1.0, 0.92, 1.0)
    tint.inputs[7].default_value = (0.94, 0.99, 1.08, 1.0)
    nt.links.new(cell_rgb.outputs["Green"], tint.inputs[0])
    tinted = nt.nodes.new("ShaderNodeMix")
    tinted.data_type = "RGBA"
    tinted.blend_type = "MULTIPLY"
    tinted.inputs[0].default_value = 1.0
    nt.links.new(stone_color.outputs[2], tinted.inputs[6])
    nt.links.new(tint.outputs[2], tinted.inputs[7])

    # Moss accent: layered-noise mask (periodic domain, stays seamless) mixes
    # toward a desaturated green built FROM the sampled grey.
    # Scale 2.0 (was 2.6): bigger moss patches so the accent still reads
    # after the web shrink pass (COLOR_WAVE item 3).
    moss_noise = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(periodic.outputs["Vector"], moss_noise.inputs["Vector"])
    moss_noise.inputs["Scale"].default_value = 2.0
    moss_noise.inputs["Detail"].default_value = 4.0
    moss_ramp = nt.nodes.new("ShaderNodeValToRGB")
    moss_ramp.color_ramp.elements[0].position = 0.52
    moss_ramp.color_ramp.elements[1].position = 0.72
    nt.links.new(moss_noise.outputs["Fac"], moss_ramp.inputs["Fac"])
    moss_fac = nt.nodes.new("ShaderNodeMath")
    moss_fac.operation = "MULTIPLY"
    nt.links.new(moss_ramp.outputs["Color"], moss_fac.inputs[0])
    moss_fac.inputs[1].default_value = params["moss"] * 0.8
    mossed = nt.nodes.new("ShaderNodeMix")
    mossed.data_type = "RGBA"
    nt.links.new(moss_fac.outputs[0], mossed.inputs[0])
    nt.links.new(tinted.outputs[2], mossed.inputs[6])
    mossed.inputs[7].default_value = (base[0] * 0.5, base[1] * 0.72,
                                      base[2] * 0.38, 1.0)

    # Dark grout lines over everything (moss survives only on the stones).
    grout = nt.nodes.new("ShaderNodeMix")
    grout.data_type = "RGBA"
    nt.links.new(grout_mask.outputs["Result"], grout.inputs[0])
    nt.links.new(mossed.outputs[2], grout.inputs[6])
    grout.inputs[7].default_value = (0.09, 0.082, 0.075, 1.0)

    # Roughness: grout roughest, cobbles vary with the breakup mask.
    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = nodes.noise_breakup()
    nt.links.new(periodic.outputs["Vector"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 14.0
    rough_var = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], rough_var.inputs["Value"])
    rough_var.inputs["To Min"].default_value = 0.55
    rough_var.inputs["To Max"].default_value = 0.9
    rough = nt.nodes.new("ShaderNodeMix")
    rough.data_type = "FLOAT"
    nt.links.new(grout_mask.outputs["Result"], rough.inputs[0])
    nt.links.new(rough_var.outputs["Result"], rough.inputs[2])
    rough.inputs[3].default_value = 0.95

    # Cobbles bulge: edge distance is a natural dome height per cell.
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.45
    nt.links.new(vor_edge.outputs["Distance"], bump.inputs["Height"])

    nt.links.new(grout.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(rough.outputs[0], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
