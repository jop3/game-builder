"""Neural (image-to-3D) backend support -- the bpy-free, GPU-side half.

This package holds the parts of an optional neural asset backend (e.g. TRELLIS
/ trellis.cpp) that run **outside** the deterministic Blender core: the
content-addressed cache that freezes each non-deterministic model output so
the in-Blender generator recipe stays deterministic and re-runnable.

Nothing here imports ``bpy``; it is plain CPython and unit-tested in the same
pure-Python CI tier as the rest of ``assetpipe`` (see ``assetpipe/README.md``).
The design and the split-stage-G rationale are documented in
``docs/NEURAL_BACKEND.md``.
"""
