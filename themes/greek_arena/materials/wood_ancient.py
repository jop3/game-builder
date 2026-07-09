"""themes/greek_arena/materials/wood_ancient -- mörkt uråldrigt brädträ.

Spelfältet på det antika brädet: mörk, oljad valnöt/ceder som mörknat av
århundraden — två djupbruna toner blandade av ``matlib.nodes.wood_grain``
(ringar + fin ådring), åldrad av ett lågfrekvent grunge-multiplikat så ytan
läser som nött trä, inte plastlaminat. Matt-sidenblank (roughness ~0.55) med
svag bump ur ådringen. Ring-/grunge-variationen ger S16-varians (ingen
flat_color). Namnet innehåller "wood" → brädgeneratorns slot 0 (spelyta).
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.35, "maximum": 0.8, "default": 0.55},
        "ring_scale": {"type": "number", "minimum": 6.0, "maximum": 30.0, "default": 14.0},
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

    # mörk valnöt: ådring mellan djupbrunt och nästan-svart
    grain = nt.nodes.new("ShaderNodeGroup")
    grain.node_tree = nodes.wood_grain()
    nt.links.new(tex.outputs["Object"], grain.inputs["Vector"])
    grain.inputs["Color A"].default_value = (0.068, 0.044, 0.027, 1.0)   # varm mycket mörk brun
    grain.inputs["Color B"].default_value = (0.028, 0.019, 0.012, 1.0)   # nästan svart
    grain.inputs["Ring Scale"].default_value = params["ring_scale"]

    # århundraden av smuts/oljning: lågfrekvent grunge multiplicerar värdet
    age = nt.nodes.new("ShaderNodeGroup")
    age.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], age.inputs["Vector"])
    age.inputs["Scale"].default_value = 3.0
    age.inputs["Contrast"].default_value = 1.0
    aval = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(age.outputs["Fac"], aval.inputs["Value"])
    aval.inputs["To Min"].default_value = 0.75
    aval.inputs["To Max"].default_value = 1.10
    aged = nt.nodes.new("ShaderNodeMix"); aged.data_type = "RGBA"; aged.blend_type = "MULTIPLY"
    aged.inputs[0].default_value = 1.0
    nt.links.new(grain.outputs["Color"], aged.inputs[6])
    nt.links.new(aval.outputs["Result"], aged.inputs[7])
    nt.links.new(aged.outputs[2], bsdf.inputs["Base Color"])

    # ådringen sticker upp en aning; blankare i de mörka oljade partierna
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    nt.links.new(grain.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(grain.outputs["Fac"], rough.inputs["Value"])
    rough.inputs["To Min"].default_value = max(params["roughness"] - 0.12, 0.2)
    rough.inputs["To Max"].default_value = min(params["roughness"] + 0.10, 1.0)
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

    bsdf.inputs["Metallic"].default_value = 0.0
