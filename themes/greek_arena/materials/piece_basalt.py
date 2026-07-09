"""themes/greek_arena/materials/piece_basalt -- polerad svart basalt-bricka.

Den mörka spelbrickans ansikte: djupt svart vulkanisk sten med en aning varm
gråton (inte obsidianens kalla blå — settet är varmt medelhavsljus) och ett
subtilt kornigt värdebrus. Basen ligger på ~0.03 med smal spridning så den
läser som svart men klarar S16 (medelluminans >= 0.02, std > 0.01) utan
flat_color. Polerad men stenmatt jämfört med glas (roughness ~0.20).
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.08, "maximum": 0.4, "default": 0.20},
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

    # kornigt nästan-svart: smal värdeband med varm neutral ton
    speck = nt.nodes.new("ShaderNodeGroup")
    speck.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], speck.inputs["Vector"])
    speck.inputs["Scale"].default_value = 45.0     # fint stenkorn i brickskala
    speck.inputs["Contrast"].default_value = 1.0
    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(speck.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.022
    band.inputs["To Max"].default_value = 0.052
    tint = nt.nodes.new("ShaderNodeCombineColor")
    warm = nt.nodes.new("ShaderNodeMath"); warm.operation = "MULTIPLY"
    nt.links.new(band.outputs["Result"], warm.inputs[0]); warm.inputs[1].default_value = 1.08
    nt.links.new(warm.outputs["Value"], tint.inputs["Red"])
    nt.links.new(band.outputs["Result"], tint.inputs["Green"])
    cool = nt.nodes.new("ShaderNodeMath"); cool.operation = "MULTIPLY"
    nt.links.new(band.outputs["Result"], cool.inputs[0]); cool.inputs[1].default_value = 0.95
    nt.links.new(cool.outputs["Value"], tint.inputs["Blue"])
    nt.links.new(tint.outputs["Color"], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    nt.links.new(speck.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
