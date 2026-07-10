"""themes/greek_arena/materials/marble_scan -- HYBRIDRECEPT: fotoscannad vit
marmor (texlib marble_polished_012, CC0) + procedurell åldring ovanpå.

Ersätter den syntetiska ådringen i marble_white med en riktig scans albedo/
normal/roughness (box-projicerad i objektrum), och lägger pipelinens åldring
ovanpå: lågfrekvent varm patina-multiplikation (solgulnad antik sten, inte
showroom-vit) och en roughness-bias uppåt. Samma mönster som stone_travertine.

KRÄVER texlib-cachen: `python -m assetpipe texlib fetch` (TexlibMissing ger
hinten). Sessionstart-hooken gör detta automatiskt i molnsessioner.
"""
from __future__ import annotations

from assetpipe import texlib
from assetpipe.matlib import imagesets, nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "scale": {"type": "number", "minimum": 0.3, "maximum": 6.0, "default": 1.2},
        "age": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.45},
        "roughness_bias": {"type": "number", "minimum": -0.3, "maximum": 0.4, "default": 0.10},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

TEXSET = "marble_polished_012"


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    ms = texlib.resolve(TEXSET)
    socks = imagesets.wire_pbr_maps(nt, ms["maps"], scale=params["scale"])

    # åldring: lågfrekvent VARM patina (multiplikativ mot en solgul kräm-ton)
    tex = nt.nodes.new("ShaderNodeTexCoord")
    age = nt.nodes.new("ShaderNodeGroup")
    age.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], age.inputs["Vector"])
    age.inputs["Scale"].default_value = 2.2
    age.inputs["Contrast"].default_value = 1.0
    afac = nt.nodes.new("ShaderNodeMath"); afac.operation = "MULTIPLY"
    nt.links.new(age.outputs["Fac"], afac.inputs[0])
    afac.inputs[1].default_value = params["age"]
    warm = nt.nodes.new("ShaderNodeMix"); warm.data_type = "RGBA"
    nt.links.new(afac.outputs["Value"], warm.inputs[0])
    nt.links.new(socks["color"], warm.inputs[6])
    tone = nt.nodes.new("ShaderNodeMix"); tone.data_type = "RGBA"; tone.blend_type = "MULTIPLY"
    tone.inputs[0].default_value = 1.0
    nt.links.new(socks["color"], tone.inputs[6])
    cream = nt.nodes.new("ShaderNodeRGB")
    cream.outputs[0].default_value = (0.92, 0.85, 0.72, 1.0)   # solgulnad
    nt.links.new(cream.outputs[0], tone.inputs[7])
    nt.links.new(tone.outputs[2], warm.inputs[7])
    nt.links.new(warm.outputs[2], bsdf.inputs["Base Color"])

    if "roughness" in socks:
        radd = nt.nodes.new("ShaderNodeMath"); radd.operation = "ADD"
        radd.use_clamp = True
        nt.links.new(socks["roughness"], radd.inputs[0])
        radd.inputs[1].default_value = params["roughness_bias"]
        nt.links.new(radd.outputs["Value"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.35

    if "normal" in socks:
        nt.links.new(socks["normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Metallic"].default_value = 0.0
