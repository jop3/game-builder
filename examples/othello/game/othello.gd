# othello.gd — the playable board. Loads the pipeline-generated board + disc
# .glb at runtime, plays a full bot-vs-bot game, and animates the signature
# Othello flip cascade. Driven by a MANUAL fixed-timestep clock so a headless
# recording is deterministic and FPS-locked regardless of render speed
# (same pattern as Snittet's record_demo.gd).
#
# Run (interactive):  godot --path . res://othello.tscn
# Record a video:     godot --path . res://othello.tscn -- --record=DIR --fps=30
extends Node3D

const Rules := preload("res://rules.gd")
const Bot := preload("res://bot.gd")

# Asset .glb (delivered next to the project, or overridden on the cmdline).
# One two-tone disc (black on one face, white on the other), like a real set:
# a flip is a genuine 180° turn-over, and the rim reads half black / half white.
var _board_glb := "res://assets/board.glb"
var _disc_glb := "res://assets/disc.glb"

# --- timeline knobs (seconds) — the feel rubric lives here ---
const THINK_T := 0.05      # brief beat before a move
const PLACE_T := 0.16      # new disc drops + settles
const FLIP_DUR := 0.24     # one disc's 180° turn
const FLIP_STAGGER := 0.06 # delay between successive discs in the captured run
const PAUSE_T := 0.10      # beat between moves
const END_HOLD := 2.6      # linger on the final board

var _fps := 30.0
var _record_dir := ""
var _frame := 0

var _board_root: Node3D
var _disc_proto: Node3D    # the single hidden two-tone disc template
var _disc_rot := {}        # cell(int) -> current settled rotation.x (which face is up)
var _cx := 0.0
var _cy := 0.0
var _cell := 0.05
var _surf_z := 0.03
var _border := 0.0265
var _disc_h := 0.007
var _bw := 0.46          # board footprint (measured)

var _discs := {}           # cell(int) -> Node3D holder (child = colored disc)
var _disc_side := {}       # cell(int) -> currently displayed side
var _moves := []           # precomputed [{side, cell, flips:[{cell, from}]}]

# animation cursor
var _mi := 0               # move index
var _phase := "think"      # think | place | flip | pause | done
var _pt := 0.0             # time in phase
var _done_t := 0.0

func _ready() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--record="): _record_dir = a.substr(9)
		elif a.begins_with("--fps="): _fps = float(a.substr(6))
		elif a.begins_with("--board="): _board_glb = a.substr(8)
		elif a.begins_with("--disc="): _disc_glb = a.substr(7)

	_build_stage()
	_load_assets()
	_build_trays()
	_precompute_game()
	_place_start()
	# drive everything from a manual clock so recordings are deterministic
	_run()

# ---------------------------------------------------------------- assets ---
func _load_glb(path: String) -> Node3D:
	var doc := GLTFDocument.new()
	var st := GLTFState.new()
	if doc.append_from_file(ProjectSettings.globalize_path(path), st) != OK:
		push_error("failed to load %s" % path)
		return null
	return doc.generate_scene(st)

func _aabb(o: Node3D) -> AABB:
	var acc := AABB()
	var have := false
	for mi in _mesh_instances(o):
		var ab: AABB = mi.get_aabb()
		var t: Transform3D = _rel_xform(o, mi)
		ab = t * ab
		if not have: acc = ab; have = true
		else: acc = acc.merge(ab)
	return acc

func _mesh_instances(n: Node) -> Array:
	var out := []
	if n is MeshInstance3D: out.append(n)
	for c in n.get_children(): out.append_array(_mesh_instances(c))
	return out

func _rel_xform(root: Node3D, child: Node3D) -> Transform3D:
	var t := Transform3D()
	var chain := []
	var n: Node = child
	while n != null and n != root:
		if n is Node3D: chain.push_front(n)
		n = n.get_parent()
	for c in chain: t = t * (c as Node3D).transform
	return t

func _load_assets() -> void:
	_board_root = _load_glb(_board_glb)
	add_child(_board_root)
	var ab := _aabb(_board_root)
	var bw: float = min(ab.size.x, ab.size.z)          # board footprint (X/Z plane, Y up in Godot)
	_bw = bw
	_cx = ab.position.x + ab.size.x * 0.5
	_cy = ab.position.z + ab.size.z * 0.5
	var play := bw - 2.0 * _border
	_cell = play / 8.0
	_surf_z = ab.position.y + ab.size.y - 0.004        # wood surface, just under the grid tops
	_disc_proto = _load_glb(_disc_glb)
	_disc_proto.visible = false
	add_child(_disc_proto)
	_disc_h = _aabb(_disc_proto).size.y            # for a center-pivot flip

func _build_trays() -> void:
	# Two side trays holding the reserve discs as a HORIZONTAL ROLL lying in the
	# tray channel (discs on edge, axis along Z), not a standing column -- their
	# rims read as a striped black/white roll, like the real set.
	var half := _bw / 2.0
	var rad := _aabb(_disc_proto).size.x / 2.0     # disc radius
	var n := 16
	var roll := n * _disc_h
	for sign in [-1.0, 1.0]:
		var tx: float = _cx + sign * (half + 0.052)
		var base := MeshInstance3D.new()
		var bm := BoxMesh.new()
		bm.size = Vector3(0.058, 0.012, roll + 0.03)
		base.mesh = bm
		var mat := StandardMaterial3D.new()
		mat.albedo_color = Color(0.02, 0.02, 0.02)
		mat.roughness = 0.3
		base.material_override = mat
		base.position = Vector3(tx, 0.006, _cy)
		add_child(base)
		for i in n:
			var holder := Node3D.new()
			add_child(holder)
			holder.position = Vector3(tx, 0.012 + rad, _cy - roll / 2.0 + (i + 0.5) * _disc_h)
			holder.rotation.x = PI / 2.0        # lay the disc on its edge (roll axis along Z)
			var vis: Node3D = _disc_proto.duplicate()
			vis.visible = true
			vis.position.y = -_disc_h / 2.0     # centre the disc on the holder
			holder.add_child(vis)

func _cell_pos(cell: int) -> Vector3:
	var r: int = cell / 8
	var c: int = cell % 8
	var play := _cell * 8.0
	var x := _cx - play / 2.0 + (c + 0.5) * _cell
	var z := _cy - play / 2.0 + (r + 0.5) * _cell
	return Vector3(x, _surf_z, z)

# rotation.x that puts a given face up. The generator gives the disc's TOP half
# (z>0) material slot 0 = black, bottom = white, so black is up at rot 0.
func _rot_for(side: int) -> float:
	return 0.0 if side == Rules.DARK else PI

func _disc_visual() -> Node3D:
	# the two-tone disc, offset so its CENTER sits at the holder origin (so the
	# holder pivots the flip about the disc's middle, not its base)
	var vis: Node3D = _disc_proto.duplicate()
	vis.visible = true
	vis.position.y = -_disc_h / 2.0
	return vis

func _new_disc(side: int, cell: int) -> Node3D:
	var holder := Node3D.new()
	add_child(holder)
	var p := _cell_pos(cell)
	holder.position = Vector3(p.x, p.y + _disc_h / 2.0, p.z)   # holder at disc center
	var rot := _rot_for(side)
	holder.rotation.x = rot
	holder.add_child(_disc_visual())
	_discs[cell] = holder
	_disc_side[cell] = side
	_disc_rot[cell] = rot
	return holder

# --------------------------------------------------------------- game -----
func _precompute_game() -> void:
	var b := Rules.start_board()
	var side := Rules.DARK
	var guard := 0
	while not Rules.is_terminal(b) and guard < 200:
		guard += 1
		var cell := Bot.choose(b, side)
		if cell == -1:
			side = Rules.opp(side)
			continue
		var flips := Rules.flips_for(b, side, cell / 8, cell % 8)
		var rec := []
		for fc in flips:
			rec.append({"cell": fc, "from": b[fc]})
		Rules.apply_move(b, side, cell)
		_moves.append({"side": side, "cell": cell, "flips": rec})
		side = Rules.opp(side)

func _place_start() -> void:
	var b := Rules.start_board()
	for i in 64:
		if b[i] != Rules.EMPTY:
			_new_disc(b[i], i)

# --------------------------------------------------------- animation ------
func _step(dt: float) -> void:
	if _phase == "done":
		_done_t += dt
		return
	if _mi >= _moves.size():
		_phase = "done"
		return
	_pt += dt
	var mv: Dictionary = _moves[_mi]
	match _phase:
		"think":
			if _pt >= THINK_T:
				# drop the new disc in
				var h := _new_disc(mv.side, mv.cell)
				h.scale = Vector3(0.2, 0.2, 0.2)
				_phase = "place"; _pt = 0.0
		"place":
			var k: float = clampf(_pt / PLACE_T, 0.0, 1.0)
			var e: float = 1.0 - pow(1.0 - k, 3.0)          # ease-out
			var s: float = 0.2 + 0.8 * e
			_discs[mv.cell].scale = Vector3(s, s, s)
			_discs[mv.cell].position.y = _surf_z + _disc_h / 2.0 + (1.0 - e) * 0.03   # settle down
			if k >= 1.0:
				_phase = "flip" if not mv.flips.is_empty() else "pause"
				_pt = 0.0
		"flip":
			var flips: Array = mv.flips
			var all_done := true
			for i in flips.size():
				var f: Dictionary = flips[i]
				var start: float = i * FLIP_STAGGER
				var local: float = _pt - start
				if local < 0.0:
					all_done = false
					continue
				var k2: float = clampf(local / FLIP_DUR, 0.0, 1.0)
				if k2 < 1.0: all_done = false
				# turn the physical two-tone disc over by PI from where it sat:
				# the other face (the mover's colour) comes up on its own.
				_discs[f.cell].rotation.x = _disc_rot[f.cell] + k2 * PI
			if all_done:
				for f in flips:
					_disc_rot[f.cell] += PI
					_discs[f.cell].rotation.x = _disc_rot[f.cell]
					_disc_side[f.cell] = mv.side
				_phase = "pause"; _pt = 0.0
		"pause":
			if _pt >= PAUSE_T:
				_mi += 1
				_phase = "think"; _pt = 0.0

# ------------------------------------------------------------ run loop ----
func _run() -> void:
	var dt := 1.0 / _fps
	if not _record_dir.is_empty():
		DirAccess.make_dir_recursive_absolute(_record_dir)
	while true:
		_step(dt)
		await RenderingServer.frame_post_draw
		if not _record_dir.is_empty():
			var img := get_viewport().get_texture().get_image()
			img.save_png("%s/frame_%04d.png" % [_record_dir, _frame])
		_frame += 1
		if _phase == "done" and _done_t >= END_HOLD:
			break
	var sc := Rules.score(_final_board())
	print("GAME_OVER dark=%d light=%d winner=%s frames=%d" % [
		sc.dark, sc.light, ("dark" if sc.dark > sc.light else "light"), _frame])
	get_tree().quit()

func _final_board() -> PackedInt32Array:
	var b := PackedInt32Array(); b.resize(64); b.fill(0)
	for cell in _disc_side: b[cell] = _disc_side[cell]
	return b

# ------------------------------------------------------------- staging ----
func _build_stage() -> void:
	var env := WorldEnvironment.new()
	var e := Environment.new()
	# Soft studio look with RICH contrast: a neutral medium-grey backdrop (not a
	# washed white), low ambient so blacks stay black, and a soft directional key
	# (soft shadows) instead of a hard point light so the highlights aren't harsh.
	e.background_mode = Environment.BG_COLOR
	e.background_color = Color(0.34, 0.35, 0.37)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	e.ambient_light_color = Color(0.50, 0.54, 0.62)
	e.ambient_light_energy = 0.10          # low → deep blacks, saturated felt
	e.tonemap_mode = Environment.TONE_MAPPER_AGX
	e.tonemap_white = 1.6
	e.glow_enabled = true
	e.glow_intensity = 0.06
	e.glow_bloom = 0.02
	env.environment = e
	add_child(env)

	# Soft directional key (the window/softbox): even, gentle shadows, so nothing
	# is harshly blown; energy modest so the near-black frame/pieces read black.
	var key := DirectionalLight3D.new()
	key.light_color = Color(1.0, 0.98, 0.95)
	key.light_energy = 1.6
	key.light_angular_distance = 2.5       # soft shadow edges
	key.shadow_enabled = true
	key.rotation = Vector3(deg_to_rad(-58.0), deg_to_rad(-32.0), 0.0)
	add_child(key)
	# a small omni high up for a single controlled specular glint on the gloss
	var spec := OmniLight3D.new()
	spec.light_color = Color(0.95, 0.97, 1.0)
	spec.light_energy = 1.2
	spec.omni_range = 2.4
	spec.position = Vector3(0.28, 0.6, 0.35)
	add_child(spec)
	# gentle cool fill from the camera side so shadows aren't crushed to pure black
	var fill := OmniLight3D.new()
	fill.light_color = Color(0.80, 0.85, 0.95)
	fill.light_energy = 0.35
	fill.omni_range = 4.0
	fill.position = Vector3(0.05, 0.4, 0.7)
	add_child(fill)

	var table := MeshInstance3D.new()
	var pm := PlaneMesh.new(); pm.size = Vector2(4, 4)
	table.mesh = pm
	var tm := StandardMaterial3D.new()
	tm.albedo_color = Color(0.30, 0.31, 0.34); tm.roughness = 0.6   # neutral tabletop
	table.material_override = tm
	table.position = Vector3(0, -0.001, 0)
	add_child(table)

	var cam := Camera3D.new()
	cam.fov = 60
	# pulled back + up a touch so the board AND the two side trays fit frame.
	# look_at() needs the node in-tree; look_at_from_position() does not.
	cam.look_at_from_position(Vector3(0.0, 0.54, 0.52), Vector3(0.0, 0.015, 0.0), Vector3.UP)
	add_child(cam)
