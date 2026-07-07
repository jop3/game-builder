"""themes/fantasy_medieval/materials/fantasy_window_glow -- warm lamplit
window glass (docs/TEXTURE_WAVE.md item 4). The baked emissive PNG is the
final glow everywhere downstream (8-bit, clamped at 1.0, and export_gltf
pins Emission Strength to 1.0), so this recipe SHAPES the map instead of
multiplying a big strength through it (the old strength-4 bake clamped the
whole pane to hueless white): a saturated warm gold base, a noise-based
per-pane center bias (real per-part coords are unavailable -- the shared
material sees whole-house Object coords, see HOUSE_ROADMAP phase 1) whose
peaks clamp into small white-hot cores, and an Ambient Occlusion node that
paints the REAL mullion cross + frame recess into the emissive as dark bars
(the mullion geometry occludes the pane surface it crosses), so the glow
reads as panes even at LOD distance. Declares an emissive bake so assets
carrying it produce ``emissive.png`` (spec 10.3 step 4)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        # Overall map brightness: 4.0 bakes the pane peak at full scale
        # (values above 1 clamp into the white-hot cores by design).
        "glow_strength": {"type": "number", "minimum": 0.5, "maximum": 10.0, "default": 4.0},
        "pane_scale": {"type": "number", "minimum": 2.0, "maximum": 24.0, "default": 6.0},
        # Extra warm shift toward lamplight orange on top of the base warm
        # bias (COLOR_WAVE item 4 / accessories: a lantern slot pins ~1.0
        # so its glass reads as flame, not the windows' pale gold).
        "warmth": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
        # How strongly the AO mullion/frame bars darken the emissive.
        # 1.0 = the window look. A lantern pane is small enough that its
        # cage/bracket AO would dim the WHOLE pane grey -- its slot pins 0.
        "bar_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0},
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

    # Warm-biased draw from the emissive group: pull green/blue down so the
    # glow reads lamplit gold-orange, not pale yellow, in dark shots.
    # ``warmth`` pulls green/blue down further toward flame orange.
    r, g, b = palette.sample_palette_color(palette_dict, "emissive", rng)
    w = params.get("warmth", 0.0)
    warm = (min(1.0, r * 1.05), g * 0.82 * (1.0 - 0.30 * w), b * 0.5 * (1.0 - 0.65 * w))

    # Soft interior unevenness so the glow reads as lamplight through glass,
    # not a uniform LED panel (theme brief: torch-lit, hand-built).
    unevenness = nt.nodes.new("ShaderNodeTexNoise")
    unevenness.inputs["Scale"].default_value = params["pane_scale"]
    unevenness.inputs["Detail"].default_value = 2.0
    nt.links.new(tex_coord.outputs["Object"], unevenness.inputs["Vector"])
    uneven_var = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(unevenness.outputs["Fac"], uneven_var.inputs["Value"])
    uneven_var.inputs["To Min"].default_value = 0.72
    uneven_var.inputs["To Max"].default_value = 1.0

    # Per-pane radial vignette, noise approximation: a window-sized noise
    # whose bright patches land somewhere on each pane and whose peaks
    # (>1 after remap) clamp into small white-hot lamp cores.
    center_bias = nt.nodes.new("ShaderNodeTexNoise")
    center_bias.inputs["Scale"].default_value = 1.4
    center_bias.inputs["Detail"].default_value = 1.0
    nt.links.new(tex_coord.outputs["Object"], center_bias.inputs["Vector"])
    # To Max 1.2 (was 1.35): the dormer pane sat on a bias peak and its
    # whole pane clamped paler than the main windows -- narrower bias keeps
    # pane cores gold with only small white-hot spots (COLOR_WAVE item 4).
    bias_var = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(center_bias.outputs["Fac"], bias_var.inputs["Value"])
    bias_var.inputs["To Min"].default_value = 0.62
    bias_var.inputs["To Max"].default_value = 1.2

    # Mullion cross + frame recess as dark bars: the cross bars and frame are
    # real geometry over the pane, so tight-radius AO darkens exactly the
    # texels they cover. Sharpened so the bars read painted, not smoky.
    ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = 0.12
    ao_sharp = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(ao.outputs["AO"], ao_sharp.inputs["Value"])
    ao_sharp.inputs["From Min"].default_value = 0.35
    ao_sharp.inputs["From Max"].default_value = 0.85
    ao_sharp.inputs["To Min"].default_value = 0.08
    ao_sharp.inputs["To Max"].default_value = 1.0
    # bar_strength fades the bars out: lerp(1, sharpened AO, bar_strength).
    bars = nt.nodes.new("ShaderNodeMix")
    bars.data_type = "FLOAT"
    bars.inputs[0].default_value = params.get("bar_strength", 1.0)
    bars.inputs[2].default_value = 1.0
    nt.links.new(ao_sharp.outputs["Result"], bars.inputs[3])

    brightness = nt.nodes.new("ShaderNodeMath")
    brightness.operation = "MULTIPLY"
    nt.links.new(uneven_var.outputs["Result"], brightness.inputs[0])
    nt.links.new(bias_var.outputs["Result"], brightness.inputs[1])
    barred = nt.nodes.new("ShaderNodeMath")
    barred.operation = "MULTIPLY"
    nt.links.new(brightness.outputs[0], barred.inputs[0])
    nt.links.new(bars.outputs[0], barred.inputs[1])
    # glow_strength/4: the default bakes the pane peak at full map scale;
    # higher values push more of the pane into the clamped white core.
    strength = nt.nodes.new("ShaderNodeMath")
    strength.operation = "MULTIPLY"
    nt.links.new(barred.outputs[0], strength.inputs[0])
    strength.inputs[1].default_value = params["glow_strength"] / 4.0

    glow_color = nt.nodes.new("ShaderNodeMix")
    glow_color.data_type = "RGBA"
    glow_color.blend_type = "MULTIPLY"
    glow_color.inputs[0].default_value = 1.0
    glow_color.inputs[6].default_value = (warm[0], warm[1], warm[2], 1.0)
    nt.links.new(strength.outputs[0], glow_color.inputs[7])

    # Albedo: warm parchment (glass reads warm even unlit); emission carries
    # the glow. Slightly rough dielectric.
    bsdf.inputs["Base Color"].default_value = (min(1.0, r * 0.9 + 0.1),
                                               min(1.0, g * 0.85 + 0.1),
                                               min(1.0, b * 0.7 + 0.1), 1.0)
    bsdf.inputs["Roughness"].default_value = 0.35
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(glow_color.outputs[2], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 1.0
