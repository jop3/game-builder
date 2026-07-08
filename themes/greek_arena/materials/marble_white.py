"""themes/greek_arena/materials/marble_white -- polished white Greek marble.

A warm cream base veined with thin cool-grey streaks. The veins come from a
distorted BANDS wave texture sharpened to narrow ridges (a pow() falloff around
each mid-crossing) rather than a broad gradient, so they read as marble veining
and not as stripes. A low-frequency mottle varies the base value and a light
bump lifts the veins; roughness is low (polished) but a hair higher in the
veins. The vein area gives natural S16 variance -- no flat_color needed.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.12, "maximum": 0.5, "default": 0.24},
        "vein_scale": {"type": "number", "minimum": 1.0, "maximum": 5.0, "default": 2.2},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex = nt.nodes.new("ShaderNodeTexCoord")

    # veiny bands: a SIN bands wave with heavy distortion + detail
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "X"
    wave.wave_profile = "SIN"
    nt.links.new(tex.outputs["Object"], wave.inputs["Vector"])
    wave.inputs["Scale"].default_value = params["vein_scale"]
    wave.inputs["Distortion"].default_value = 12.0
    wave.inputs["Detail"].default_value = 3.0
    wave.inputs["Detail Scale"].default_value = 1.4
    wave.inputs["Detail Roughness"].default_value = 0.6

    # sharpen the wave into thin veins: line = (1 - 2*|Fac-0.5|) ^ 6
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
    nt.links.new(inv.outputs["Value"], line.inputs[0]); line.inputs[1].default_value = 6.0

    # low-frequency mottle for base value variation (also feeds S16 std)
    mottle = nt.nodes.new("ShaderNodeGroup")
    mottle.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], mottle.inputs["Vector"])
    mottle.inputs["Scale"].default_value = 4.0
    mottle.inputs["Contrast"].default_value = 0.8

    cream = nt.nodes.new("ShaderNodeRGB")
    cream.outputs[0].default_value = (0.86, 0.845, 0.80, 1.0)     # warm marble white
    # mottle the base a touch
    mval = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(mottle.outputs["Fac"], mval.inputs["Value"])
    mval.inputs["To Min"].default_value = 0.88
    mval.inputs["To Max"].default_value = 1.06
    basecol = nt.nodes.new("ShaderNodeMix"); basecol.data_type = "RGBA"; basecol.blend_type = "MULTIPLY"
    basecol.inputs[0].default_value = 1.0
    nt.links.new(cream.outputs[0], basecol.inputs[6])
    nt.links.new(mval.outputs["Result"], basecol.inputs[7])

    vein = nt.nodes.new("ShaderNodeRGB")
    vein.outputs[0].default_value = (0.42, 0.44, 0.48, 1.0)       # cool grey vein

    col = nt.nodes.new("ShaderNodeMix"); col.data_type = "RGBA"
    nt.links.new(line.outputs["Value"], col.inputs[0])           # 0 base, 1 vein
    nt.links.new(basecol.outputs[2], col.inputs[6])
    nt.links.new(vein.outputs[0], col.inputs[7])
    nt.links.new(col.outputs[2], bsdf.inputs["Base Color"])

    # veins sit a hair proud/rougher; polished elsewhere
    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(line.outputs["Value"], rough.inputs["Value"])
    rough.inputs["To Min"].default_value = params["roughness"]
    rough.inputs["To Max"].default_value = min(params["roughness"] + 0.18, 1.0)
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.08
    nt.links.new(line.outputs["Value"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Metallic"].default_value = 0.0
