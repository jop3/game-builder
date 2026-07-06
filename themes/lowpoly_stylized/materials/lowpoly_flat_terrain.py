"""themes/lowpoly_stylized/materials/lowpoly_flat_terrain -- the theme's
degenerate material case (spec 7): flat albedo + constant roughness, no
normal/ORM bake. Faces are meant to read as solid color blocks; ``rng``
picks a single palette color per material instance and nothing else varies.
"""
from __future__ import annotations

from assetpipe.matlib import palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.6, "maximum": 0.95, "default": 0.85},
    },
    "additionalProperties": False,
}
BAKES = ["albedo"]
TILING = False
FLAT_COLOR = True


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    """Flat-shaded terrain fill: a Principled BSDF with a constant color and
    roughness, no procedural texture inputs at all (spec 7)."""
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    r, g, b = palette.sample_palette_color(palette_dict, "primary", rng)
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
