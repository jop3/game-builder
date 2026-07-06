"""Shared shader node-group builders (spec 10.2). Each function returns a
reusable ``bpy.types.NodeTree`` (a Blender "node group" data-block) that
material recipes instantiate via ``nt.nodes.new('ShaderNodeGroup')`` and
``group_node.node_tree = <returned tree>``.

Every builder is idempotent: if a node group with the target name already
exists in ``bpy.data.node_groups`` (e.g. a second material recipe in the
same bake session asks for ``metal_base``), the existing tree is returned
rather than rebuilt, so the library is cheap to call from many recipes.

``bpy`` is imported inside each function only (never at module scope) so
this module -- like every generator recipe -- stays importable in plain
CPython (see ``assetpipe/generators/__init__.py`` for the same discipline).
"""
from __future__ import annotations

TAU = 6.283185307179586


def _get_or_create(name: str):
    import bpy

    existing = bpy.data.node_groups.get(name)
    if existing is not None:
        return existing
    return bpy.data.node_groups.new(name, "ShaderNodeTree")


def _io(nt, inputs: list[tuple[str, str]], outputs: list[tuple[str, str]]):
    """Declare a node group's interface (Blender 4.x ``NodeTree.interface``
    API) and return the ``(group_input_node, group_output_node)`` pair."""
    for socket_name, socket_type in inputs:
        nt.interface.new_socket(socket_name, in_out="INPUT", socket_type=socket_type)
    for socket_name, socket_type in outputs:
        nt.interface.new_socket(socket_name, in_out="OUTPUT", socket_type=socket_type)
    group_in = nt.nodes.new("NodeGroupInput")
    group_out = nt.nodes.new("NodeGroupOutput")
    return group_in, group_out


def periodic_coords(name: str = "AP_PeriodicCoords"):
    """Torus-mapped 4D-periodic coordinate domain (spec 10.3): remaps a 2D
    UV into ``(vector3, w)`` so a Noise/Voronoi texture fed with them is
    *exactly* periodic over the 0-1 tile in both axes --
    ``vector = R*(cos(2*pi*u), sin(2*pi*u), cos(2*pi*v))``,
    ``w = R*sin(2*pi*v)``.
    """
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("UV", "NodeSocketVector"), ("Radius", "NodeSocketFloat")],
        outputs=[("Vector", "NodeSocketVector"), ("W", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Radius"].default_value = 1.0

    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(group_in.outputs["UV"], sep.inputs["Vector"])

    def _angle(component):
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = TAU
        nt.links.new(sep.outputs[component], mul.inputs[0])
        return mul

    angle_u = _angle("X")
    angle_v = _angle("Y")

    def _trig(op, angle_node):
        node = nt.nodes.new("ShaderNodeMath")
        node.operation = op
        nt.links.new(angle_node.outputs[0], node.inputs[0])
        return node

    cos_u = _trig("COSINE", angle_u)
    sin_u = _trig("SINE", angle_u)
    cos_v = _trig("COSINE", angle_v)
    sin_v = _trig("SINE", angle_v)

    def _scale_by_radius(math_node):
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        nt.links.new(math_node.outputs[0], mul.inputs[0])
        nt.links.new(group_in.outputs["Radius"], mul.inputs[1])
        return mul

    cos_u_r = _scale_by_radius(cos_u)
    sin_u_r = _scale_by_radius(sin_u)
    cos_v_r = _scale_by_radius(cos_v)
    sin_v_r = _scale_by_radius(sin_v)

    combine = nt.nodes.new("ShaderNodeCombineXYZ")
    nt.links.new(cos_u_r.outputs[0], combine.inputs["X"])
    nt.links.new(sin_u_r.outputs[0], combine.inputs["Y"])
    nt.links.new(cos_v_r.outputs[0], combine.inputs["Z"])

    nt.links.new(combine.outputs[0], group_out.inputs["Vector"])
    nt.links.new(sin_v_r.outputs[0], group_out.inputs["W"])
    return nt


def noise_breakup(name: str = "AP_NoiseBreakup"):
    """Two noise layers at different scales, multiplied and remapped through
    a ColorRamp -- the classic layered-breakup mask used to keep flat
    albedo fills from reading as computer-perfect (spec 10.2)."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Scale", "NodeSocketFloat"),
                ("Contrast", "NodeSocketFloat")],
        outputs=[("Fac", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Scale"].default_value = 4.0
    nt.interface.items_tree["Contrast"].default_value = 0.5

    noise_a = nt.nodes.new("ShaderNodeTexNoise")
    noise_b = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(group_in.outputs["Vector"], noise_a.inputs["Vector"])
    nt.links.new(group_in.outputs["Vector"], noise_b.inputs["Vector"])
    nt.links.new(group_in.outputs["Scale"], noise_a.inputs["Scale"])

    scale_mul = nt.nodes.new("ShaderNodeMath")
    scale_mul.operation = "MULTIPLY"
    scale_mul.inputs[1].default_value = 8.5
    nt.links.new(group_in.outputs["Scale"], scale_mul.inputs[0])
    nt.links.new(scale_mul.outputs[0], noise_b.inputs["Scale"])

    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "FLOAT"
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 1.0
    nt.links.new(noise_a.outputs["Fac"], mix.inputs[2])
    nt.links.new(noise_b.outputs["Fac"], mix.inputs[3])

    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(mix.outputs[0], ramp.inputs["Fac"])

    nt.links.new(ramp.outputs["Color"], group_out.inputs["Fac"])
    return nt


def edge_wear(name: str = "AP_EdgeWear"):
    """Bevel-normal-difference convex-edge mask (spec 10.2): reliable on
    low-poly game meshes, unlike vertex-interpolated Pointiness. ``Fac`` is
    ~1 on sharp convex edges/chamfers, ~0 elsewhere -- lighten/roughen those
    texels in the calling recipe."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Radius", "NodeSocketFloat"), ("Sharpness", "NodeSocketFloat")],
        outputs=[("Fac", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Radius"].default_value = 0.01
    nt.interface.items_tree["Sharpness"].default_value = 0.6

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    bevel = nt.nodes.new("ShaderNodeBevel")
    nt.links.new(group_in.outputs["Radius"], bevel.inputs["Radius"])

    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    nt.links.new(bevel.outputs["Normal"], dot.inputs[1])

    invert = nt.nodes.new("ShaderNodeMath")
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    nt.links.new(dot.outputs["Value"], invert.inputs[1])

    sharpen = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(invert.outputs[0], sharpen.inputs["Value"])
    nt.links.new(group_in.outputs["Sharpness"], sharpen.inputs["From Min"])
    sharpen.inputs["From Max"].default_value = 1.0
    sharpen.inputs["To Min"].default_value = 0.0
    sharpen.inputs["To Max"].default_value = 1.0
    sharpen.clamp = True

    nt.links.new(sharpen.outputs["Result"], group_out.inputs["Fac"])
    return nt


def panel_lines(name: str = "AP_PanelLines"):
    """Brick-pattern grooves (spec 10.2): mortar mask darkens albedo and
    feeds a Bump for the normal pass.

    Verified against real Blender 4.2: the Brick Texture's mask output is
    ``Fac`` (1 in the mortar) -- there is no "Mortar" socket -- and ``Rows``
    is wired to the texture ``Scale`` (a count per UV unit), NOT to "Row
    Height" (a size, which would invert the semantics). A brick grid is a
    discrete lookup, not a continuous field, so unlike the noise groups it
    must NOT be routed through PeriodicCoords: feed it raw UV and keep Rows
    integer -- an integer-scale grid over the 0-1 tile is exactly seamless.
    """
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Rows", "NodeSocketFloat"),
                ("Mortar Width", "NodeSocketFloat")],
        outputs=[("Fac", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Rows"].default_value = 6.0
    nt.interface.items_tree["Mortar Width"].default_value = 0.02

    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.offset = 0.0  # row offset breaks vertical tiling; a panel grid has none
    nt.links.new(group_in.outputs["Vector"], brick.inputs["Vector"])
    nt.links.new(group_in.outputs["Rows"], brick.inputs["Scale"])
    nt.links.new(group_in.outputs["Mortar Width"], brick.inputs["Mortar Size"])

    nt.links.new(brick.outputs["Fac"], group_out.inputs["Fac"])
    return nt


def grunge(name: str = "AP_Grunge"):
    """Layered noise dirt/cavity mask (spec 10.2): two noise scales
    multiplied through a narrow ColorRamp window -- combine with an AO pass
    in the calling recipe for cavity-biased grime."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Scale", "NodeSocketFloat")],
        outputs=[("Fac", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Scale"].default_value = 4.0

    fine = nt.nodes.new("ShaderNodeTexNoise")
    coarse = nt.nodes.new("ShaderNodeTexNoise")
    nt.links.new(group_in.outputs["Vector"], fine.inputs["Vector"])
    nt.links.new(group_in.outputs["Vector"], coarse.inputs["Vector"])
    nt.links.new(group_in.outputs["Scale"], fine.inputs["Scale"])

    coarse_scale = nt.nodes.new("ShaderNodeMath")
    coarse_scale.operation = "MULTIPLY"
    coarse_scale.inputs[1].default_value = 9.25
    nt.links.new(group_in.outputs["Scale"], coarse_scale.inputs[0])
    nt.links.new(coarse_scale.outputs[0], coarse.inputs["Scale"])

    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "FLOAT"
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 1.0
    nt.links.new(fine.outputs["Fac"], mix.inputs[2])
    nt.links.new(coarse.outputs["Fac"], mix.inputs[3])

    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[1].position = 0.65
    nt.links.new(mix.outputs[0], ramp.inputs["Fac"])

    nt.links.new(ramp.outputs["Color"], group_out.inputs["Fac"])
    return nt


def metal_base(name: str = "AP_MetalBase"):
    """Brushed-metal building block: anisotropic-look noise breakup mixed
    into a base color, high metallic, mid-low roughness (spec 10.2)."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Base Color", "NodeSocketColor"),
                ("Roughness", "NodeSocketFloat")],
        outputs=[("Color", "NodeSocketColor"), ("Roughness", "NodeSocketFloat"),
                 ("Metallic", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Roughness"].default_value = 0.35

    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = noise_breakup()
    nt.links.new(group_in.outputs["Vector"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 12.0

    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    nt.links.new(breakup.outputs["Fac"], darken.inputs[0])
    darken.inputs[6].default_value = (1.0, 1.0, 1.0, 1.0)
    nt.links.new(group_in.outputs["Base Color"], darken.inputs[7])

    rough_var = nt.nodes.new("ShaderNodeMath")
    rough_var.operation = "MULTIPLY_ADD"
    rough_var.inputs[1].default_value = 0.15
    nt.links.new(breakup.outputs["Fac"], rough_var.inputs[0])
    nt.links.new(group_in.outputs["Roughness"], rough_var.inputs[2])

    nt.links.new(darken.outputs[2], group_out.inputs["Color"])
    nt.links.new(rough_var.outputs[0], group_out.inputs["Roughness"])
    metallic_const = nt.nodes.new("ShaderNodeValue")
    metallic_const.outputs[0].default_value = 1.0
    nt.links.new(metallic_const.outputs[0], group_out.inputs["Metallic"])
    return nt


def wood_grain(name: str = "AP_WoodGrain"):
    """Anisotropic-stretched noise for wood rings + fine grain, mixed
    between two base colors (spec 10.2)."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Color A", "NodeSocketColor"),
                ("Color B", "NodeSocketColor"), ("Ring Scale", "NodeSocketFloat")],
        outputs=[("Color", "NodeSocketColor"), ("Fac", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Ring Scale"].default_value = 18.0

    stretch = nt.nodes.new("ShaderNodeVectorMath")
    stretch.operation = "MULTIPLY"
    stretch.inputs[1].default_value = (1.0, 0.15, 1.0)
    nt.links.new(group_in.outputs["Vector"], stretch.inputs[0])

    rings = nt.nodes.new("ShaderNodeTexWave")
    rings.wave_type = "RINGS"
    nt.links.new(stretch.outputs[0], rings.inputs["Vector"])
    nt.links.new(group_in.outputs["Ring Scale"], rings.inputs["Scale"])

    grain = nt.nodes.new("ShaderNodeGroup")
    grain.node_tree = noise_breakup()
    nt.links.new(group_in.outputs["Vector"], grain.inputs["Vector"])
    grain.inputs["Scale"].default_value = 40.0

    combine = nt.nodes.new("ShaderNodeMath")
    combine.operation = "MULTIPLY_ADD"
    combine.inputs[1].default_value = 0.3
    nt.links.new(rings.outputs["Fac"], combine.inputs[0])
    nt.links.new(grain.outputs["Fac"], combine.inputs[2])

    mix_color = nt.nodes.new("ShaderNodeMix")
    mix_color.data_type = "RGBA"
    nt.links.new(combine.outputs[0], mix_color.inputs[0])
    nt.links.new(group_in.outputs["Color A"], mix_color.inputs[6])
    nt.links.new(group_in.outputs["Color B"], mix_color.inputs[7])

    nt.links.new(mix_color.outputs[2], group_out.inputs["Color"])
    nt.links.new(combine.outputs[0], group_out.inputs["Fac"])
    return nt


def stone_base(name: str = "AP_StoneBase"):
    """Voronoi cell breakup for dressed/rough stone: cell mask darkens
    mortar-like gaps and drives roughness variance (spec 10.2)."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Base Color", "NodeSocketColor"),
                ("Cell Scale", "NodeSocketFloat")],
        outputs=[("Color", "NodeSocketColor"), ("Roughness", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Cell Scale"].default_value = 6.0

    voronoi = nt.nodes.new("ShaderNodeTexVoronoi")
    voronoi.feature = "DISTANCE_TO_EDGE"
    nt.links.new(group_in.outputs["Vector"], voronoi.inputs["Vector"])
    nt.links.new(group_in.outputs["Cell Scale"], voronoi.inputs["Scale"])

    breakup = nt.nodes.new("ShaderNodeGroup")
    breakup.node_tree = noise_breakup()
    nt.links.new(group_in.outputs["Vector"], breakup.inputs["Vector"])
    breakup.inputs["Scale"].default_value = 20.0

    darken = nt.nodes.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    nt.links.new(voronoi.outputs["Distance"], darken.inputs[0])
    nt.links.new(group_in.outputs["Base Color"], darken.inputs[6])
    darken.inputs[7].default_value = (0.05, 0.05, 0.05, 1.0)

    rough = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(breakup.outputs["Fac"], rough.inputs["Value"])
    rough.inputs["To Min"].default_value = 0.5
    rough.inputs["To Max"].default_value = 0.9

    nt.links.new(darken.outputs[2], group_out.inputs["Color"])
    nt.links.new(rough.outputs["Result"], group_out.inputs["Roughness"])
    return nt


def emissive_strip(name: str = "AP_EmissiveStrip"):
    """Sharp-edged emissive band mask (spec 10.2): a coordinate threshold
    through a ColorRamp so accent strips read as crisp, not blurry noise."""
    import bpy

    nt = _get_or_create(name)
    if nt.nodes:
        return nt

    group_in, group_out = _io(
        nt,
        inputs=[("Vector", "NodeSocketVector"), ("Width", "NodeSocketFloat"),
                ("Color", "NodeSocketColor"), ("Strength", "NodeSocketFloat")],
        outputs=[("Emission", "NodeSocketColor"), ("Fac", "NodeSocketFloat")],
    )
    nt.interface.items_tree["Width"].default_value = 0.05
    nt.interface.items_tree["Strength"].default_value = 2.0

    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(group_in.outputs["Vector"], sep.inputs["Vector"])

    band = nt.nodes.new("ShaderNodeMath")
    band.operation = "PINGPONG"
    band.inputs[1].default_value = 1.0
    nt.links.new(sep.outputs["Y"], band.inputs[0])

    threshold = nt.nodes.new("ShaderNodeMath")
    threshold.operation = "LESS_THAN"
    nt.links.new(band.outputs[0], threshold.inputs[0])
    nt.links.new(group_in.outputs["Width"], threshold.inputs[1])

    emit_color = nt.nodes.new("ShaderNodeMix")
    emit_color.data_type = "RGBA"
    emit_color.blend_type = "MULTIPLY"
    nt.links.new(threshold.outputs[0], emit_color.inputs[0])
    nt.links.new(group_in.outputs["Color"], emit_color.inputs[6])

    strength_mul = nt.nodes.new("ShaderNodeVectorMath")
    strength_mul.operation = "SCALE"
    nt.links.new(emit_color.outputs[2], strength_mul.inputs[0])
    nt.links.new(group_in.outputs["Strength"], strength_mul.inputs["Scale"])

    nt.links.new(strength_mul.outputs[0], group_out.inputs["Emission"])
    nt.links.new(threshold.outputs[0], group_out.inputs["Fac"])
    return nt
