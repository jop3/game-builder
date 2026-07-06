"""themes/fantasy_medieval/materials/fantasy_window_glow -- warm lamplit
window glass: a parchment-warm surface with a soft emissive interior glow
sampled from the theme's ``emissive`` palette group (torch-lit, spec 7's
"torch-lit emissive glow rather than sci-fi strip lighting"). Declares an
emissive bake so assets carrying it produce ``emissive.png`` (spec 10.3
step 4)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "glow_strength": {"type": "number", "minimum": 0.5, "maximum": 6.0, "default": 3.0},
        "pane_scale": {"type": "number", "minimum": 2.0, "maximum": 12.0, "default": 6.0},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm", "emissive"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    r, g, b = palette.sample_palette_color(palette_dict, "emissive", rng)

    # Soft interior unevenness so the glow reads as lamplight through glass,
    # not a uniform LED panel (theme brief: torch-lit, hand-built).
    unevenness = nt.nodes.new("ShaderNodeTexNoise")
    unevenness.inputs["Scale"].default_value = params["pane_scale"]
    unevenness.inputs["Detail"].default_value = 2.0
    nt.links.new(tex_coord.outputs["Object"], unevenness.inputs["Vector"])

    glow_var = nt.nodes.new("ShaderNodeMapRange")
    glow_var.inputs["From Min"].default_value = 0.0
    glow_var.inputs["From Max"].default_value = 1.0
    glow_var.inputs["To Min"].default_value = 0.65
    glow_var.inputs["To Max"].default_value = 1.0
    nt.links.new(unevenness.outputs["Fac"], glow_var.inputs["Value"])

    glow_color = nt.nodes.new("ShaderNodeMix")
    glow_color.data_type = "RGBA"
    glow_color.blend_type = "MULTIPLY"
    glow_color.inputs[0].default_value = 1.0
    glow_color.inputs[6].default_value = (r, g, b, 1.0)
    nt.links.new(glow_var.outputs["Result"], glow_color.inputs[7])

    # Albedo: warm parchment (glass reads warm even unlit); emission carries
    # the glow. Slightly rough dielectric.
    bsdf.inputs["Base Color"].default_value = (min(1.0, r * 0.9 + 0.1),
                                               min(1.0, g * 0.85 + 0.1),
                                               min(1.0, b * 0.7 + 0.1), 1.0)
    bsdf.inputs["Roughness"].default_value = 0.35
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(glow_color.outputs[2], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = params["glow_strength"]
