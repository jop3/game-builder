"""themes/fantasy_medieval/materials/fantasy_roof_shingles -- overlapping
shingle courses in the theme's oxblood accent, built from a brick-texture
course pattern (discrete pattern: raw UV/object coordinates, never the
periodic domain -- see the tiling gotcha in docs/NEXT_STEPS.md) plus
``matlib.nodes.grunge`` weathering (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "course_scale": {"type": "number", "minimum": 4.0, "maximum": 16.0, "default": 8.0},
        "roughness": {"type": "number", "minimum": 0.5, "maximum": 0.95, "default": 0.8},
        "weathering": {"type": "number", "minimum": 0.0, "maximum": 0.6, "default": 0.3},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    # Shingle courses: a brick texture with row offset reads as overlapping
    # shingles at game-texture distance. Color A/B are two draws from the
    # accent group so adjacent courses vary slightly.
    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.offset = 0.5
    brick.inputs["Scale"].default_value = params["course_scale"]
    brick.inputs["Mortar Size"].default_value = 0.02
    brick.inputs["Mortar"].default_value = (0.05, 0.03, 0.03, 1.0)
    ra, ga, ba = palette.sample_palette_color(palette_dict, "accent", rng)
    rb, gb, bb = palette.sample_palette_color(palette_dict, "accent", rng)
    brick.inputs["Color1"].default_value = (ra, ga, ba, 1.0)
    brick.inputs["Color2"].default_value = (ra * 0.8 + rb * 0.2, ga * 0.8 + gb * 0.2,
                                            ba * 0.8 + bb * 0.2, 1.0)
    nt.links.new(tex_coord.outputs["Object"], brick.inputs["Vector"])

    grime = nt.nodes.new("ShaderNodeGroup")
    grime.node_tree = nodes.grunge()
    nt.links.new(tex_coord.outputs["Object"], grime.inputs["Vector"])
    grime.inputs["Scale"].default_value = 4.0

    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs[0].default_value = params["weathering"]
    nt.links.new(brick.outputs["Color"], darken.inputs[6])
    nt.links.new(grime.outputs["Fac"], darken.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    nt.links.new(brick.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(darken.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
