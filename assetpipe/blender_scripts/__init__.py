"""In-Blender stage scripts (spec 4.3, 9-14, 16.2).

Every module in this package (except this one, :mod:`common`, :mod:`views`,
and :mod:`contact_sheets`) is executed **inside** Blender as a subprocess, not
imported by the plain-Python orchestrator:

    blender --background [file.blend] --python <this_package>/<stage>.py \\
        -- --args-json <path/to/args.json> [--extra value ...]

Blender invokes the script with ``sys.argv`` containing Blender's own flags
before a bare ``--``, followed by whatever this pipeline passed; each stage
script's ``main()`` calls :func:`assetpipe.blender_scripts.common.parse_args`
to recover only the tail after ``--`` and load the JSON payload. Stage scripts
never accept interactive input and never touch global state Blender didn't
already give them (spec 3's determinism discipline): all randomness comes
from ``random.Random(request.seed)`` / a seeded ``numpy`` generator, Cycles is
pinned to CPU with ``seed=0`` and ``use_animated_seed=False``, and the render
color-management pipeline is pinned (``view_transform='AgX'``).

Execution model per stage (spec 4.3, 5, 9-14):

- ``generate.py``   (Stage G) — build geometry via a generator recipe
  (:mod:`assetpipe.generators.registry`), scene conventions, finishing pass,
  UV pass; writes ``params.json`` + ``asset.blend``.
- ``bake.py``       (Stage M) — build a material recipe's node graph and bake
  albedo/normal/ORM/emissive maps to PNG.
- ``export_gltf.py``(Stage X) — LOD generation, collision suffixes, re-wire
  baked maps to a clean Principled BSDF, and export the canonical
  uncompressed ``.glb`` (spec 12.1's exact exporter parameter set).
- ``static_checks_mesh.py`` (Stage V1, in-Blender half) — checks S1-S12e
  against the pre-export scene; writes ``static_report.json`` entries.
- ``render_views.py`` (Stage R) — re-import the exported ``.glb`` into a
  clean scene and render the spec 14.2 view set + contact sheets.
- ``fixes.py``      (Stage F, table-fix appliers) — one function per
  ``assetpipe.blender_scripts.fixes.*`` dotted path in
  ``assetpipe/schemas/fixes.json``; ``main()`` applies a fix plan's actions.

Bpy-free helper modules (importable and unit-tested with plain CPython, no
Blender process required — this is what makes them testable in
``assetpipe/tests/test_blender_scripts.py``):

- :mod:`assetpipe.blender_scripts.common` — argv/args-json parsing, result
  writing, seeded RNG constructors, generator-parameter resolution.
- :mod:`assetpipe.blender_scripts.views` — the spec 14.2 view table, lighting
  rig specs, and bbox-based camera framing math (plain tuples/``math``, no
  ``mathutils``).
- :mod:`assetpipe.blender_scripts.contact_sheets` — Pillow-only contact sheet
  composition (spec 14.3).

Do not import ``bpy`` (or anything that transitively imports it, e.g.
``bmesh``, ``mathutils``) at module scope in this package's bpy-free modules.
Every bpy-touching module puts ``import bpy`` at the top of the file — that is
fine because these files are only ever *executed* inside Blender; the test
suite verifies them with ``ast.parse``/``py_compile`` instead of ``import``.
"""
