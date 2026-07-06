extends SceneTree
# Headless verification entrypoint (spec docs/specs/asset-pipeline.md §19.4,
# steps 2 and 3). Installed by GodotAdapter into
# res://assets/generated/_pipeline/verify_import.gd and invoked as:
#
#   godot --headless --path <project> \
#         --script res://assets/generated/_pipeline/verify_import.gd \
#         -- <res_path>
#
# Design choice: the spec describes step 2 (mesh PackedScene checks) and
# step 3 (skybox PanoramaSkyMaterial checks) as two scripts' worth of logic,
# but the task deliverable ships a single verify_import.gd — so this script
# branches on the delivered resource's extension: a ".tres" is the adapter's
# generated PanoramaSkyMaterial resource (skybox branch), anything else is a
# mesh .glb (PackedScene branch). Both branches print exactly one JSON report
# line on stdout (the only contract the adapter parses) and exit 0/1.


func _init() -> void:
	var args := OS.get_cmdline_user_args()  # everything after the bare `--`
	var res_path: String = args[0] if args.size() > 0 else ""
	var report := {"asset": res_path, "checks": [], "pass": true}
	if res_path.ends_with(".tres"):
		_verify_skybox(report, res_path)
	else:
		_verify_mesh(report, res_path)
	_finish(report)


# ---------- mesh branch (spec 19.4 step 2) ----------

func _verify_mesh(report: Dictionary, res_path: String) -> void:
	var manifest := _load_manifest_for(res_path)
	var ps: PackedScene = load(res_path)
	_check(report, "loads_as_packed_scene", ps != null)
	if ps == null:
		return
	var root := ps.instantiate()
	var meshes: Array = []
	_collect_mesh_instances(root, meshes)
	_check(report, "has_mesh_instance", meshes.size() >= 1)

	var textures: Dictionary = manifest.get("stats", {}).get("textures", {})
	for mi in meshes:
		var mesh: Mesh = mi.mesh
		if mesh == null:
			continue
		for s in range(mesh.get_surface_count()):
			var mat := mesh.surface_get_material(s)
			var is_standard: bool = mat is BaseMaterial3D
			_check(report, "material_is_standard", is_standard)
			if is_standard and textures.has("albedo"):
				var tex: Texture2D = mat.albedo_texture
				_check(report, "albedo_texture_present", tex != null)
				if tex != null:
					var budget := int(textures["albedo"])
					_check(report, "albedo_within_budget",
						int(tex.get_size().x) <= budget)

	var collision_tag: String = manifest.get("collision", "convex")
	var wants_collision: bool = collision_tag != "none"
	_check(report, "collision_matches_request",
		_has_node_of_type(root, "CollisionObject3D") == wants_collision)
	root.free()


# ---------- skybox branch (spec 19.4 step 3) ----------

func _verify_skybox(report: Dictionary, res_path: String) -> void:
	var mat: Resource = load(res_path)
	var is_panorama: bool = mat != null and mat is PanoramaSkyMaterial
	_check(report, "panorama_material_loads", is_panorama)
	if not is_panorama:
		return
	_check(report, "panorama_texture_present", mat.panorama != null)

	var sky := Sky.new()
	sky.sky_material = mat
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = sky
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	_check(report, "world_environment_assigns",
		world_env.environment != null and world_env.environment.sky.sky_material == mat)
	world_env.free()


# ---------- shared helpers ----------

func _collect_mesh_instances(node: Node, out: Array) -> void:
	if node is MeshInstance3D:
		out.append(node)
	for child in node.get_children():
		_collect_mesh_instances(child, out)


func _has_node_of_type(node: Node, type_name: String) -> bool:
	if node.is_class(type_name):
		return true
	for child in node.get_children():
		if _has_node_of_type(child, type_name):
			return true
	return false


func _load_manifest_for(res_path: String) -> Dictionary:
	var manifest_path := res_path.get_basename() + ".manifest.json"
	if not FileAccess.file_exists(manifest_path):
		return {}
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
	return parsed if parsed is Dictionary else {}


func _check(report: Dictionary, id: String, ok: bool) -> void:
	report.checks.append({"id": id, "ok": ok})
	report.pass = report.pass and ok


func _finish(report: Dictionary) -> void:
	print(JSON.stringify(report))  # single JSON line — the adapter parses stdout
	quit(0 if report.pass else 1)
