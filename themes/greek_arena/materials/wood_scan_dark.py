"""themes/greek_arena/materials/wood_scan_dark -- HYBRIDRECEPT: fotoscannat
trä (texlib wood_dark_051, CC0) mörknat till uråldrig oljad yta.

Ersätter det syntetiska wood_ancient på brädets spelfält: scanens riktiga
ådring/porer (albedo/normal/roughness) multipliceras ner mot nästan-svart
("århundraden av olja och händer") med ett lågfrekvent grunge så mörkningen
inte är jämn. Namnet innehåller "wood" → brädgeneratorns slot 0; ligger FÖRE
wood_ancient i temats materiallista så nyckelordsupplösningen väljer scanen.

KRÄVER texlib-cachen (`python -m assetpipe texlib fetch`; sessionstart-hooken
gör det automatiskt).
"""
from __future__ import annotations

from assetpipe import texlib
from assetpipe.matlib import imagesets, nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "scale": {"type": "number", "minimum": 0.3, "maximum": 8.0, "default": 2.4},
        "darken": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.72},
        "roughness_bias": {"type": "number", "minimum": -0.3, "maximum": 0.4, "default": -0.05},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False

TEXSET = "wood_dark_051"


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    ws = texlib.resolve(TEXSET)
    socks = imagesets.wire_pbr_maps(nt, ws["maps"], scale=params["scale"])

    # mörka ner mot uråldrigt oljat: multiplicera mot en låg varm ton, brutet
    # av lågfrekvent grunge så fältet inte blir jämnsvart (S16 behåller varians
    # via scanens ådring som skiner igenom)
    tex = nt.nodes.new("ShaderNodeTexCoord")
    grunge = nt.nodes.new("ShaderNodeGroup")
    grunge.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], grunge.inputs["Vector"])
    grunge.inputs["Scale"].default_value = 3.0
    grunge.inputs["Contrast"].default_value = 1.0
    lo = 1.0 - params["darken"]                     # 0.72 → faktor ~0.28
    gband = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(grunge.outputs["Fac"], gband.inputs["Value"])
    gband.inputs["To Min"].default_value = max(lo * 0.7, 0.05)
    gband.inputs["To Max"].default_value = min(lo * 1.6, 1.0)
    dark = nt.nodes.new("ShaderNodeMix"); dark.data_type = "RGBA"; dark.blend_type = "MULTIPLY"
    dark.inputs[0].default_value = 1.0
    nt.links.new(socks["color"], dark.inputs[6])
    nt.links.new(gband.outputs["Result"], dark.inputs[7])
    nt.links.new(dark.outputs[2], bsdf.inputs["Base Color"])

    if "roughness" in socks:
        radd = nt.nodes.new("ShaderNodeMath"); radd.operation = "ADD"
        radd.use_clamp = True
        nt.links.new(socks["roughness"], radd.inputs[0])
        radd.inputs[1].default_value = params["roughness_bias"]
        nt.links.new(radd.outputs["Value"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.55

    if "normal" in socks:
        nt.links.new(socks["normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Metallic"].default_value = 0.0
