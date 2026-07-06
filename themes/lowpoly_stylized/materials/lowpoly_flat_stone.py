"""themes/lowpoly_stylized/materials/lowpoly_flat_stone -- flat-shaded rock
fill, sampled from ``accent`` (spec 7)."""
from __future__ import annotations

from assetpipe.matlib import palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.7, "maximum": 1.0, "default": 0.9},
    },
    "additionalProperties": False,
}
BAKES = ["albedo"]
TILING = False
FLAT_COLOR = True


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    r, g, b = palette.sample_palette_color(palette_dict, "accent", rng)
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
