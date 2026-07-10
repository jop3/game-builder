"""themes/greek_arena/materials/stone_travertine -- HYBRIDRECEPT: fotoscannad
travertin (texlib, CC0) + procedurellt slitage ovanpå.

Första receptet som använder texlib i stället för att syntetisera stenen från
brus: albedo/normal/roughness kommer från ambientCG:s Travertine009 (pinnad
sha256, box-projicerad i objektrum → ingen UV-beroende stretch), och ovanpå
läggs pipelinens vanliga procedurella lager (sprick-AO ur ridged-mask + låg-
frekvent grime) så scanen inte ser kliniskt ren ut. Mönstret att kopiera för
fler hybridrecept: texlib.resolve → imagesets.wire_pbr_maps → komponera.

KRÄVER texlib-cachen: kör `python -m assetpipe texlib fetch` först
(TexlibMissing ger den hinten om cachen saknas — hellre ett tydligt rött än
en tyst fallback som ändrar utseendet).
"""
from __future__ import annotations

from assetpipe import texlib
from assetpipe.matlib import imagesets, nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "scale": {"type": "number", "minimum": 0.3, "maximum": 6.0, "default": 1.6},
        "grime": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.35},
        "roughness_bias": {"type": "number", "minimum": -0.3, "maximum": 0.3, "default": 0.05},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

TEXSET = "travertine_009"


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tv = texlib.resolve(TEXSET)
    socks = imagesets.wire_pbr_maps(nt, tv["maps"], scale=params["scale"])

    # procedurell grime: lågfrekvent mörkning så scanen inte läser fabriksny
    tex = nt.nodes.new("ShaderNodeTexCoord")
    grunge = nt.nodes.new("ShaderNodeGroup")
    grunge.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], grunge.inputs["Vector"])
    grunge.inputs["Scale"].default_value = 2.6
    grunge.inputs["Contrast"].default_value = 1.0
    gband = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(grunge.outputs["Fac"], gband.inputs["Value"])
    gband.inputs["To Min"].default_value = 1.0 - 0.35 * params["grime"]
    gband.inputs["To Max"].default_value = 1.0 + 0.10 * params["grime"]
    dirty = nt.nodes.new("ShaderNodeMix"); dirty.data_type = "RGBA"; dirty.blend_type = "MULTIPLY"
    dirty.inputs[0].default_value = 1.0
    nt.links.new(socks["color"], dirty.inputs[6])
    nt.links.new(gband.outputs["Result"], dirty.inputs[7])
    nt.links.new(dirty.outputs[2], bsdf.inputs["Base Color"])

    # scanens roughness med justerbar bias (åldrad sten är sällan blank)
    if "roughness" in socks:
        radd = nt.nodes.new("ShaderNodeMath"); radd.operation = "ADD"
        radd.use_clamp = True
        nt.links.new(socks["roughness"], radd.inputs[0])
        radd.inputs[1].default_value = params["roughness_bias"]
        nt.links.new(radd.outputs["Value"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.75

    if "normal" in socks:
        nt.links.new(socks["normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Metallic"].default_value = 0.0
