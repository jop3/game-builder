---
name: godot-asset-import
description: Godot 4 headless asset import automation and verification for glTF-based pipelines. Use when importing .glb assets into a Godot project from the command line, writing EditorScenePostImport scripts, using glTF name-suffix conventions (-col, -convcol, -occ, -noimp) for collision/occlusion generation, verifying imports with headless SceneTree scripts, configuring importer defaults in project.godot, or delivering skyboxes (PanoramaSkyMaterial) and parallax backgrounds. Triggers on: godot --headless, godot import, EditorScenePostImport, glb import Godot, collision suffix, verify_import, PanoramaSkyMaterial, Godot CI. Original skill authored for this repo's asset pipeline (docs/specs/asset-pipeline.md §19).
---

# Godot Asset Import (Headless, Scripted, Verified)

## Overview

Expert knowledge for the engine-adapter side of a glTF pipeline: getting `.glb` files into a
Godot **4.3+** project from the command line, controlling import behavior without touching
the editor UI, and *verifying* — with a script that exits 0/1 — that the asset actually
loads, instantiates, and matches its manifest. Everything here runs under `--headless` in CI.

## The import model (what actually happens)

Godot never uses your source file at runtime — dropping `asset.glb` under `res://` does
nothing until the **editor import step** converts it into resources under `.godot/imported/`
and writes a sidecar `asset.glb.import`. Headless trigger:

```bash
godot --headless --path /path/to/project --import
```

Facts that save hours:
- `--import` (4.2+) imports all pending resources and exits. On a **fresh project/checkout**
  (no `.godot/` dir) the first run also builds the global class cache and may report
  errors that self-resolve — run `--import` **twice** on cold CI checkouts and judge the
  second run's output.
- Exit code alone is not a reliable failure signal for individual assets — **capture stderr
  and scan for `ERROR:` lines mentioning your delivered paths**; treat any as a delivery
  failure for that asset.
- To force reimport of one asset: delete its `<file>.import` sidecar *and* its entries under
  `.godot/imported/`, then rerun `--import`. Editing the source file's mtime/hash also
  triggers it.
- The `.import` sidecar is a config you *can* write by hand, but its keys drift across Godot
  minor versions — prefer **project-level importer defaults + name suffixes + a post-import
  script** (all stable APIs) over generated sidecars.

## Name-suffix conventions (author-side control, zero config)

The glTF importer honors suffixes on **object names inside the glb** — set them in Blender
before export; nothing to configure in Godot:

| Suffix | Effect on import |
|---|---|
| `-col` | Mesh + `StaticBody3D` child with trimesh (`ConcavePolygonShape3D`) collision |
| `-convcol` | Mesh + convex collision (`ConvexPolygonShape3D`) |
| `-colonly` | Node becomes *only* a `StaticBody3D` collider — mesh not rendered (invisible collision proxies) |
| `-convcolonly` | As above, convex |
| `-occ` / `-occonly` | Generates an `OccluderInstance3D` (with / without keeping the mesh) |
| `-rigid` | `RigidBody3D` with convex shape |
| `-navmesh` | Converted to a `NavigationRegion3D` mesh |
| `-noimp` | Node skipped entirely |
| `-loop` / `-cycle` (animations) | Marks the clip looping |

Policy mapping for a pipeline: props → `-convcol` (cheap, dynamic-friendly), kit/environment
→ `-col` (exact static), characters → no suffix (capsule added in-engine). Note the suffix
applies to the *node* name; when a Blender object and its mesh share the name, suffix the
object.

## Project-level import defaults + post-import script

Set once in `project.godot` (the adapter can append this section idempotently):

```ini
[importer_defaults]

scene={
"nodes/import_script/path": "res://assets/generated/_pipeline/post_import.gd"
}
```

The post-import script runs inside the import step for every scene (glb) and is where
per-asset logic lives — it can read a sibling manifest for data-driven behavior:

```gdscript
@tool
extends EditorScenePostImport

func _post_import(scene: Node) -> Object:
    var manifest_path := get_source_file().get_basename() + ".manifest.json"
    var manifest := {}
    if FileAccess.file_exists(manifest_path):
        manifest = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
    _walk(scene, manifest)
    scene.name = get_source_file().get_file().get_basename().to_pascal_case()
    return scene

func _walk(node: Node, manifest: Dictionary) -> void:
    if node is MeshInstance3D:
        if str(node.name).contains("_LOD"):          # pipeline LODs: strip, Godot auto-LODs
            node.get_parent().remove_child(node); node.queue_free(); return
        if manifest.get("category", "") in ["modular_kit_piece", "environment_piece"]:
            node.gi_mode = GeometryInstance3D.GI_MODE_STATIC
    for c in node.get_children():
        _walk(c, manifest)
```

Gotchas: the script must be `@tool`; `queue_free()` on stripped nodes (plain `free()` during
import can crash); return the scene or import produces an empty asset.

## Headless verification (the adapter's `verify()`)

A `SceneTree` script is the cleanest headless entrypoint — `_init()` runs, you `quit(code)`:

```bash
godot --headless --path /path/to/project \
      --script res://assets/generated/_pipeline/verify_import.gd \
      -- res://assets/generated/scifi/prop_small/scifi_crate_small_01/scifi_crate_small_01.glb
```

```gdscript
extends SceneTree

func _init() -> void:
    var args := OS.get_cmdline_user_args()        # everything after the bare `--`
    var report := {"asset": args[0], "checks": [], "pass": true}
    var ps: PackedScene = load(args[0])
    _check(report, "loads_as_packed_scene", ps != null)
    if ps == null: _finish(report); return
    var root := ps.instantiate()
    var meshes: Array[MeshInstance3D] = []
    _collect(root, meshes)
    _check(report, "has_mesh_instance", meshes.size() >= 1)
    var manifest := _load_manifest(args[0])
    for mi in meshes:
        for s in mi.mesh.get_surface_count():
            var mat := mi.mesh.surface_get_material(s)
            _check(report, "material_is_standard", mat is BaseMaterial3D)
            if mat is BaseMaterial3D and manifest.get("stats", {}).get("textures", {}).has("albedo"):
                var tex: Texture2D = mat.albedo_texture
                _check(report, "albedo_texture_present", tex != null)
                if tex: _check(report, "albedo_within_budget",
                    int(tex.get_size().x) <= int(manifest.stats.textures.albedo))
    var wants_collision: bool = manifest.get("collision", "convex") != "none"
    _check(report, "collision_matches_request",
           _has_node_of(root, "CollisionShape3D") == wants_collision)
    root.free()
    _finish(report)

func _check(r: Dictionary, id: String, ok: bool) -> void:
    r.checks.append({"id": id, "ok": ok}); r.pass = r.pass and ok

func _finish(r: Dictionary) -> void:
    print(JSON.stringify(r))                       # single JSON line — adapter parses stdout
    quit(0 if r.pass else 1)
```

(`_collect`, `_has_node_of`, `_load_manifest` are trivial recursions/file reads.) Principles:
**one JSON line on stdout is the report contract**; asserts mirror the manifest, not
hardcoded expectations; `load()` failures print Godot errors to stderr — capture both
streams.

## Skyboxes and 2D backgrounds

- **Skybox (equirect EXR):** ship the `.exr`, let Godot import it (`Image`/`Texture2D`,
  HDR preserved), then generate a `.tres`:

  ```
  [gd_resource type="PanoramaSkyMaterial" load_steps=2 format=3]
  [ext_resource type="Texture2D" path="res://assets/generated/skies/<id>/<id>.exr" id="1"]
  [resource]
  panorama = ExtResource("1")
  ```

  Verification: a headless script builds `Sky` + `WorldEnvironment`, assigns the material,
  asserts no load errors and `panorama != null`. Writing `.tres` text directly is stable
  and diff-friendly; format 3 = Godot 4.
- **Parallax backgrounds:** generate a `.tscn` with one `Parallax2D` per layer
  (`scroll_scale = Vector2(parallax_factor, parallax_factor)`, `repeat_size.x = layer_width`
  when the layer loops) wrapping a `Sprite2D`. Same pattern: text resource generation +
  headless load-and-assert.

## Recording deterministic gameplay video (+ procedural audio)

To show a generated asset *in motion* (a played game, a turntable of a rigged prop) as a
shareable video, drive the scene from a **manual fixed-timestep clock**, not `_process`:

```gdscript
var dt := 1.0 / _fps
while true:
    _step(dt)                       # advance game state by exactly dt (no OS.delta)
    _elapsed += dt                  # accumulate your own time — for camera moves, timers
    _update_camera()
    await RenderingServer.frame_post_draw
    if not _record_dir.is_empty():
        get_viewport().get_texture().get_image().save_png("%s/frame_%04d.png" % [_record_dir, _frame])
    _frame += 1
```

Because every time-dependent value (animation phase, camera azimuth, event timing) reads
from the dt-summed `_elapsed` and never from wall-clock delta, the render is **bit-stable
and FPS-locked** regardless of how slowly software-Vulkan (lavapipe) actually paints each
frame. Stitch with `ffmpeg -framerate <fps> -i frame_%04d.png ...`.

Corollaries that bite when you make the camera cinematic:

- **Everything time-driven must read `_elapsed`, including effects.** A `GPUParticles3D`/
  `CPUParticles3D` system advances on the engine's *real* frame delta — under slow
  software rendering each frame is seconds of wall-clock, so the sim burns through a whole
  puff in one frame and is non-deterministic. Animate "smoke"/flash/shake **by hand** from
  your `dt` (billboarded quads whose position/scale/alpha you step yourself), same as the
  game state.
- **A straight-down camera can't use `look_at`** — when the view direction is parallel to
  the up hint the basis is degenerate (the image twists or NaNs). Build the basis by hand:
  derive `right` from the orbit azimuth (`Vector3(cos(az),0,-sin(az))`, always well-defined),
  then `up = backward.cross(right)`, re-orthogonalize, and set `global_transform`. This also
  makes a top-down shot rotate smoothly as the azimuth spins.
- **To land a move exactly on a game event** (e.g. the camera settling as the game ends),
  sum the timeline's exact duration up front from the same constants `_step` consumes, then
  key the move to `clampf(_elapsed / total, 0, 1)` through a `smoothstep`. Verify the
  *extreme* frames (first, mid-descent, last), not just the middle.
- **Don't eyeball geometry in low-res smoke frames.** A pedestal punching *through* a board
  is a handful of stray bright pixels at 640×360 — trivially mistaken for a game piece — but
  glaring at 1280×720. Two guards: (a) a headless **geometry audit** mode (`-- --audit`) that
  builds the scene and asserts spatial invariants *without rendering* (top of A below top of
  B but reaching B's underside; movable objects on their surface, not at the base; nothing
  below sea level), exiting non-zero on violation; and (b) spot-check a real **high-res**
  still of the risky frame. Make the audit a **verify-the-verifier**: feed the checker known
  broken values and require it to go RED on each, so a silently-broken audit can't pass. The
  bug that motivated this came from *ordering* — a dependent object was placed (with a
  guessed size) before the object it should measure against was loaded; build it after, and
  measure.

**Audio: headless has no audio driver** (`AudioServer` falls back to the dummy driver — no
sound reaches the PNG frames). So you cannot "record" audio inline. Instead make it
deterministic and mux it in post:

1. Synthesize SFX procedurally as `AudioStreamWAV` (PCM in code, no assets) — same streams
   you `play()` in interactive mode, so game and video sound identical.
2. During recording, **log each sound event** as `frame kind idx` and `save_to_wav()` the
   streams once into the record dir.
3. A tiny post script builds a silent master of length `nframes/fps`, mixes each clip in at
   `round(frame/fps * rate)`, writes `soundtrack.wav`, and `ffmpeg -i video -i soundtrack
   -c:v copy -c:a aac -shortest out.mp4`.

The event log is the single source of truth for both the on-screen action and the
soundtrack, so they can never drift. (Worked example: `examples/othello/game/audio.gd` +
`mux_audio.py`.)

## Engine-neutrality guardrails (why the adapter stays thin)

- **Godot does not support `KHR_draco_mesh_compression`** — a Draco-compressed glb imports
  as an empty scene or errors. Keep the canonical glb uncompressed; compress only in
  adapters for engines that decode it. Same caution for `KHR_texture_basisu`/KTX2.
- Godot 4 generates **automatic mesh LODs** on import — pipeline-side LOD siblings are
  redundant for Godot (strip them, above) but other engines need them; that asymmetry
  belongs in the adapter, never in generation.
- glTF emissive strength (`KHR_materials_emissive_strength`) is honored natively in 4.3+.
- The adapter must be **idempotent**: re-delivering overwrites files, re-runs `--import`,
  re-verifies. Never edit imported artifacts under `.godot/` — they are cache, not output.

## Pitfall checklist

- Running verification **before** `--import` → `load()` returns null even though the file
  is present. Import first, verify second, always.
- `OS.get_cmdline_user_args()` only sees args after a **bare `--`**; args before it belong
  to Godot and mixing them breaks silently.
- Headless `print()` interleaves with engine log lines on stdout — make your report the
  only line starting with `{`, or prefix it (`REPORT:`) and grep.
- A `SceneTree` script's `_init` runs before the scene tree is usable for rendering —
  fine for load/instantiate checks; anything needing a frame (viewport capture) must use
  `await process_frame` inside a deferred call, or better, keep render-based QA in the
  Blender harness where it's deterministic.
- CI containers need a writable `~/.local/share/godot` (shader/class caches); read-only
  home dirs cause misleading import errors.
- Version pin matters: suffix behavior, `Parallax2D` (4.3+), and importer defaults keys
  all shifted during Godot 4.x — record the exact Godot version in every verification
  report, and gate the adapter on it at startup.

## Relationship to the pipeline spec

Implements `docs/specs/asset-pipeline.md` §19 (Godot adapter: delivery layout, import
trigger, post-import script, `verify_import.gd`, skybox/background mapping) within the §18
`EngineAdapter` contract (deliver/verify, idempotent, never mutates canonical artifacts).
