"""themes/fantasy_medieval/materials/fantasy_cloth_banner -- dyed cloth
banner/tabard fabric with the painted look (docs/COLOR_WAVE.md item 5):
matte weave broken up by noise, per-patch value jitter keyed to a coarse
cell grid (``matlib.nodes.cell_jitter`` -- reads as unevenly dyed cloth
panels), and a sun-fade gradient toward the top via the Object-Z height-mask
trick from fantasy_aged_wood (dye fades where the sun hits; the hem keeps
its color). Discrete jitter on raw object coordinates; the fade and weave
are continuous but this recipe is non-tiling, so no periodic domain."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "weave_scale": {"type": "number", "minimum": 30.0, "maximum": 120.0, "default": 60.0},
        "roughness": {"type": "number", "minimum": 0.5, "maximum": 0.95, "default": 0.8},
        # Sun-fade strength at the banner's top (0 disables).
        "sun_fade": {"type": "number", "minimum": 0.0, "maximum": 0.8, "default": 0.4},
        # Explicit dye color ("#RRGGBB"), e.g. description-driven
        # (docs/COLOR_WAVE.md item 1). Empty -> sampled from ``accent``.
        "color1_hex": {"type": "string", "default": ""},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

# Dye-patch cell size in meters: hand-dyed cloth varies in ~15 cm patches.
PATCH_W, PATCH_H, PATCH_OFFSET = 0.15, 0.15, 0.5
# Sun-fade height window (Object Z, meters): fade ramps in from the banner's
# lower third and peaks by ~1.6 m -- proportionate on prop-scale banners.
FADE_Z_MIN, FADE_Z_MAX = 0.4, 1.6


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    weave = nt.nodes.new("ShaderNodeGroup")
    weave.node_tree = nodes.noise_breakup()
    nt.links.new(tex_coord.outputs["Object"], weave.inputs["Vector"])
    weave.inputs["Scale"].default_value = params["weave_scale"]

    r, g, b = (palette.hex_to_rgb(params["color1_hex"]) if params.get("color1_hex")
               else palette.sample_palette_color(palette_dict, "accent", rng))
    base = nt.nodes.new("ShaderNodeRGB")
    base.outputs[0].default_value = (r, g, b, 1.0)

    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 0.2
    nt.links.new(base.outputs[0], tint.inputs[6])
    nt.links.new(weave.outputs["Fac"], tint.inputs[7])

    # Per-patch dye value jitter (item 5): one stable random value per
    # coarse cell -- hand-dyed panels, not a flat printed sheet.
    cells = nt.nodes.new("ShaderNodeGroup")
    cells.node_tree = nodes.cell_jitter()
    nt.links.new(tex_coord.outputs["Object"], cells.inputs["Vector"])
    cells.inputs["Brick Width"].default_value = PATCH_W
    cells.inputs["Row Height"].default_value = PATCH_H
    cells.inputs["Offset"].default_value = PATCH_OFFSET
    value_jit = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(cells.outputs["Fac"], value_jit.inputs["Value"])
    value_jit.inputs["To Min"].default_value = 0.88
    value_jit.inputs["To Max"].default_value = 1.10
    valued = nt.nodes.new("ShaderNodeMix")
    valued.data_type = "RGBA"
    valued.blend_type = "MULTIPLY"
    valued.inputs[0].default_value = 1.0
    nt.links.new(tint.outputs[2], valued.inputs[6])
    nt.links.new(value_jit.outputs["Result"], valued.inputs[7])

    # Sun-fade gradient (item 5, the aged-wood height-mask trick inverted):
    # fade factor rises with Object Z, mixing toward a lighter desaturated
    # version of the dye -- broken up by low-frequency noise so the fade
    # line isn't a hard horizon.
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tex_coord.outputs["Object"], sep.inputs["Vector"])
    fade_mask = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(sep.outputs["Z"], fade_mask.inputs["Value"])
    fade_mask.inputs["From Min"].default_value = FADE_Z_MIN
    fade_mask.inputs["From Max"].default_value = FADE_Z_MAX
    fade_break = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(tex_coord.outputs["Object"], fade_break.inputs["Vector"])
    fade_break.inputs["Scale"].default_value = 3.0
    fade_break.inputs["Detail"].default_value = 2.0
    fade_var = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(fade_break.outputs["Fac"], fade_var.inputs["Value"])
    fade_var.inputs["To Min"].default_value = 0.7
    fade_var.inputs["To Max"].default_value = 1.0
    fade_fac = nt.nodes.new("ShaderNodeMath")
    fade_fac.operation = "MULTIPLY"
    nt.links.new(fade_mask.outputs["Result"], fade_fac.inputs[0])
    nt.links.new(fade_var.outputs["Result"], fade_fac.inputs[1])
    fade_amt = nt.nodes.new("ShaderNodeMath")
    fade_amt.operation = "MULTIPLY"
    nt.links.new(fade_fac.outputs[0], fade_amt.inputs[0])
    fade_amt.inputs[1].default_value = params["sun_fade"]
    faded = nt.nodes.new("ShaderNodeMix")
    faded.data_type = "RGBA"
    nt.links.new(fade_amt.outputs[0], faded.inputs[0])
    nt.links.new(valued.outputs[2], faded.inputs[6])
    faded.inputs[7].default_value = (min(1.0, r * 0.65 + 0.33),
                                     min(1.0, g * 0.65 + 0.33),
                                     min(1.0, b * 0.65 + 0.30), 1.0)

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    nt.links.new(weave.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(faded.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
