"""themes/greek_arena/materials/bronze_trim -- åldrad brons för ram + rutnät.

Antikens spelbräden var trä + brons, inte svart plast: en varm bronston med
brusad värdejitter, ljusare nött brons på kanterna (``edge_wear``, tajt radie
så de smala ribborna inte blir helt blanka) och en återhållsam ÄRG-antydan
(kall grönblå) i de lågfrekventa mörka fickorna — patina, inte färgglad
korrosion. Variationen ger S16-varians.

FÄRGKEDJAN BYGGS HÄR, inte via ``metal_base``: dess vit-mixande breakup
bleker basfärgen mot grädde (samma fälla som fantasy_iron_trim dokumenterar
— första bakningen av den här filen kom ut som elfenbenssten). Metallic sätts
direkt på BSDF:n.

Namnet innehåller "trim" → brädgeneratorns slot 1 (ram + rutnätsribbor).
Medvetet undantag från temats "inga metalliska material"-anti-stil: den regeln
gäller STENEN (marmorn får inte läsa som metall); brons är periodkorrekt.
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.25, "maximum": 0.7, "default": 0.45},
        "edge_highlight": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.55},
        "patina": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.35},
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

    # bronsbas: varm koppar-guld-ton med brusad VÄRDE-jitter (multiplikativ,
    # aldrig mot vitt — det bleker; se docstringen)
    jitter = nt.nodes.new("ShaderNodeGroup")
    jitter.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], jitter.inputs["Vector"])
    jitter.inputs["Scale"].default_value = 14.0
    jitter.inputs["Contrast"].default_value = 1.0
    jband = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(jitter.outputs["Fac"], jband.inputs["Value"])
    jband.inputs["To Min"].default_value = 0.80
    jband.inputs["To Max"].default_value = 1.12
    bronze = nt.nodes.new("ShaderNodeRGB")
    bronze.outputs[0].default_value = (0.40, 0.24, 0.10, 1.0)
    base = nt.nodes.new("ShaderNodeMix"); base.data_type = "RGBA"; base.blend_type = "MULTIPLY"
    base.inputs[0].default_value = 1.0
    nt.links.new(bronze.outputs[0], base.inputs[6])
    nt.links.new(jband.outputs["Result"], base.inputs[7])

    # ärg i de mörka fickorna: lågfrekvent mask, låga fac → kall grönblå patina
    pocket = nt.nodes.new("ShaderNodeGroup")
    pocket.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], pocket.inputs["Vector"])
    pocket.inputs["Scale"].default_value = 5.0
    pocket.inputs["Contrast"].default_value = 1.2
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    nt.links.new(pocket.outputs["Fac"], inv.inputs[1])
    pat_amt = nt.nodes.new("ShaderNodeMath"); pat_amt.operation = "MULTIPLY"
    nt.links.new(inv.outputs["Value"], pat_amt.inputs[0])
    pat_amt.inputs[1].default_value = params["patina"] * 0.6
    patina = nt.nodes.new("ShaderNodeMix"); patina.data_type = "RGBA"
    nt.links.new(pat_amt.outputs["Value"], patina.inputs[0])
    nt.links.new(base.outputs[2], patina.inputs[6])
    verd = nt.nodes.new("ShaderNodeRGB")
    verd.outputs[0].default_value = (0.16, 0.30, 0.26, 1.0)
    nt.links.new(verd.outputs[0], patina.inputs[7])

    # nötta ljusa kanter: rå brons där fingrar/brickor slitit
    wear = nt.nodes.new("ShaderNodeGroup")
    wear.node_tree = nodes.edge_wear()
    wear.inputs["Radius"].default_value = 0.004   # tajt: ribborna är ~3 mm
    wear.inputs["Sharpness"].default_value = 0.55
    wear_amt = nt.nodes.new("ShaderNodeMath"); wear_amt.operation = "MULTIPLY"
    nt.links.new(wear.outputs["Fac"], wear_amt.inputs[0])
    wear_amt.inputs[1].default_value = params["edge_highlight"]
    worn = nt.nodes.new("ShaderNodeMix"); worn.data_type = "RGBA"
    nt.links.new(wear_amt.outputs["Value"], worn.inputs[0])
    nt.links.new(patina.outputs[2], worn.inputs[6])
    bright = nt.nodes.new("ShaderNodeRGB")
    bright.outputs[0].default_value = (0.62, 0.42, 0.19, 1.0)
    nt.links.new(bright.outputs[0], worn.inputs[7])
    nt.links.new(worn.outputs[2], bsdf.inputs["Base Color"])

    # ärgen är matt, nött brons blank: roughness följer nötningen
    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(wear_amt.outputs["Value"], rough.inputs["Value"])
    rough.inputs["To Min"].default_value = min(params["roughness"] + 0.15, 1.0)
    rough.inputs["To Max"].default_value = max(params["roughness"] - 0.15, 0.1)
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 1.0
