@tool
extends EditorScenePostImport
# Bundled post-import hook (spec docs/specs/asset-pipeline.md §19.3). Installed by
# GodotAdapter (assetpipe/adapters/godot/adapter.py) into
# res://assets/generated/_pipeline/post_import.gd and wired via project.godot's
# [importer_defaults] -> scene -> "nodes/import_script/path" so it runs for every
# imported glb in the project.
#
# Responsibilities (all four required by §19.3):
#   1. Strip `_LOD*` sibling meshes unless pipeline LODs are kept.
#   2. Set GeometryInstance3D.gi_mode = STATIC for kit/environment categories.
#   3. Apply a physics collision_layer from a `layer:<n>` request tag.
#   4. Rename the root node to the PascalCased asset_id.
#
# Design choices (documented per task instructions):
#   - `use_pipeline_lods` is read from ProjectSettings
#     ("assetpipe/use_pipeline_lods"), NOT the per-asset manifest: it is a
#     pipeline-wide policy flag set once by the adapter for the whole project
#     (spec §19.1's `use_pipeline_lods: true` config knob lives in
#     pipeline.yaml -> delivery.godot, not per asset), so a single project
#     setting is the natural place for it. Per-asset overrides remain possible
#     later via a manifest key without touching this default path.
#   - Every other per-asset decision (category, tags, asset_id) comes from the
#     sibling "<name>.manifest.json" the adapter always delivers next to the
#     .glb (spec §19.1), read here via get_source_file().

const KIT_ENV_CATEGORIES := ["modular_kit_piece", "environment_piece"]
const LOD_MARKER := "_LOD"
const LAYER_TAG_PREFIX := "layer:"
const MAX_PHYSICS_LAYER := 20


func _post_import(scene: Node) -> Object:
	var manifest := _load_sibling_manifest()
	var use_pipeline_lods: bool = ProjectSettings.get_setting(
		"assetpipe/use_pipeline_lods", false)
	_walk(scene, manifest, use_pipeline_lods)
	var asset_id: String = manifest.get(
		"asset_id", get_source_file().get_file().get_basename())
	scene.name = asset_id.to_pascal_case()
	return scene


func _load_sibling_manifest() -> Dictionary:
	var manifest_path := get_source_file().get_basename() + ".manifest.json"
	if not FileAccess.file_exists(manifest_path):
		return {}
	var text := FileAccess.get_file_as_string(manifest_path)
	var parsed = JSON.parse_string(text)
	return parsed if parsed is Dictionary else {}


func _walk(node: Node, manifest: Dictionary, use_pipeline_lods: bool) -> void:
	if node is MeshInstance3D:
		if not use_pipeline_lods and str(node.name).contains(LOD_MARKER):
			# Godot 4 generates its own mesh LODs on import; pipeline-side LOD
			# siblings are redundant here (they matter for other engines).
			var parent := node.get_parent()
			if parent:
				parent.remove_child(node)
			node.queue_free()  # queue_free, not free(): plain free() during
			                   # import can crash the importer (skill pitfall).
			return
		if manifest.get("category", "") in KIT_ENV_CATEGORIES:
			node.gi_mode = GeometryInstance3D.GI_MODE_STATIC
	_apply_physics_layer(node, manifest)
	for child in node.get_children():
		_walk(child, manifest, use_pipeline_lods)


func _apply_physics_layer(node: Node, manifest: Dictionary) -> void:
	# -col/-convcol glTF name suffixes already produced the CollisionObject3D
	# (StaticBody3D/RigidBody3D) child at import time; this only assigns which
	# physics layer bit it occupies, from the request's `layer:<n>` tag.
	if not (node is CollisionObject3D):
		return
	for tag in manifest.get("tags", []):
		var tag_str := str(tag)
		if tag_str.begins_with(LAYER_TAG_PREFIX):
			var layer_num := int(tag_str.substr(LAYER_TAG_PREFIX.length()))
			if layer_num >= 1 and layer_num <= MAX_PHYSICS_LAYER:
				node.collision_layer = 1 << (layer_num - 1)
