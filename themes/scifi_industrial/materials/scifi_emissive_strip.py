"""themes/scifi_industrial/materials/scifi_emissive_strip -- thin glowing
accent band, sampled from the theme's ``emissive`` palette group, built from
``matlib.nodes.emissive_strip`` (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width": {"type": "number", "minimum": 0.01, "maximum": 0.2, "default": 0.05},
        "strength": {"type": "number", "minimum": 0.5, "maximum": 5.0, "default": 2.5},
    },
    "additionalProperties": False,
}
BAKES = ["albedo", "normal", "orm", "emissive"]
TILING = False


def build(nt, params: dict, rng, palette_dict: dict) -> None:
    import bpy

    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")

    strip = nt.nodes.new("ShaderNodeGroup")
    strip.node_tree = nodes.emissive_strip()
    nt.links.new(tex_coord.outputs["Object"], strip.inputs["Vector"])
    strip.inputs["Width"].default_value = params["width"]
    strip.inputs["Strength"].default_value = params["strength"]
    r, g, b = palette.sample_palette_color(palette_dict, "emissive", rng)
    strip.inputs["Color"].default_value = (r, g, b, 1.0)

    # Off-strip housing is the same dark rubber/metal base as the trim
    # material -- kept as a fixed dark value so the emissive strip reads
    # unambiguously against a non-emissive surround.
    housing = nt.nodes.new("ShaderNodeRGB")
    housing.outputs[0].default_value = (0.06, 0.07, 0.08, 1.0)

    base_mix = nt.nodes.new("ShaderNodeMix")
    base_mix.data_type = "RGBA"
    nt.links.new(strip.outputs["Fac"], base_mix.inputs[0])
    nt.links.new(housing.outputs[0], base_mix.inputs[6])
    base_mix.inputs[7].default_value = (r, g, b, 1.0)

    nt.links.new(base_mix.outputs[2], bsdf.inputs["Base Color"])
    nt.links.new(strip.outputs["Emission"], bsdf.inputs["Emission Color"])
    nt.links.new(strip.outputs["Fac"], bsdf.inputs["Emission Strength"])
    bsdf.inputs["Roughness"].default_value = 0.4
    bsdf.inputs["Metallic"].default_value = 0.0
