"""Generator recipes (spec 9.1) and the registry that indexes them (spec 9.2).

A recipe is a plain Python module living at
``assetpipe/generators/<category_group>/<name>.py`` (e.g. ``props/crate.py``) with:

- ``PARAM_SCHEMA`` — JSON Schema (object) for the recipe's parameters. Every
  ``"number"``/``"integer"`` property MUST declare both ``minimum`` and
  ``maximum`` (the fix loop, spec 16.4, may only move values inside those
  bounds).
- ``CATEGORY`` — one of ``assetpipe.contracts.MESH_CATEGORIES``.
- ``KEYWORDS`` — list[str] used by :meth:`Registry.resolve` to pick a recipe
  from a free-text description when a request omits ``generator``.
- ``generate(params: dict, rng, theme: dict)`` — builds and returns the root
  Blender object.

Convention (load-bearing for testability): recipe modules import ``bpy`` (and
any other Blender-only module) **only inside** ``generate()``, never at module
scope. That keeps ``PARAM_SCHEMA``/``CATEGORY``/``KEYWORDS`` — and therefore the
whole registry — importable and unit-testable in plain CPython, with no
Blender process required (mirrors the "pure Python core" discipline in
``assetpipe/README.md``).
"""
