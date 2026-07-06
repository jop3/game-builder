---
name: blender-procedural-geometry
description: Procedural mesh generation and validation with Blender bmesh/bpy for game asset pipelines. Use when writing generator recipes that build props/kit pieces/characters in headless Blender, enforcing scene conventions (units, origins, applied transforms), running scriptable mesh-validity checks (manifold, degenerate faces, normals, self-intersection), measuring UV quality (overlap, stretch, texel density), decimating to poly budgets, generating LODs, or rigging stylized humanoids with automatic weights. Triggers on: bmesh, procedural modeling, mesh validation, non-manifold, UV unwrap automation, decimate, LOD generation, poly budget, armature auto-weights. Original skill authored for this repo's asset pipeline (docs/specs/asset-pipeline.md §9, §13).
---

# Blender Procedural Geometry (Generation + Validation)

## Overview

Expert knowledge for building game meshes **in code** (headless Blender 4.2 LTS, `bmesh`
preferred over `bpy.ops.mesh`) and for **validating them with scripts** — every check here
returns a measurable pass/fail, no eyeballing. Companion to `pbr-material-baking` (materials)
and `asset-visual-qa` (renders).

## Why bmesh, not bpy.ops.mesh

`bpy.ops.mesh.*` operators need a correct context (edit mode, active object, sometimes a
window) — brittle headless, and they carry hidden state (selection). `bmesh.ops.*` are pure
functions on a `BMesh`: deterministic, testable without a scene, and they return the created
geometry so you can keep building on it. Use `bpy.ops` only where no bmesh equivalent exists
(Smart UV Project, modifier apply, parenting).

### Core recipe pattern

```python
import bmesh, bpy
from mathutils import Matrix, Vector

def generate(params: dict, rng, theme: dict) -> bpy.types.Object:
    bm = bmesh.new()
    # 1. Build — every op returns dict of created geom; chain them:
    box = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, verts=box['verts'],
                    vec=(params['width_m'], params['depth_m'], params['height_m']))
    edges = [e for e in bm.edges if e.calc_length() > 0.1]          # select by query, not UI
    bmesh.ops.bevel(bm, geom=edges, offset=params['chamfer'],
                    segments=2, profile=0.7, affect='EDGES')
    # 2. Base at z=0 (origin convention): move geometry, not the object
    zmin = min(v.co.z for v in bm.verts)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, -zmin))
    # 3. Emit
    mesh = bpy.data.meshes.new(params['name']); bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(params['name'], mesh)
    bpy.context.collection.objects.link(obj)
    return obj
```

Useful builders: `create_cube`, `create_cone` (cylinders: same op, equal radii),
`create_uvsphere`, `create_grid`; shape with `extrude_face_region` + `translate`,
`inset_region` (panels), `solidify` (shells), `spin` (lathed profiles),
`boolean` (available as `bmesh.ops.boolean` in 4.x — prefer avoiding booleans; they are the
top producer of degenerate slivers and non-manifold edges; when unavoidable, run the full
cleanup pass after).

**Determinism discipline:** all randomness from the passed `rng` (`random.Random(seed)`).
Never `random.*` module-level, never `mathutils.noise` without a seeded offset, never
iteration over `dict`/`set` of Blender IDs where order feeds the RNG stream.

## Scene conventions (enforce, don't document)

- 1 BU = 1 m (`scene.unit_settings.system='METRIC'`, `scale_length=1.0`).
- Object transforms identity: build geometry at final world scale inside bmesh, so there is
  nothing to apply. If you did transform the object:
  `obj.data.transform(obj.matrix_basis); obj.matrix_basis = Matrix.Identity(4)`
  (works headless — avoids the `bpy.ops.object.transform_apply` context dance).
- Origin at base-center for props/kit (min-Z plane, XY centroid), feet for characters.
- Modular kit sockets: `bpy.data.objects.new("SOCKET_N_0", None)` empties parented to the
  root at exact 0.5 m grid coordinates.

## The finishing pass (always, in this order)

Order matters — e.g. triangulating before remove_doubles leaves needle triangles:

```python
bm = bmesh.new(); bm.from_mesh(obj.data)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-6)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)           # consistent outward normals
bmesh.ops.triangulate(bm, faces=bm.faces, quad_method='BEAUTY', ngon_method='BEAUTY')
bm.to_mesh(obj.data); bm.free()
obj.data.update()
```

Apply modifiers headless via the evaluated depsgraph, not `bpy.ops`:

```python
deps = bpy.context.evaluated_depsgraph_get()
obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(deps))
obj.modifiers.clear()
```

## Scriptable mesh validity checks

All on a fresh `bmesh` copy; each returns counts you compare to thresholds.

```python
bm = bmesh.new(); bm.from_mesh(obj.data)
bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()

non_manifold = [e for e in bm.edges if not e.is_manifold]
boundary     = [e for e in bm.edges if e.is_boundary]        # exempt if topology=="open"
wire_edges   = [e for e in bm.edges if not e.link_faces]     # never OK
loose_verts  = [v for v in bm.verts if not v.link_edges]
degenerate   = [f for f in bm.faces if f.calc_area() < 1e-8]
zero_edges   = [e for e in bm.edges if e.calc_length() < 1e-6]
```

**Normal consistency without mutating the asset:** copy the bmesh, snapshot normals, run
`recalc_face_normals` on the copy, count faces whose normal dot-flipped
(`n_old.dot(n_new) < 0`). Zero flips = consistent. If >0: recalc *is* the fix — apply it on
the real mesh once, re-check; still >0 means genuinely broken topology (usually a Möbius-like
strip from bad bridging) → regenerate, don't patch.

**Self-intersection (BVH):**

```python
from mathutils.bvhtree import BVHTree
tree = BVHTree.FromBMesh(bm, epsilon=0.0)
pairs = tree.overlap(tree)   # includes adjacent-face false positives — filter shared verts:
def shares_vert(i, j):
    return bool({v.index for v in bm.faces[i].verts} & {v.index for v in bm.faces[j].verts})
real = [(i, j) for i, j in pairs if i < j and not shares_vert(i, j)]
```

Treat as a warn-level metric (fraction of faces involved); intentional intersecting
sub-parts (greebles sunk into a hull) are legitimate — the threshold, not the check, encodes
tolerance.

**Triangle count:** `mesh.calc_loop_triangles(); tris = len(mesh.loop_triangles)` — count
*after* the finishing pass; quads count 2.

## UV automation and quality metrics

**Unwrap strategy order:** (1) recipe-placed seams (`edge.seam = True` on known feature
edges — best results, do this for kit pieces), then `bpy.ops.uv.unwrap(method='ANGLE_BASED')`;
(2) fallback `bpy.ops.uv.smart_project(angle_limit=radians(66), island_margin=margin)` —
**`angle_limit` is radians in 4.x**, a bare `66` silently produces garbage islands;
`island_margin` ≈ `4 / texture_resolution` keeps a 4-texel bake gutter. Both ops need edit
mode + everything selected:

```python
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
# ... uv op ...
bpy.ops.object.mode_set(mode='OBJECT')
```

(3) Tiling surfaces: skip unwrap; box-project at fixed world texel density (compute UV =
world coordinate on the dominant-axis plane × density / resolution) and flag the mesh
`obj.data['uv_mode'] = 'tiling'` so validators skip the 0–1 bounds check.

**Metrics (per face, via loop triangles):**

```python
uv = bm.loops.layers.uv.active
def uv_area(f):     # shoelace over the face's UV polygon (faces are tris post-finishing)
    a, b, c = (l[uv].uv for l in f.loops)
    return abs((b - a).cross(c - a)) / 2
texel_density = [sqrt(uv_area(f) / f.calc_area()) for f in bm.faces if f.calc_area() > 1e-12]
# density uniformity: p95/p5 ratio; stretch: per-edge |uv_len/world_len| max/min per face
```

**Island overlap** — rasterize, don't do polygon clipping: draw every UV triangle into a
1024² uint8 accumulation buffer (your own scanline fill or `PIL.ImageDraw` per triangle into
a count array); texels with count ≥ 2 ÷ texels with count ≥ 1 = overlap fraction. Robust,
fast, and matches what actually happens at bake time. Mirrored-island exemptions: recipes
tag mirrored islands via a face int layer, subtract their texels before the ratio.

## Budget enforcement and LODs

Decimate-to-budget (never guess the ratio once):

```python
while tri_count(obj) > budget and steps < 5:
    m = obj.modifiers.new('Dec', 'DECIMATE')
    m.ratio = max(0.5, budget / tri_count(obj)) * 0.97   # aim slightly under
    m.use_collapse_triangulate = True
    apply_modifiers(obj)                                  # depsgraph method above
```

Decimation is the top producer of degenerate/sliver triangles — **re-run the full validity
check set on every decimated result, including each LOD**. LODs: duplicate
(`obj.copy()` + `obj.data.copy()`), decimate to ratio, name `<asset>_LOD1`… Keep LODs as
siblings in the export collection; engines/adapters decide what to do with them. UVs survive
collapse decimation but texel density drifts — LOD UV checks should use the warn thresholds
only.

## Stylized humanoid rigging (v1 scope: rest pose, no animation)

- Build the armature programmatically with **Godot `SkeletonProfileHumanoid` bone names**
  (`Hips, Spine, Chest, UpperChest, Neck, Head, LeftShoulder, LeftUpperArm, LeftLowerArm,
  LeftHand, LeftUpperLeg, LeftLowerLeg, LeftFoot, LeftToes`, + Right*) — Godot's humanoid
  retargeting then works with zero mapping config.
- Skin with automatic weights: select mesh then armature (armature active),
  `bpy.ops.object.parent_set(type='ARMATURE_AUTO')`. Headless caveat: this op needs both
  objects in the view layer and visible.
- Then enforce the glTF/game contract:
  `bpy.ops.object.vertex_group_limit_total(limit=4)` and
  `bpy.ops.object.vertex_group_normalize_all(lock_active=False)` (mesh active, weight-paint
  context not required in 4.x object mode).
- Validate: every vertex total weight ∈ [0.999, 1.001]; ≤ 4 nonzero groups per vertex; no
  vertex with max weight < 0.1 (orphaned — auto-weights failed there, usually a mesh part
  disconnected from any bone envelope; fix by widening the part or explicit group assign).

## Pitfall checklist

- `ensure_lookup_table()` before any indexed access after topology edits, or you get
  "outdated internal index table" errors.
- `bm.to_mesh()` does not free — always `bm.free()`; leaked BMesh in a loop = memory blowup
  over a batch.
- `bmesh.ops.bevel` with `offset` bigger than local edge spacing self-intersects; clamp
  chamfer params against min edge length in the recipe schema.
- Smart UV Project on a mesh with degenerate faces hangs or emits NaN UVs — validity checks
  run *before* unwrap, always.
- NaN guard: after generation, assert no NaN in `v.co` and no NaN UVs
  (`math.isnan(uv.x)`) — booleans and extreme bevels produce them silently.
- `dissolve_degenerate` can open a hole in `topology=="closed"` meshes (it deletes the
  sliver) — follow with hole-fill: collect boundary loops and `bmesh.ops.holes_fill`.
- Object mode ops (`mode_set`) fail if the object isn't in the current view layer — link to
  `bpy.context.collection` first, and keep exactly one window/scene assumption out of
  library code.

## Relationship to the pipeline spec

Implements `docs/specs/asset-pipeline.md` §9 (Stage G recipes, finishing pass, UV modes,
rigging) and §13.1–13.2 (checks S1–S12) with the concrete APIs and thresholds rationale.
