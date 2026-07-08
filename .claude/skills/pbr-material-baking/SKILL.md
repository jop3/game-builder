---
name: pbr-material-baking
description: Procedural PBR material synthesis and texture baking in headless Blender for game asset pipelines. Use when building Blender shader node graphs via Python, baking albedo/normal/roughness/metallic/AO maps with Cycles, packing ORM textures, generating seamless tiling textures with periodic noise, or debugging bake artifacts (black bakes, seams, banding, wrong color spaces). Triggers on: texture baking, PBR maps, ORM packing, procedural materials, shader nodes via bpy, seamless textures, normal map baking, edge wear masks. Original skill authored for this repo's asset pipeline (docs/specs/asset-pipeline.md §10) — no verified upstream skill covers this domain.
---

# PBR Material Baking (Procedural, Headless Blender)

## Overview

Expert knowledge for generating game-ready PBR texture sets **procedurally** in Blender —
shader node graphs built by Python, baked to maps with Cycles — with no hand-painting, no
image-generation models, and full determinism (same seed → same pixels). Written for
Blender **4.2 LTS**; version-specific API notes are called out.

**When to use this skill:**
- Building material node graphs programmatically (`material.node_tree` via `bpy`)
- Baking PBR channels (albedo, tangent normal, roughness, metallic, AO, emissive)
- Packing ORM (occlusion/roughness/metallic) textures
- Creating seamless tiling textures that are *mathematically* periodic
- Debugging bakes: black output, visible seams, banding, washed-out normals

## The glTF PBR contract (get this right first)

Every map you bake targets glTF 2.0 metallic-roughness. The channel/color-space contract:

| Map | glTF slot | Color space in Blender | Notes |
|---|---|---|---|
| `albedo.png` | `baseColorTexture` | **sRGB** | No lighting/AO baked in — color only |
| `normal.png` | `normalTexture` | **Non-Color** | Tangent space, **OpenGL +Y green** |
| `orm.png` | `occlusionTexture` (R) + `metallicRoughnessTexture` (G=rough, B=metal) — same image, both slots | **Non-Color** | One texture, referenced twice |
| `emissive.png` | `emissiveTexture` | **sRGB** | Pair with `KHR_materials_emissive_strength` for >1.0 |

Cardinal color-space rules (the #1 source of "materials look wrong in engine"):
- Set `image.colorspace_settings.name = 'Non-Color'` on normal/ORM images **before baking
  and before wiring them into any preview material**. An sRGB-interpreted normal map shifts
  every normal and shows as blotchy lighting.
- Albedo bakes must use `DIFFUSE` with `pass_filter={'COLOR'}` — never `COMBINED` — or you
  bake lighting into the color map and the asset double-lights in engine.

## Building node graphs from Python

Standard pattern — build into a fresh tree, never rely on default nodes existing:

```python
def new_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()                      # default Principled's location varies; start clean
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return mat

def link(nt, from_node, from_sock, to_node, to_sock):
    nt.links.new(from_node.outputs[from_sock], to_node.inputs[to_sock])
```

**Blender 4.x API landmines:**
- **Musgrave was removed in 4.1.** Any 3.x recipe using `ShaderNodeTexMusgrave` must use
  `ShaderNodeTexNoise` instead (it gained Lacunarity; combine with Detail/Roughness inputs
  for fBM-style results).
- Principled BSDF sockets were renamed in 4.0: `'Specular'` → `'Specular IOR Level'`,
  `'Emission'` → `'Emission Color'`, `'Transmission'` → `'Transmission Weight'`,
  `'Subsurface'` → `'Subsurface Weight'`. Index-based access breaks across versions —
  always address sockets by current name; wrap in a helper that raises a clear error.
- Node `location` is cosmetic; skip layout entirely in headless code.

### Reusable mask building blocks

**Edge wear** — the workhorse of "used"-looking materials. Two options:

1. *Bevel-normal difference* (works on low-poly, Cycles-only): add `ShaderNodeBevel`
   (radius ≈ 0.5–2 cm), take `dot(Normal, BevelNormal)` via Vector Math DOT, invert and
   sharpen with a Map Range/ColorRamp. Convex edges → mask ≈ 1. This is the reliable
   method for game-res meshes.
2. *Pointiness* (`ShaderNodeNewGeometry` → `Pointiness`): **vertex-interpolated — useless
   on low-poly meshes** where edges have no intermediate vertices. Only use on dense/
   subdivided bake meshes. Prefer option 1.

**Cavity/crevice dirt**: `ShaderNodeAmbientOcclusion` (set `inside=False`, distance a few
cm), invert → multiply grunge into albedo + increase roughness in cavities.

**Panel lines / grooves**: `ShaderNodeTexBrick` with mortar width as the groove; use the
mortar mask to darken albedo AND feed a Bump node (grooves read in the normal map for free).

**Grunge**: two `ShaderNodeTexNoise` at different scales (e.g. 4.0 and 37.0), multiplied,
through a ColorRamp with a narrow ramp window — the classic layered-breakup mask.

**Height → normal**: wire all height-like signals into ONE `ShaderNodeBump`
(`Strength` 0.1–0.5) feeding the Principled `Normal` input. Bake the surface's `NORMAL`
pass afterward — do **not** bake height and convert to normal in post; the bump-then-bake
route gets tangent space right with zero extra code.

### Determinism

Blender noise nodes are deterministic by construction (pure functions of position + params).
Your randomness lives in *parameter choice only*: derive every scale/seed/color pick from
`random.Random(seed)`. Vary noise "seed" by adding a large seeded offset to the texture
coordinate vector (Mapping node translation), not by expecting a seed socket.

## Baking

### Setup invariants (every bake)

```python
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'          # deterministic; GPU trades reproducibility for speed
scene.cycles.seed = 0
scene.render.bake.margin = 8         # px bleed past island borders — prevents seams at mips
scene.render.bake.use_selected_to_active = False   # self-bake (procedural → own UVs)
```

The bake target is selected implicitly: the **active** Image Texture node in the material's
node tree, holding the target image. Create it, set `nt.nodes.active = tex_node`, and make
sure the object is selected + active in the view layer. Forgetting `nodes.active` bakes into
whatever node happens to be active — a classic silent-wrong-output bug.

```python
img = bpy.data.images.new(name, width=res, height=res, alpha=False, float_buffer=True)
img.colorspace_settings.name = 'Non-Color'   # or 'sRGB' for albedo/emissive
tex_node = nt.nodes.new('ShaderNodeTexImage'); tex_node.image = img
nt.nodes.active = tex_node
```

Bake into a **float buffer** and save via a 16-bit intermediate; quantize to 8-bit with
dithering at the end (see Banding, below).

### Per-channel recipes

**Albedo** — `bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'})`, 16 samples is
plenty (color pass is noise-free; samples only matter for lighting passes).

**Tangent normal** — glTF wants OpenGL +Y:

```python
scene.render.bake.normal_space = 'TANGENT'
scene.render.bake.normal_r, scene.render.bake.normal_g, scene.render.bake.normal_b = \
    'POS_X', 'POS_Y', 'POS_Z'          # POS_Y = OpenGL. DirectX (NEG_Y) will look inverted
bpy.ops.object.bake(type='NORMAL')     # in glTF viewers/Godot: lighting flips on Y-facing slopes
```

Bake normals from the **final game mesh with its final UVs and triangulation** — tangent
space depends on both; baking before triangulation then triangulating after shifts tangents
and creates faint shading seams.

**Scalar channels (roughness, metallic) — the EMIT-reroute trick.** Cycles has no direct
"bake this socket" for arbitrary scalars. Temporarily rewire:

```python
def bake_scalar(nt, scalar_output_socket, bsdf, out_node):
    emit = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(scalar_output_socket, emit.inputs['Color'])   # scalar → greyscale color
    old_link = out_node.inputs['Surface'].links[0]
    nt.links.new(emit.outputs['Emission'], out_node.inputs['Surface'])
    bpy.ops.object.bake(type='EMIT')                            # exact, 1 sample would do
    nt.links.new(bsdf.outputs['BSDF'], out_node.inputs['Surface'])  # restore
    nt.nodes.remove(emit)
```

`EMIT` bakes are exact and sample-independent — perfect for masks/scalars. This is also how
you bake *any* intermediate mask for debugging.

**AO** — `bpy.ops.object.bake(type='AO')` with `scene.cycles.samples = 64` minimum (AO is a
real Monte-Carlo pass; undersampled AO is *noise you will ship*). If the asset is a single
convex-ish prop, a constant 1.0 AO channel is acceptable and free.

**Emissive** — bake `EMIT` with the *real* emission wiring active (no reroute needed).

### ORM packing (NumPy, outside Blender)

```python
import numpy as np
from PIL import Image
ao   = np.asarray(Image.open("ao.png").convert("L"), np.float32)
rough= np.asarray(Image.open("rough.png").convert("L"), np.float32)
metal= np.asarray(Image.open("metal.png").convert("L"), np.float32)
orm = np.stack([ao, rough, metal], axis=-1).astype(np.uint8)   # R=AO G=rough B=metal
Image.fromarray(orm, "RGB").save("orm.png")                     # no alpha channel — ever
```

Post-pack sanity: renormalize normal maps per-pixel (`xyz / |xyz|`, remap to 0–255); snap
metallic toward {0,1} unless the recipe explicitly declares blended metal — mid-grey metal
is almost always an authoring bug and renders as "dull plastic".

## Seamless tiling textures (mathematically periodic)

Baking a plane and hoping edges match does not work with world-space noise. Make the noise
itself periodic by embedding UV as **two circles in 4D** — Noise/Voronoi accept a 3D vector
+ a W scalar = 4 inputs, which is exactly enough:

```
angle_u = u * 2π          angle_v = v * 2π       (Math nodes)
vector  = CombineXYZ( R·cos(angle_u), R·sin(angle_u), R·cos(angle_v) )
W       = R·sin(angle_v)
→ feed vector+W into Noise Texture (4D) / Voronoi (4D)
```

Result is *exactly* periodic over the 0–1 tile in both axes — no blending, no visible fold.
Notes:
- `R` (circle radius) controls feature scale; the noise node's own Scale stays at 1.
  Multiply the periods (`angle * n`) for n× repeats within the tile.
- Distortion in this domain is fine; adding a *non-periodic* second noise on top silently
  breaks tiling. Every texture node in a TILING recipe must go through periodic coords —
  enforce with a shared `PeriodicCoords` node group and a lint that walks the tree.
- Brick/Checker are natively periodic **iff** the tile contains an integer number of
  cells — snap their scale to integers.
- Anisotropy: the 4D-torus embedding slightly distorts noise "grain" compared to flat
  3D noise. Invisible at typical scales; if a recipe looks warped, increase R.

Always **verify, never assume**: opposite 4-px edge strips must match within 2/255 mean
abs diff, and a 50%-rolled copy must show no gradient spike along the former seam.

## Banding, bit depth, and dithering

Bakes of smooth ramps (roughness gradients, AO) band visibly when quantized straight to
8-bit — and banding is a defect the visual QA stage flags. Pipeline: bake to float
(`float_buffer=True`) → save intermediate as 16-bit PNG
(`scene.render.image_settings.color_depth = '16'`) or keep in NumPy float → quantize to
8-bit **with seeded blue-noise/triangular dither** (add `rng.uniform(-0.5, 0.5)` per pixel
before rounding). Deterministic because the RNG is seeded.

## Near-black / near-white albedo is staging-coupled

A "make it read pure black" material is a lighting contract, not just an albedo value.
An albedo at the S16 luminance floor (~2.5%) only *reads* black under a **moderate,
controlled** key. Flat over-bright even lighting (a white studio dome, ambient energy
turned up "so we can see it") lifts a 2.5%-albedo surface to muddy grey and destroys the
black-vs-white contrast — the material is fine; the scene killed it. Lessons that held up
building the Reversi disc set:

- Bake the black just above the floor (`To Min≈0.020`, `To Max≈0.026`) and the white just
  below the blown ceiling (`0.94–0.985`), each with a **low roughness** so the contrast
  comes from a *sharp specular dot*, not from lifting the base value. A glossy near-black
  with one bright glint reads far blacker than a matte dark-grey.
- Push readability into the *scene*, not the albedo: keep a light backdrop but a single
  controlled directional key + low ambient (energy ~0.1). If a reviewer says "the black
  looks grey," suspect the lights before you raise the albedo.
- For a genuinely flat piece, declare `flat_color` so S16 enforces only the luminance
  floor (not the std-dev "must have variance" rule) — otherwise you're forced to add
  noise that fights the clean look you want.

## Layered value noise: blend additively, not multiplicatively

To give a matte surface visible texture (felt weave, fabric nap) mix **two noise scales**
— a tight weave over a broad mottle. Blend them with a `ShaderNodeMix` in **FLOAT** mode
(average), *not* a MULTIPLY: multiplying two <1 factors concentrates toward dark and the
"texture" collapses into blotches while the mean drops. Average keeps both scales
contributing spread around the mean. Then a **wide** `MapRange` value band (e.g.
`0.45 → 1.5`) plus a bump keyed to the *fine* scale makes the weave actually visible
rather than reading as a flat coloured card. Verify with a bake-smoke (`mean`, `std`): a
deep felt landed at `mean≈0.095 std≈0.006` — low mean (deep colour) but non-zero std
(real texture).

## Pitfall checklist

- **Black bake** → target image node not active, object not selected+active, or baking
  `DIFFUSE` on a fully-metallic surface (metals have no diffuse — bake albedo from the
  Base Color socket via EMIT-reroute instead for metal-heavy materials).
- **Seams at UV islands in engine but not in Blender** → margin too small (< 4 px), or
  normals baked pre-triangulation (see above), or engine regenerating tangents — export
  tangents in the glTF.
- **Washed-out / grey normal map** → image was sRGB during bake. Recreate as Non-Color;
  you cannot fix it by reassigning after the bake.
- **Normal map looks inverted on one axis in Godot** → baked DirectX (NEG_Y). glTF/Godot
  expect +Y.
- **AO/lighting visible in albedo** → used `COMBINED` or forgot `pass_filter={'COLOR'}`.
- **Noise/grain in flat areas** → undersampled AO bake, or denoiser ON for bakes
  (denoising is for renders; keep bakes raw and use enough samples).
- **Different pixels across machines** → GPU device, or Blender minor-version drift
  (noise implementations change between majors). Pin Blender, bake on CPU.
- **Huge .blend after many bakes** → `bpy.data.images` accumulate; remove intermediates
  (`bpy.data.images.remove(img)`) after saving to disk.

## Relationship to the pipeline spec

Implements the knowledge for `docs/specs/asset-pipeline.md` §10 (Stage M), §13.3–13.4
(texture/tiling validation thresholds) and feeds §12.2 (baked-map → glTF material
assignment). The spec's `matlib` building blocks (`EdgeWear`, `PanelLines`, `Grunge`,
`PeriodicCoords`) are the node-group formalizations of the masks above.
