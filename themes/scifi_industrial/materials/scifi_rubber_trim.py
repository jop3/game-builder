"""themes/scifi_industrial/materials/scifi_rubber_trim -- matte black
gasket/handle-grip trim with fine noise breakup, non-metallic (spec 10.2)."""
from __future__ import annotations

from assetpipe.matlib import nodes, palette

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.5, "maximum": 0.95, "default": 0.8},
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

    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = nodes.noise_breakup()
    nt.links.new(tex_coord.outputs["Object"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 60.0

    # Rubber trim is a near-black accent, not sampled from the metal
    # palette groups -- a fixed dark base darkened further by the breakup
    # mask keeps it visually distinct from painted panels.
    base = nt.nodes.new("ShaderNodeRGB")
    base.outputs[0].default_value = (0.03, 0.03, 0.035, 1.0)

    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs[0].default_value = 0.5
    nt.links.new(breakup.outputs["Fac"], darken.inputs[6])
    nt.links.new(base.outputs[0], darken.inputs[7])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    nt.links.new(breakup.outputs["Fac"], bump.inputs["Height"])

    nt.links.new(darken.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
