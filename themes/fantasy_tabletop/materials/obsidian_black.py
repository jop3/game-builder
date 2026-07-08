"""themes/fantasy_tabletop/materials/obsidian_black -- polished black obsidian
for the dark Othello disc.

Near-black volcanic glass: a very dark base with a faint cool tint, low
roughness for a wet glossy sheen, dielectric (metallic 0). The base can't be
pure black -- S16 requires albedo mean luminance >= 0.02 with std > 0.01 --
so it sits at ~0.03 with a subtle low-frequency value breakup that both keeps
it reading as near-black and satisfies the not-flat check without declaring
flat_color.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.05, "maximum": 0.35, "default": 0.14},
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

    # Subtle value breakup so the albedo is dark-but-not-flat (S16 std test).
    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 6.0
    breakup.inputs["Contrast"].default_value = 1.0

    # Map the 0..1 breakup into a narrow near-black band [0.024, 0.055].
    band = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], band.inputs["Value"])
    band.inputs["To Min"].default_value = 0.024
    band.inputs["To Max"].default_value = 0.055
    # Cool obsidian tint: blue slightly above red/green.
    tint = nt.nodes.new("ShaderNodeCombineColor")
    nt.links.new(band.outputs["Result"], tint.inputs["Red"])
    nt.links.new(band.outputs["Result"], tint.inputs["Green"])
    bluer = nt.nodes.new("ShaderNodeMath")
    bluer.operation = "MULTIPLY"
    nt.links.new(band.outputs["Result"], bluer.inputs[0])
    bluer.inputs[1].default_value = 1.25
    nt.links.new(bluer.outputs[0], tint.inputs["Blue"])
    nt.links.new(tint.outputs["Color"], bsdf.inputs["Base Color"])

    # Glossy, faintly conchoidal surface: low roughness + a whisper of bump.
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.06
    nt.links.new(breakup.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
