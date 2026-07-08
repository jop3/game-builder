"""themes/fantasy_tabletop/materials/pearl_white -- warm pearl / nacre for the
light Othello disc.

A high-value warm off-white with a faint hue drift between cooler and warmer
pearl (a whisper of nacreous variation), a satin sheen (mid-low roughness),
and a very light bump so it reads as smooth polished stone, not chalk. Stays
below the S16 blown-highlight ceiling (mean luminance < 0.98).
"""
from __future__ import annotations

from assetpipe.matlib import nodes

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "roughness": {"type": "number", "minimum": 0.15, "maximum": 0.5, "default": 0.3},
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

    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = nodes.noise_breakup()
    nt.links.new(tex.outputs["Object"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 5.0
    breakup.inputs["Contrast"].default_value = 0.8

    # Nacreous hue drift: lerp between a cool pearl and a warm ivory so the
    # albedo has real (small) variation -- pale but not a dead flat white.
    cool = nt.nodes.new("ShaderNodeRGB"); cool.outputs[0].default_value = (0.84, 0.86, 0.90, 1.0)
    warm = nt.nodes.new("ShaderNodeRGB"); warm.outputs[0].default_value = (0.93, 0.90, 0.82, 1.0)
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"
    nt.links.new(breakup.outputs["Fac"], mix.inputs[0])
    nt.links.new(cool.outputs[0], mix.inputs[6])
    nt.links.new(warm.outputs[0], mix.inputs[7])
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.05
    nt.links.new(breakup.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    bsdf.inputs["Roughness"].default_value = params["roughness"]
    bsdf.inputs["Metallic"].default_value = 0.0
