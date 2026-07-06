"""Shared material node-group library and palette sampling (spec 10.2, 10.5).

``matlib.nodes`` builds the reusable Blender shader node groups
(``noise_breakup``, ``edge_wear``, ``panel_lines``, ``grunge``,
``metal_base``, ``wood_grain``, ``stone_base``, ``emissive_strip``,
``periodic_coords``) that material recipes (``themes/<theme>/materials/*.py``)
compose from. Like generator recipes, every function in ``matlib.nodes``
imports ``bpy`` only inside its own body, so the module is importable
without Blender.

``matlib.palette`` is deliberately bpy-free: it only samples/jitters hex
colors from a theme's palette (spec 10.5), which is pure arithmetic and
needs to be unit-testable without a Blender process.
"""
