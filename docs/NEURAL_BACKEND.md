# Optional neural (image-to-3D) generator backend

**Status:** design + tested seam. The bpy-free cache helper
(`assetpipe/neural/trellis_cache.py`) is implemented and CI-covered
(`assetpipe/tests/test_trellis_cache.py`, 36 tests). The generator recipe and
the out-of-band CUDA service are **not** shipped — this document is the plan
and the concrete integration cost for adding them.

This describes how a neural image-to-3D model (TRELLIS / `trellis.cpp`, as
wrapped by projects like AISmith-3D) could be added as an **optional,
opt-in** generator alongside the procedural recipes in
`assetpipe/generators/`, without compromising the pipeline's deterministic,
GPU-free, zero-human core (spec §1.1).

---

## 1. Why it doesn't drop straight into a recipe

A generator recipe (spec §9.1, `assetpipe/generators/__init__.py`) must be:

- **deterministic** given `(params, rng)` — same seed ⇒ same mesh (spec §21.2);
- **GPU-free and headless** — `generate()` runs inside the Blender subprocess,
  which the toolchain keeps CUDA-free so the core runs in CI;
- **importable in plain CPython** — `bpy` is imported only *inside*
  `generate()`, so the registry and schemas are unit-testable with no Blender.

A neural model breaks all three: it's a heavy, **non-deterministic** CUDA
process. You cannot run it inside `generate()` and keep the recipe contract.

## 2. The seam: split stage G in two

Isolate the non-deterministic half **out of band** and freeze its output in a
**content-addressed cache**. The in-Blender half then becomes a normal,
deterministic recipe that just imports the frozen mesh and finishes it like
any procedural recipe — so everything downstream is unchanged.

```
 request.reference_image ──┐
                           │   OUT OF BAND, on a GPU host, ONCE per (image, model, seed)
                           ▼
                  ┌──────────────────────┐
                  │  trellis gen service  │   trellis.cpp / TRELLIS.2 FP8
                  │  → raw mesh .glb      │   NON-deterministic
                  └──────────┬───────────┘
                             ▼
        trellis_cache.store(...)  →  <cache_root>/<sha256(img)>__<model>__seed<n>.glb
                             ▼                                   (frozen artifact)
   ─────────────── from here on, identical to a procedural recipe ───────────────
   generate():  trellis_cache.resolve_or_fail(...)  → import .glb → finishing_pass
                → decimate_to_budget → smart_uv_project → return root
                             ▼                              (DETERMINISTIC: import + cleanup)
        export X → V1 → render R → vision V2 → fix loop F        (UNCHANGED)
```

**Determinism contract.** The only variable input — the model — is frozen in
the cache, keyed by the tuple that fully determines it: the **SHA-256 of the
reference-image bytes**, the `model_version`, and the `seed`. On a cache hit
`generate()` is pure import + cleanup: deterministic and re-runnable. On a
cache miss it fails clean (see §4), never runs a model in-process, and so
never smuggles CUDA or non-determinism into the Blender core.

Keying on image **content** (not path) means the same image under a different
filename resolves to the same artifact, and any pixel change invalidates it.

## 3. What ships today — `assetpipe/neural/trellis_cache.py`

A pure-Python, bpy-free cache module (lives in its own package, *not* under
`generators/`, so `Registry.discover()` never mistakes it for a recipe):

| Function | Role |
|---|---|
| `image_digest(image)` | Streamed SHA-256 of the reference image bytes |
| `cache_key(image, model_version, seed)` | `<sha256>__<model_version>__seed<seed>`; validates a filesystem-safe `model_version` slug (no traversal) and a non-negative int `seed` |
| `artifact_path(cache_root, image, model_version, seed)` | Absolute `.glb` path; pure path arithmetic |
| `resolve_or_fail(cache_root, …)` | What the recipe calls — returns the path on a hit, raises `TrellisCacheMiss` on a miss |
| `store(cache_root, …, produced_glb)` | What the **out-of-band service** calls — copies bytes into the cache; idempotent |
| `provenance(image, model_version, seed)` | JSON-able dict for the run manifest / `history.jsonl` (spec §17) |

It has no dependency on `bpy`, the network, or `loop.InfraError` — the coupling
to the pipeline is left to the recipe layer (§4) so the helper stays trivially
testable.

## 4. The recipe (sketch — not yet shipped)

Drop this at `assetpipe/generators/neural/trellis_prop.py` and
`Registry.discover()` picks it up with **no registry changes**. Adding it also
means adding `neural/trellis_prop` to `EXPECTED_RECIPES` in
`assetpipe/tests/test_recipes.py`.

```python
"""neural/trellis_prop -- image->mesh via TRELLIS, category ``prop_small``.

Non-procedural generator: the mesh is produced by a neural image-to-3D model
run OUT OF BAND on a CUDA host and frozen in a content-addressed cache. This
module never runs the model; it imports the cached artifact and runs the same
finishing passes as a procedural recipe, so export/V1/R/V2/F are unchanged.
bpy is imported only inside generate(), same as every procedural recipe.
"""
from __future__ import annotations

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        # Select/freeze the neural front-half. NOT fix-loop inputs: §16.4 only
        # nudges numeric params, and re-generating a mesh is not a targeted fix.
        "reference_image": {"type": "string"},          # path, relative to run intake
        "model_version":   {"type": "string"},          # e.g. "trellis2-fp8"
        "seed":            {"type": "integer", "minimum": 0, "maximum": 2**31 - 1,
                            "default": 0},
        # Feed the deterministic in-Blender half AND the fix loop.
        "target_budget":   {"type": "integer", "minimum": 300, "maximum": 20000,
                            "default": 3000},
        "texture_resolution": {"type": "integer", "minimum": 256, "maximum": 4096,
                            "default": 1024},
        "materials": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reference_image", "model_version"],
    "additionalProperties": False,
}
CATEGORY = "prop_small"          # must be in contracts.GENERATOR_CATEGORIES
KEYWORDS = []                    # empty: never auto-resolved from free text;
                                 # selected only via an explicit request.generator


def generate(params: dict, rng, theme: dict):
    import bpy
    from assetpipe.generators import common
    from assetpipe.loop import InfraError
    from assetpipe.neural import trellis_cache

    try:
        glb_path = trellis_cache.resolve_or_fail(
            cache_root=params["_cache_root"],       # injected via §9.3 param resolution
            image_path=params["reference_image"],
            model_version=params["model_version"],
            seed=params["seed"],
        )
    except trellis_cache.TrellisCacheMiss as exc:
        # A missing artifact is an infra gap (run the gen service), not an
        # asset-quality failure the fix loop should retry. Spec §4.3.
        raise InfraError(str(exc)) from exc

    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(glb_path))
    imported = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    root = common.join_meshes(imported, name="trellis_prop")   # small new helper

    # Re-join the deterministic pipeline: identical tail to props/crate.py.
    common.recenter_and_scale_to_bbox(root, theme)   # neural meshes arrive at
                                                      # arbitrary scale/origin
    common.freeze_transform(root)
    common.decimate_to_budget(root, budget=params["target_budget"])
    common.smart_uv_project(root, texture_resolution=params["texture_resolution"])
    return root
```

Two `common` helpers don't exist yet and are the only real in-Blender work:
`join_meshes(objs, name)` (neural output is often multi-object) and
`recenter_and_scale_to_bbox(obj, theme)` (procedural recipes are born at the
right scale/origin; neural meshes are not).

## 5. Integration cost

| Piece | Effort | Notes |
|---|---|---|
| `assetpipe/neural/trellis_cache.py` + tests | **done** | Shipped, 36 tests in CI |
| `generators/neural/trellis_prop.py` | **trivial** | The §4 module; registry auto-discovers it |
| `EXPECTED_RECIPES` entry in `test_recipes.py` | **trivial** | One line |
| `common.join_meshes`, `common.recenter_and_scale_to_bbox` | **small** | The only new in-Blender code |
| `reference_image` on the Asset Request (spec §6) + intake validation | **small–medium** | New input surface; a genuine contract change |
| `_cache_root` wiring through §9.3 param resolution + config | **small** | One config key, threaded like existing paths |
| Out-of-band **TRELLIS gen service** that calls `store()` | **large, external** | The CUDA half. Lives outside `assetpipe`; the pipeline only reads its output |
| Downstream stages X / V1 / R / V2 / F | **zero** | An imported+cleaned mesh is indistinguishable from a procedural one at the export boundary |

The `assetpipe`-side cost is genuinely small and all inside existing patterns.
The real weight is standing up and maintaining the GPU service that fills the
cache — deliberately quarantined outside the deterministic core.

## 6. What you keep, what you give up

**Preserved:** deterministic re-runs (cache hit), a GPU-free Blender/CI core,
the whole V1/V2 gate + repair loop, and the engine-neutral `.glb` boundary.
The neural lane is strictly opt-in — selected only by an explicit
`request.generator: "neural/trellis_prop"`, never auto-resolved (empty
`KEYWORDS`) — so it can't leak into procedural batches.

**Given up, by design:** the fix loop can repair *parameters and maps*, not
*shape*. Spec §16.4 only moves numeric params inside their bounds, and "the
neural mesh is wrong" is not a targeted fix. A bad TRELLIS mesh that fails V2
will exhaust the loop and land as flagged best-effort output with a
machine-written diagnosis (spec §16.6) — handled gracefully, but worth stating
plainly: **this backend feeds geometry V1/V2 can *judge*, not geometry the
loop can *fix*.**

## 7. Provenance of the idea

Prompted by an evaluation of [AISmith-3D](https://github.com/intisarGIT/AISmith-3D),
a Windows-local interactive studio that wraps TRELLIS (image→mesh), TRELLIS.2
(refine), and Mesh2Motion (rigging). AISmith itself is not adoptable here —
it is Windows/GPU-only, interactive, and human-in-the-loop, the opposite of
this pipeline's autonomous headless core. The reusable idea is narrower: the
**model it wraps** could sit behind this cache seam as an optional generator.
Mesh2Motion is likewise worth evaluating *directly* as prior art if the §1.2
"animation beyond a basic rig" non-goal is ever revisited.
