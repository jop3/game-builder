"""themes/greek_arena/materials/piece_ivory -- polerad elfenbensmarmor-bricka.

Den ljusa spelbrickans ansikte: varm gräddvit sten (samma Pentelic-familj som
kolonnens marmor så settet hänger ihop), ett ENDA diskret grått ådersystem i
brickskala och en mjuk värdemottling. Polerad (roughness ~0.22) men inte
plastblank. Åder + mottling ger S16-varians utan flat_color.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.1, "maximum": 0.5, "default": 0.22},
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

    # varm gräddvit bas med mjuk mottling
    mottle = nt.nodes.new("ShaderNodeGroup")
    mottle.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], mottle.inputs["Vector"])
    mottle.inputs["Scale"].default_value = 30.0     # brickskala (~4 cm objekt)
    mottle.inputs["Contrast"].default_value = 0.9
    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(mottle.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.88
    band.inputs["To Max"].default_value = 1.05
    cream = nt.nodes.new("ShaderNodeRGB")
    cream.outputs[0].default_value = (0.84, 0.79, 0.70, 1.0)
    base = nt.nodes.new("ShaderNodeMix"); base.data_type = "RGBA"; base.blend_type = "MULTIPLY"
    base.inputs[0].default_value = 1.0
    nt.links.new(cream.outputs[0], base.inputs[6])
    nt.links.new(band.outputs["Result"], base.inputs[7])

    # ett diskret grått ådersystem (distorderad våg → tunn ås)
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"; wave.bands_direction = "X"; wave.wave_profile = "SIN"
    nt.links.new(tex.outputs["Object"], wave.inputs["Vector"])
    wave.inputs["Scale"].default_value = 18.0
    wave.inputs["Distortion"].default_value = 10.0
    wave.inputs["Detail"].default_value = 2.0
    sub = nt.nodes.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
    nt.links.new(wave.outputs["Fac"], sub.inputs[0]); sub.inputs[1].default_value = 0.5
    ab = nt.nodes.new("ShaderNodeMath"); ab.operation = "ABSOLUTE"
    nt.links.new(sub.outputs["Value"], ab.inputs[0])
    m2 = nt.nodes.new("ShaderNodeMath"); m2.operation = "MULTIPLY"
    nt.links.new(ab.outputs["Value"], m2.inputs[0]); m2.inputs[1].default_value = 2.0
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    nt.links.new(m2.outputs["Value"], inv.inputs[1])
    vein = nt.nodes.new("ShaderNodeMath"); vein.operation = "POWER"
    nt.links.new(inv.outputs["Value"], vein.inputs[0]); vein.inputs[1].default_value = 6.0
    vein_amt = nt.nodes.new("ShaderNodeMath"); vein_amt.operation = "MULTIPLY"
    nt.links.new(vein.outputs["Value"], vein_amt.inputs[0]); vein_amt.inputs[1].default_value = 0.5

    veined = nt.nodes.new("ShaderNodeMix"); veined.data_type = "RGBA"
    nt.links.new(vein_amt.outputs["Value"], veined.inputs[0])
    nt.links.new(base.outputs[2], veined.inputs[6])
    grey = nt.nodes.new("ShaderNodeRGB")
    grey.outputs[0].default_value = (0.55, 0.53, 0.50, 1.0)
    nt.links.new(grey.outputs[0], veined.inputs[7])
    nt.links.new(veined.outputs[2], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    nt.links.new(mottle.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
