---
name: asset-visual-qa
description: Autonomous visual QA for 3D game assets - deterministic headless Blender render harnesses, scripted image analytics, glTF structural validation, and vision-model inspection with structured rubrics. Use when rendering turntables/debug passes headlessly, building contact sheets for model inspection, writing NumPy pixel checks (black textures, backface detection, tiling seams, clipping), validating glTF files programmatically, designing vision-model QA rubrics with forced structured output, or hardening a render-inspect-fix loop against false positives. Triggers on: turntable render, headless render, visual verification, vision QA rubric, render inspection, image diff checks, glTF validation, contact sheet. Original skill authored for this repo's asset pipeline (docs/specs/asset-pipeline.md §13.5-§15).
---

# Asset Visual QA (Headless Renders + Scripted Analytics + Vision Rubrics)

## Overview

Expert knowledge for verifying 3D assets **with no human in the loop**: deterministic
headless renders, cheap NumPy pixel checks first, then a vision model judging against an
explicit rubric with forced structured output. The layering principle: **anything measurable
in pixels is a script check; the vision model only judges what needs perception** (does it
read as a crate, does the metal look like metal). Blender 4.2 LTS assumed.

## Deterministic headless rendering

### Settings block (pin everything that affects pixels)

```python
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'                      # GPU ≠ bit-identical across machines
scene.cycles.samples = 128
scene.cycles.seed = 0
scene.cycles.use_animated_seed = False
scene.cycles.use_denoising = True
scene.cycles.denoiser = 'OPENIMAGEDENOISE'
scene.view_settings.view_transform = 'AgX'       # pin explicitly; defaults drift across versions
scene.view_settings.look = 'None'
scene.render.resolution_x = scene.render.resolution_y = 1024
scene.render.image_settings.file_format = 'PNG'
scene.render.film_transparent = False
```

Render the **exported .glb re-imported into a clean scene**
(`bpy.ops.import_scene.gltf(filepath=...)`), never the authoring .blend — the whole point is
to see what engines will see (broken texture links, missing tangents, wrong color spaces
all become visible only post-export).

### Camera framing from the bounding box (no viewport ops)

`bpy.ops.view3d.camera_to_view_selected` needs a window context — unusable headless.
Compute instead:

```python
import math
from mathutils import Vector
def frame_object(cam_obj, target_bbox_min, target_bbox_max, azimuth_deg, elevation_deg,
                 fill=0.65):                     # asset fills ~65% of frame height
    center = (target_bbox_min + target_bbox_max) / 2
    radius = (target_bbox_max - target_bbox_min).length / 2
    fov = cam_obj.data.angle                     # radians
    dist = radius / (fill * math.tan(fov / 2))
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    offset = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    cam_obj.location = center + offset * dist
    look = center - cam_obj.location
    cam_obj.rotation_euler = look.to_track_quat('-Z', 'Y').to_euler()
```

Scene furniture that makes checks objective: an 18%-grey ground plane, and a **1 m
reference cube** at a known offset — scale judgments become "compare to the labeled cube",
not a guess.

### Debug passes via material override

`view_layer.material_override` replaces every material for one render — no scene surgery:

```python
def backface_debug_material():
    mat = new_material("DBG_backface")           # helper from pbr-material-baking skill
    nt = mat.node_tree
    geo = nt.nodes.new('ShaderNodeNewGeometry')
    red = nt.nodes.new('ShaderNodeEmission'); red.inputs['Color'].default_value = (1,0,0,1)
    nrm = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(geo.outputs['Normal'], nrm.inputs['Color'])   # normal-as-RGB
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(geo.outputs['Backfacing'], mix.inputs['Fac'])
    nt.links.new(nrm.outputs['Emission'], mix.inputs[1])
    nt.links.new(red.outputs['Emission'], mix.inputs[2])       # backfacing → pure red
    # wire mix → output
    return mat
bpy.context.view_layer.material_override = backface_debug_material()
```

Same mechanism for: **UV-checker pass** (8×8 checker texture on UVs), **silhouette pass**
(white emission override + black world + film settings). Reset `material_override = None`
after. These passes turn "are normals inverted?" and "is anything in frame?" into pixel
counting.

### Lighting rigs

Three fixed rigs, switched per view: L1 neutral studio HDRI (bundle one license-clean .exr,
`world.node_tree` Environment Texture, strength 1.0); L2 warm sun
(`light.data.type='SUN'`, ~4500 K via blackbody node, 45° elevation) + weak fill; L3 dim
blue rim only — the stress rig: black/missing textures and dead emissive hide in bright
renders and are obvious under rim light.

## Scripted image analytics (run before any model call)

Cheap, objective, zero-API-cost. NumPy on the PNGs:

```python
import numpy as np
from PIL import Image
def load(p): return np.asarray(Image.open(p).convert("RGB"), np.float32) / 255.0

def not_empty(img):            # A1: catches black frames, missing asset, blown render
    return img.std() > 2/255 and 0.01 < img.mean() < 0.99

def backface_fraction(img):    # A2: on the backface-debug pass — red = backfacing
    r, g, b = img[...,0], img[...,1], img[...,2]
    return float(((r > 0.9) & (g < 0.1) & (b < 0.1)).mean())     # fail if > 0.001

def silhouette_area(img):      # A3: white-on-black silhouette pass
    return float((img.mean(-1) > 0.5).mean())                    # sane range 0.05–0.85

def clipped_fraction(img):     # A4: blown highlights (warn)
    return float((img >= 254/255).all(-1).mean())

def edge_wrap_diff(img, axis): # tiling: opposite 4px strips must match (≤ 2/255)
    a = img.take(range(4), axis=axis); b = img.take(range(-4, 0), axis=axis)
    return float(np.abs(a - np.flip(b, axis=axis)).mean())

def rolled_seam_spike(img):    # tiling: roll 50%, gradient along former seam vs global median
    r = np.roll(img, img.shape[0]//2, axis=0)
    gy = np.abs(np.diff(r.mean(-1), axis=0))
    seam_row = img.shape[0]//2 - 1
    return float(gy[seam_row].mean() / (np.median(gy) + 1e-6))   # fail if > 1.5
```

## glTF structural validation

Two layers:

1. **Khronos glTF-Validator** CLI — the authority on spec compliance. Run it, parse the JSON
   report, gate on `issues.numErrors == 0` (and treat severity-0/1 messages as blockers).
   Flag names vary between builds — probe `--help` at pipeline startup and fail fast if the
   binary is missing, rather than shelling blind per asset.
2. **Inventory checks in pure Python** — a GLB is a 12-byte header + chunks; chunk 0 is the
   glTF JSON. No dependency needed:

```python
import json, struct
def glb_json(path):
    with open(path, 'rb') as f:
        magic, _ver, _len = struct.unpack('<III', f.read(12))
        assert magic == 0x46546C67, "not a GLB"
        clen, ctype = struct.unpack('<II', f.read(8))
        assert ctype == 0x4E4F534A, "first chunk must be JSON"
        return json.loads(f.read(clen))
g = glb_json("asset.glb")
exts = set(g.get("extensionsUsed", []))          # whitelist check
mesh_names = [m.get("name","") for m in g.get("meshes", [])]      # LOD siblings present?
has_tangents = all("TANGENT" in p["attributes"]
                   for m in g.get("meshes", []) for p in m["primitives"]
                   if "normalTexture" in str(g["materials"][p.get("material", 0)]))
imgs = g.get("images", [])                        # count + mimeType vs expectations
```

Gate on: extension whitelist, expected mesh/material/texture counts, tangents present
wherever a normal map is referenced, file size ≤ budget.

## Vision-model inspection

### Call shape (Anthropic API, forced structured output)

```python
import anthropic, base64
client = anthropic.Anthropic()
resp = client.messages.create(
    model="claude-fable-5", max_tokens=4096, temperature=0,
    tools=[{"name": "report_inspection", "description": "Report QA verdicts",
            "input_schema": REPORT_SCHEMA}],           # your full JSON schema
    tool_choice={"type": "tool", "name": "report_inspection"},   # forced — no prose replies
    messages=[{"role": "user", "content": [
        *[{"type": "image", "source": {"type": "base64", "media_type": "image/png",
           "data": base64.b64encode(open(p, "rb").read()).decode()}} for p in sheets],
        {"type": "text", "text": prompt},
    ]}])
report = next(b.input for b in resp.content if b.type == "tool_use")
```

Then **schema-validate `report` yourself** — forced tool use guarantees shape, not
completeness (e.g. "every applicable check appears exactly once"). One retry on validation
failure; after that it's an infrastructure error, not an asset verdict.

Image sizing: the API downscales anything over ~1568 px on the long edge — composite contact
sheets so each cell is still legible **after** that (2×3 grid of 1024² cells → send as two
sheets rather than one 3072-wide sheet). **Burn the `view_id` into each cell** (PIL
`ImageDraw.text` on a corner strip): the model must cite view ids, and burned labels remove
"which image is turn_045" ambiguity entirely.

### Rubric design principles (what makes verdicts trustworthy)

- **One check = one question with a stated pass criterion**, tied to named views. "No visible
  UV-seam discontinuity where the surface is geometrically continuous (judge in close_034,
  turn_*)" — not "check texture quality".
- **Evidence or it didn't happen:** a `fail` must cite ≥1 view_id + a location phrase.
  Geometry-class defects must be visible in ≥2 views, else the verdict is `uncertain`.
  This single rule kills most hallucinated defects.
- **Closed defect vocabulary:** `defect_type` must come from your taxonomy enum — it's what
  makes fail verdicts machine-actionable (defect→fix table lookup).
- **Tell the model what's deliberate** or it will report your harness as defects: the dark
  side under the rim-light rig is expected; the UV-checker pass deliberately shows a
  checker; silhouette passes are white-on-black by design; the grey cube is a 1 m reference.
- **`uncertain` is a first-class verdict** with a defined resolution policy: re-query that
  check once with full-res crops of the cited view; still uncertain → fail-safe (treat as
  fail). Never let "not sure" silently pass.
- **Severity split (blocker/warn) belongs to the check, not the model.** The model reports
  observations; your config decides what gates.
- Temperature 0, and log the full request/response (image refs by path+SHA-256, not bytes)
  per call — rubric drift across model versions is real; a labeled regression set of
  renders with expected verdicts is the only way to notice it.

### False-positive/negative economics

Scripted checks are the floor (objective defects can't slip past), the vision model is the
ceiling (perceptual defects a script can't express). Order by cost: pixel checks (free) →
static/glTF checks (cheap) → vision (API cost) — and short-circuit: never spend a vision
call on an asset that already failed static checks; fix loops re-enter at the static gate.

## Pitfall checklist

- Renders differ across machines → GPU device or unpinned view transform; CPU + explicit
  AgX fixes both.
- Everything renders magenta after glb import → textures didn't embed at export
  (`export_image_format` / packed-image issue) — that's a *finding*, which is exactly why
  you render the re-imported glb.
- OIDN denoising smears tiny emissive details at low sample counts — if emissive checks
  flap, raise samples for the dark-rig views instead of disabling denoise.
- `material_override` doesn't apply to objects with material slots linked to *object* data
  in some edge cases — assign via override and verify with a 1-pixel probe render in CI
  once, not per asset.
- Contact sheet JPEG compression creates faint block artifacts the model may flag as
  banding — send PNG.
- A model told to "find defects" *will find defects*: always phrase checks as pass criteria
  with an explicit "report pass if…" path, and include a known-good asset in your
  regression set to measure the false-positive rate.

## Relationship to the pipeline spec

Implements `docs/specs/asset-pipeline.md` §13.5 (S20), §14 (Stage R render harness, A1–A4)
and §15 (Stage V2 rubric, schema, uncertainty policy); the anti-false-positive rules here
are the rationale behind §15.3 and the regression targets in §21.3.
