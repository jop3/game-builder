"""themes/greek_arena/materials/marble_white -- aged warm Greek marble.

Weathered ivory/cream (Pentelic-ish, honey-warm — not chalk white), veined by TWO
crossing systems: a broad cool-grey vein network and a finer, subtler warm/gold
vein, each a distorted BANDS wave sharpened to thin ridges (a pow() falloff around
the mid-crossing) so they read as marble veining, not stripes. A low-frequency
weathering mottle unsettles the base value; roughness is polished-but-aged and a
hair higher in the veins. The veins + mottle give S16 variance -- no flat_color.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.12, "maximum": 0.6, "default": 0.30},
        "vein_scale": {"type": "number", "minimum": 1.0, "maximum": 5.0, "default": 2.2},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False


def _vein_wave(nt, tex, scale, distortion, direction, detail_scale):
    """A distorted BANDS wave sharpened into a thin-ridge vein mask (0..1)."""
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = direction
    wave.wave_profile = "SIN"
    nt.links.new(tex.outputs["Object"], wave.inputs["Vector"])
    wave.inputs["Scale"].default_value = scale
    wave.inputs["Distortion"].default_value = distortion
    wave.inputs["Detail"].default_value = 3.0
    wave.inputs["Detail Scale"].default_value = detail_scale
    wave.inputs["Detail Roughness"].default_value = 0.6
    # line = (1 - 2*|Fac-0.5|) ^ 5  -> narrow bright ridge at each mid-crossing
    sub = nt.nodes.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
    nt.links.new(wave.outputs["Fac"], sub.inputs[0]); sub.inputs[1].default_value = 0.5
    ab = nt.nodes.new("ShaderNodeMath"); ab.operation = "ABSOLUTE"
    nt.links.new(sub.outputs["Value"], ab.inputs[0])
    m2 = nt.nodes.new("ShaderNodeMath"); m2.operation = "MULTIPLY"
    nt.links.new(ab.outputs["Value"], m2.inputs[0]); m2.inputs[1].default_value = 2.0
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    nt.links.new(m2.outputs["Value"], inv.inputs[1])
    line = nt.nodes.new("ShaderNodeMath"); line.operation = "POWER"
    nt.links.new(inv.outputs["Value"], line.inputs[0]); line.inputs[1].default_value = 5.0
    return line


def _mix_rgba(nt, fac_out, a_out, b_col):
    """mix a_out toward constant colour b_col by scalar fac_out; returns node."""
    m = nt.nodes.new("ShaderNodeMix"); m.data_type = "RGBA"
    nt.links.new(fac_out, m.inputs[0])
    nt.links.new(a_out, m.inputs[6])
    rgb = nt.nodes.new("ShaderNodeRGB"); rgb.outputs[0].default_value = b_col
    nt.links.new(rgb.outputs[0], m.inputs[7])
    return m


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex = nt.nodes.new("ShaderNodeTexCoord")

    # two crossing vein systems: broad grey (X) + finer gold (Y)
    line_grey = _vein_wave(nt, tex, params["vein_scale"], 12.0, "X", 1.4)
    line_gold = _vein_wave(nt, tex, params["vein_scale"] * 1.9, 9.0, "Y", 1.1)
    # damp the gold so it's a subtle secondary
    gold_fac = nt.nodes.new("ShaderNodeMath"); gold_fac.operation = "MULTIPLY"
    nt.links.new(line_gold.outputs["Value"], gold_fac.inputs[0]); gold_fac.inputs[1].default_value = 0.55

    # weathered base: warm ivory unsettled by a low-frequency mottle
    mottle = nt.nodes.new("ShaderNodeGroup")
    mottle.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], mottle.inputs["Vector"])
    mottle.inputs["Scale"].default_value = 3.5
    mottle.inputs["Contrast"].default_value = 1.0
    cream = nt.nodes.new("ShaderNodeRGB")
    cream.outputs[0].default_value = (0.80, 0.75, 0.67, 1.0)      # aged warm ivory
    mval = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(mottle.outputs["Fac"], mval.inputs["Value"])
    mval.inputs["To Min"].default_value = 0.82        # more visible weathering
    mval.inputs["To Max"].default_value = 1.08
    base = nt.nodes.new("ShaderNodeMix"); base.data_type = "RGBA"; base.blend_type = "MULTIPLY"
    base.inputs[0].default_value = 1.0
    nt.links.new(cream.outputs[0], base.inputs[6])
    nt.links.new(mval.outputs["Result"], base.inputs[7])

    # lay the grey veins, then the subtle gold veins on top
    grey = _mix_rgba(nt, line_grey.outputs["Value"], base.outputs[2], (0.42, 0.40, 0.38, 1.0))
    gold = _mix_rgba(nt, gold_fac.outputs["Value"], grey.outputs[2], (0.55, 0.46, 0.32, 1.0))
    nt.links.new(gold.outputs[2], bsdf.inputs["Base Color"])

    # veins sit a hair rougher; polished-but-aged elsewhere
    veinmax = nt.nodes.new("ShaderNodeMath"); veinmax.operation = "MAXIMUM"
    nt.links.new(line_grey.outputs["Value"], veinmax.inputs[0])
    nt.links.new(gold_fac.outputs["Value"], veinmax.inputs[1])
    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(veinmax.outputs["Value"], rough.inputs["Value"])
    rough.inputs["To Min"].default_value = params["roughness"]
    rough.inputs["To Max"].default_value = min(params["roughness"] + 0.16, 1.0)
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.10
    nt.links.new(veinmax.outputs["Value"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Metallic"].default_value = 0.0
