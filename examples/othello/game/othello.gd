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
const Audio := preload("res://audio.gd")

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
var _board_bottom_y := 0.5   # brädets underkant i världs-Y (piedestalens topp)
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
var _flip_fired := {}      # i -> true, vilka vändningar i draget som redan ljudit
var _win_fired := false

# --- kamera-spiral ---
# Kameran startar rakt ovanför och spiralar långsamt nedåt runt bordet, och
# landar i en tydlig sidovy PRECIS när partiet är slut. Allt drivs av _elapsed
# (dt-summerad) mot partiets kända längd _play_dur → deterministiskt.
var _cam: Camera3D
var _elapsed := 0.0        # total speltid (deterministisk, dt-summerad)
var _play_dur := 1.0       # partiets speltid i sekunder (utom END_HOLD)
const LIFT := 0.5          # brädet lyft upp på pelarpiedestalen
const SHOW_TRAYS := false  # ö-arenan: brädet ensamt på piedestalen (som referensen)
const CAM_CENTER := Vector3(0.0, 0.34, 0.0)   # mellan brädet (~0.5) och vattnet
const CAM_EL_TOP := 86.0   # ° elevation vid start (nästan rakt ovanför)
const CAM_EL_END := 25.0   # ° elevation vid landning (låg → havet + horisont bakom)
const CAM_D_TOP := 1.15    # kameraavstånd uppe
const CAM_D_END := 1.06    # kameraavstånd nere (brädet + piedestalen större i bild)
const CAM_SPINS := 1.5     # antal varv runt bordet under nedstigningen

# --- drama vid stora vändningskaskader ---
const DRAMA_MIN := 4       # minsta antal vändningar som utlöser effekt
var _flash: OmniLight3D     # kort ljusblixt vid draget
var _flash_t := 999.0
var _flash_dur := 0.30
var _flash_peak := 0.0
var _sflash: ColorRect      # subtil helskärmsblixt
var _sflash_t := 999.0
var _sflash_dur := 0.28
var _sflash_amp := 0.0
var _shake_t := 999.0       # liten kameraskak
var _shake_dur := 0.35
var _shake_amp := 0.0
var _puffs := []            # aktiva rökpuffar (manuellt animerade)
var _puff_tex: Texture2D

# --- arena (hav, himmel, blixt) ---
var _sea_mat: ShaderMaterial
var _sky_mat: ShaderMaterial
var _bolt: DirectionalLight3D    # blixtljus (spikar vid nedslag)
var _bolt_t := 999.0
var _bolt_dur := 0.45
var _bolt_peak := 0.0
var _bolt_times := []            # schemalagda blixttider (sekunder)
var _bolt_i := 0
var _col_glb := "res://assets/column.glb"

# --- ljud ---
var _sfx_place: AudioStreamWAV
var _sfx_flip := []         # några tonhöjdsvarianter
var _sfx_win: AudioStreamWAV
var _sfx_thunder: AudioStreamWAV
var _p_place: AudioStreamPlayer
var _p_flip: AudioStreamPlayer
var _p_win: AudioStreamPlayer
var _p_thunder: AudioStreamPlayer
var _events := []           # [{f, kind, idx}] för muxern vid inspelning

# --- audit ---
var _audit_mode := false
var _pedestal_top_y := 0.0  # pelarens topp i världs-Y (för geometri-audit)

func _ready() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--record="): _record_dir = a.substr(9)
		elif a.begins_with("--fps="): _fps = float(a.substr(6))
		elif a.begins_with("--board="): _board_glb = a.substr(8)
		elif a.begins_with("--disc="): _disc_glb = a.substr(7)
		elif a == "--audit": _audit_mode = true   # headless geometri-kontroll, ingen rendering

	_build_stage()
	_build_fx()
	_build_audio()
	_load_assets()
	_build_pedestal()      # EFTER brädet laddats: pelaren skalas mot brädets underkant
	_build_trays()
	_precompute_game()
	_play_dur = _compute_play_dur()
	_schedule_bolts()
	_place_start()
	if _audit_mode:
		_audit_selftest()   # verifiera att kontrollen faktiskt slår rött på trasigt
		_audit()            # verifiera den riktiga scenen
		get_tree().quit()
		return
	# drive everything from a manual clock so recordings are deterministic
	_run()

# Headless geometri-audit: verifierar scenens rumsliga invarianter DETERMINISTISKT
# (ingen rendering, ingen gissning). Fångar precis den klass av bugg som en
# lågupplöst ögonkontroll missade: pelaren som tränger upp genom brädet, brickor
# på fel höjd, bräde under havsnivå. Skriver AUDIT_PASS eller AUDIT_FAIL-rader.
# REN kontrollfunktion (inga noder) → återanvänds av både den skarpa auditen och
# self-testet nedan, så kontrollen kan verifieras mot medvetet trasiga värden.
func _audit_violations(ped_top: float, board_bottom: float, board_top: float,
		surf: float, max_disc_off: float, sea: float) -> PackedStringArray:
	var f := PackedStringArray()
	if ped_top > board_top - 0.005:
		f.append("POKE_THROUGH pedestal_top=%.3f > board_top=%.3f" % [ped_top, board_top])
	if ped_top < board_bottom - 0.03:
		f.append("FLOATING_BOARD pedestal_top=%.3f << board_bottom=%.3f" % [ped_top, board_bottom])
	if max_disc_off > 0.05:
		f.append("DISC_OFF_SURFACE max_offset=%.3f" % max_disc_off)
	if board_bottom <= sea:
		f.append("BOARD_BELOW_SEA board_bottom=%.3f <= sea=%.3f" % [board_bottom, sea])
	return f

func _audit() -> void:
	var board_top := _surf_z + 0.004
	var want_disc_y := _surf_z + _disc_h / 2.0
	var max_off := 0.0
	for cell in _discs:
		max_off = maxf(max_off, absf(_discs[cell].position.y - want_disc_y))
	var fails := _audit_violations(_pedestal_top_y, _board_bottom_y, board_top, _surf_z, max_off, SEA_Y)
	if fails.is_empty():
		print("AUDIT_PASS pedestal_top=%.3f board_bottom=%.3f board_top=%.3f surf=%.3f" % [
			_pedestal_top_y, _board_bottom_y, board_top, _surf_z])
	else:
		for f in fails:
			print("AUDIT_FAIL: ", f)

# verifiera-verifieraren: mata kontrollen med kända bra OCH kända trasiga värden
# och kräv att den är tyst på det bra och slår RÖTT på varje trasigt fall. Utan
# detta kan en tyst-bruten audit (som alltid returnerar "ok") ge falsk trygghet.
func _audit_selftest() -> void:
	var ok := true
	# rent bra fall → inga violations
	if not _audit_violations(0.51, 0.50, 0.546, 0.542, 0.0, -0.16).is_empty():
		ok = false; print("SELFTEST_FAIL: good scene flagged")
	# varje trasigt fall MÅSTE flaggas:
	var cases := {
		"poke": [0.55, 0.50, 0.546, 0.542, 0.0, -0.16],       # pelare genom bräde
		"floating": [0.30, 0.50, 0.546, 0.542, 0.0, -0.16],   # bräde svävar
		"disc_off": [0.51, 0.50, 0.546, 0.542, 0.40, -0.16],  # brickor vid foten
		"sunk": [0.51, -0.20, 0.546, 0.542, 0.0, -0.16],      # bräde under havet
	}
	for name in cases:
		var c: Array = cases[name]
		if _audit_violations(c[0], c[1], c[2], c[3], c[4], c[5]).is_empty():
			ok = false; print("SELFTEST_FAIL: broken case '%s' NOT flagged" % name)
	print("SELFTEST_PASS" if ok else "SELFTEST_FAILED")

# summera partiets exakta speltid (samma tidsbudget som _step förbrukar) så
# kameraspiralen kan landa precis när sista draget är klart
func _compute_play_dur() -> float:
	var d := 0.0
	for mv in _moves:
		var k: int = mv.flips.size()
		var flip_span := 0.0
		if k > 0:
			flip_span = float(k - 1) * FLIP_STAGGER + FLIP_DUR
		d += THINK_T + PLACE_T + flip_span + PAUSE_T
	return maxf(d, 1.0)

# ----------------------------------------------------------------- ljud ----
func _build_audio() -> void:
	_sfx_place = Audio.make_place()
	for k in 4:
		_sfx_flip.append(Audio.make_flip(k))
	_sfx_win = Audio.make_win()
	_sfx_thunder = Audio.make_thunder()
	_p_place = AudioStreamPlayer.new(); add_child(_p_place)
	_p_flip = AudioStreamPlayer.new(); _p_flip.max_polyphony = 8; add_child(_p_flip)
	_p_win = AudioStreamPlayer.new(); add_child(_p_win)
	_p_thunder = AudioStreamPlayer.new(); _p_thunder.max_polyphony = 4; add_child(_p_thunder)
	# Vid inspelning: spara effekterna som .wav så muxern kan lägga dem på spåret
	# (headless-drivern spelar inget). Interaktivt: spela direkt i _sfx().
	if not _record_dir.is_empty():
		_sfx_place.save_to_wav("%s/sfx_place.wav" % _record_dir)
		_sfx_win.save_to_wav("%s/sfx_win.wav" % _record_dir)
		_sfx_thunder.save_to_wav("%s/sfx_thunder.wav" % _record_dir)
		for k in _sfx_flip.size():
			_sfx_flip[k].save_to_wav("%s/sfx_flip_%d.wav" % [_record_dir, k])

# emittera en ljudhändelse: logga (för muxern) + spela direkt om vi inte spelar in.
# frame_offset låter åskan följa blixten med en liten fördröjning i inspelningen.
func _sfx(kind: String, idx: int = 0, frame_offset: int = 0) -> void:
	_events.append({"f": _frame + frame_offset, "kind": kind, "idx": idx})
	if not _record_dir.is_empty():
		return
	match kind:
		"place":
			_p_place.stream = _sfx_place; _p_place.play()
		"flip":
			_p_flip.stream = _sfx_flip[idx % _sfx_flip.size()]; _p_flip.play()
		"win":
			_p_win.stream = _sfx_win; _p_win.play()
		"thunder":
			_p_thunder.stream = _sfx_thunder; _p_thunder.play()

# ---------------------------------------------------------------- drama ----
func _build_fx() -> void:
	# kort ljusblixt (positioneras vid draget när den utlöses)
	_flash = OmniLight3D.new()
	_flash.light_color = Color(0.82, 0.9, 1.0)
	_flash.omni_range = 1.3
	_flash.light_energy = 0.0
	_flash.shadow_enabled = false
	add_child(_flash)
	# subtil helskärmsblixt (2D-overlay ovanpå vyn, kommer med i inspelningen)
	var layer := CanvasLayer.new(); add_child(layer)
	_sflash = ColorRect.new()
	_sflash.color = Color(0.9, 0.95, 1.0, 0.0)
	_sflash.set_anchors_preset(Control.PRESET_FULL_RECT)
	_sflash.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(_sflash)
	_puff_tex = _make_puff_tex()

# mjuk rund prick (radiell alfa) att billboarda som rökpuff
func _make_puff_tex() -> Texture2D:
	var sz := 64
	var img := Image.create(sz, sz, false, Image.FORMAT_RGBA8)
	for y in sz:
		for x in sz:
			var dx := (x + 0.5) / sz - 0.5
			var dy := (y + 0.5) / sz - 0.5
			var d: float = sqrt(dx * dx + dy * dy) * 2.0
			var a: float = clampf(1.0 - d, 0.0, 1.0)
			img.set_pixel(x, y, Color(1, 1, 1, a * a))
	return ImageTexture.create_from_image(img)

# utlös dramaeffekt skalad efter hur många brickor som vänds
func _trigger_drama(cell: int, count: int) -> void:
	if count < DRAMA_MIN:
		return
	var f: float = clampf(float(count - DRAMA_MIN) / 4.0, 0.0, 1.0)
	var p := _cell_pos(cell)
	_flash.position = p + Vector3(0.0, 0.08, 0.0)
	_flash_t = 0.0
	_flash_peak = 3.0 + 6.0 * f
	_sflash_t = 0.0
	_sflash_amp = 0.10 + 0.22 * f
	_shake_t = 0.0
	_shake_amp = 0.008 + 0.016 * f
	var n := 5 + int(round(5.0 * f))
	for i in n:
		_spawn_puff(p, cell * 17 + i)

func _spawn_puff(p: Vector3, seedv: int) -> void:
	var mi := MeshInstance3D.new()
	var q := QuadMesh.new(); q.size = Vector2(0.05, 0.05)
	mi.mesh = q
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	m.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	m.albedo_texture = _puff_tex
	m.albedo_color = Color(0.9, 0.92, 0.96, 0.65)
	mi.material_override = m
	mi.position = p + Vector3(0.0, 0.02, 0.0)
	add_child(mi)
	var ang: float = _nrand(seedv * 3 + 1) * PI
	var spd: float = 0.07 + 0.06 * absf(_nrand(seedv * 3 + 5))
	var vel := Vector3(cos(ang) * spd, 0.11 + 0.05 * absf(_nrand(seedv * 3 + 9)), sin(ang) * spd)
	_puffs.append({"n": mi, "m": m, "t": 0.0, "dur": 0.55 + 0.3 * absf(_nrand(seedv)),
		"vel": vel, "col": Color(0.9, 0.92, 0.96), "a0": 0.65, "grow": 2.2})

# ett vitt skumstänk vid klippkanten som slungas upp/utåt (fejkad krossande våg)
func _spawn_spray(seedv: int) -> void:
	# lågt vitt skum vid klippans vattenlinje — små fläckar som poppar och tonas
	# ut nära vattnet (ALDRIG upp längs pelaren), så piedestalen aldrig döljs
	var a: float = _nrand(seedv * 7 + 3) * PI
	var rr: float = ISLAND_R * (0.94 + 0.12 * absf(_nrand(seedv * 7 + 11)))
	var mi := MeshInstance3D.new()
	var q := QuadMesh.new(); q.size = Vector2(0.045, 0.045)
	mi.mesh = q
	var m := StandardMaterial3D.new()
	m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	m.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	m.albedo_texture = _puff_tex
	mi.material_override = m
	mi.position = Vector3(rr * sin(a), SEA_Y + 0.01, rr * cos(a))
	add_child(mi)
	var up: float = 0.05 + 0.05 * absf(_nrand(seedv * 7 + 17))    # bara ett litet lyft
	var out: float = 0.05 + 0.05 * absf(_nrand(seedv * 7 + 23))
	var vel := Vector3(sin(a) * out, up, cos(a) * out)
	_puffs.append({"n": mi, "m": m, "t": 0.0, "dur": 0.30 + 0.14 * absf(_nrand(seedv * 7 + 5)),
		"vel": vel, "col": Color(0.78, 0.82, 0.86), "a0": 0.55, "grow": 1.4})

# animera alla dramaeffekter för hand (deterministiskt, drivet av dt)
func _update_fx(dt: float) -> void:
	if _flash_t < _flash_dur:
		_flash_t += dt
		var k: float = clampf(1.0 - _flash_t / _flash_dur, 0.0, 1.0)
		_flash.light_energy = _flash_peak * k * k
	else:
		_flash.light_energy = 0.0
	var c := _sflash.color
	if _sflash_t < _sflash_dur:
		_sflash_t += dt
		var k2: float = clampf(1.0 - _sflash_t / _sflash_dur, 0.0, 1.0)
		c.a = _sflash_amp * k2 * k2
	else:
		c.a = 0.0
	_sflash.color = c
	if _shake_t < _shake_dur:
		_shake_t += dt
	var alive := []
	for pf in _puffs:
		pf.t += dt
		var k3: float = pf.t / pf.dur
		if k3 >= 1.0:
			pf.n.queue_free()
			continue
		pf.vel.y -= 0.04 * dt
		var mi: MeshInstance3D = pf.n
		mi.position += pf.vel * dt
		var sc: float = 1.0 + pf.grow * k3
		mi.scale = Vector3(sc, sc, sc)
		var mm: StandardMaterial3D = pf.m
		var bc: Color = pf.col
		mm.albedo_color = Color(bc.r, bc.g, bc.b, pf.a0 * (1.0 - k3) * (1.0 - k3))
		alive.append(pf)
	_puffs = alive
	# skummet vid vattenlinjen sköts av havs-shaderns skumkrage (billboard-stänk
	# skymde piedestalen). _spawn_spray finns kvar men anropas inte.
	# blixt: en snabb dubbelblink-envelope som lyser upp scen + himmel
	if _bolt_t < _bolt_dur:
		_bolt_t += dt
		var flick: float = maxf(exp(-_bolt_t * 9.0) * (0.6 + 0.4 * sin(_bolt_t * 80.0)), 0.0)
		_bolt.light_energy = _bolt_peak * flick
	else:
		_bolt.light_energy = 0.0
	if _sky_mat:
		_sky_mat.set_shader_parameter("flash", clampf(_bolt.light_energy / 2.4, 0.0, 1.0))

# ------------------------------------------------------------- blixt/åska ---
func _schedule_bolts() -> void:
	# några blixtnedslag utspridda över partiet, deterministisk jitter
	var t := 4.0
	var i := 0
	while t < _play_dur - 1.5:
		_bolt_times.append(t)
		t += 8.0 + 4.0 * absf(_nrand(i * 7 + 3))     # ~8–12 s mellan blixtar
		i += 1

func _trigger_lightning() -> void:
	_bolt_t = 0.0
	_bolt_peak = 2.4
	_sflash_t = 0.0
	_sflash_amp = maxf(_sflash_amp, 0.38)            # kraftig helskärmsblixt
	_sfx("thunder", 0, int(round(0.5 * _fps)))       # åskan följer ~0.5 s efter

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
	_board_root.position.y = LIFT      # lyft brädet upp på piedestalen
	# OBS: _aabb() räknar i brädets LOKALA rum (den stannar vid roten och tar
	# inte med rotens egen position), så LIFT måste adderas till Y-höjden för
	# att brickorna ska hamna PÅ brädet, inte vid piedestalens fot.
	var ab := _aabb(_board_root)
	var bw: float = min(ab.size.x, ab.size.z)          # board footprint (X/Z plane, Y up in Godot)
	_bw = bw
	_cx = ab.position.x + ab.size.x * 0.5
	_cy = ab.position.z + ab.size.z * 0.5
	var play := bw - 2.0 * _border
	_cell = play / 8.0
	_surf_z = LIFT + ab.position.y + ab.size.y - 0.004  # brädets yta i världs-Y
	_board_bottom_y = LIFT + ab.position.y              # brädets underkant i världs-Y
	_disc_proto = _load_glb(_disc_glb)
	_disc_proto.visible = false
	add_child(_disc_proto)
	_disc_h = _aabb(_disc_proto).size.y            # for a center-pivot flip

func _build_trays() -> void:
	# I ö-arenan står brädet ensamt på piedestalen (som referensen) — inga
	# sidofack. Behåll koden men hoppa över den.
	if not SHOW_TRAYS:
		return
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
	_update_fx(dt)
	if _bolt_i < _bolt_times.size() and _elapsed >= _bolt_times[_bolt_i]:
		_bolt_i += 1
		_trigger_lightning()
	if _phase == "done":
		_done_t += dt
		return
	if _mi >= _moves.size():
		_phase = "done"
		if not _win_fired:
			_win_fired = true
			_sfx("win")
		return
	_pt += dt
	var mv: Dictionary = _moves[_mi]
	match _phase:
		"think":
			if _pt >= THINK_T:
				# drop the new disc in
				var h := _new_disc(mv.side, mv.cell)
				h.scale = Vector3(0.2, 0.2, 0.2)
				_sfx("place")
				_phase = "place"; _pt = 0.0
		"place":
			var k: float = clampf(_pt / PLACE_T, 0.0, 1.0)
			var e: float = 1.0 - pow(1.0 - k, 3.0)          # ease-out
			var s: float = 0.2 + 0.8 * e
			_discs[mv.cell].scale = Vector3(s, s, s)
			_discs[mv.cell].position.y = _surf_z + _disc_h / 2.0 + (1.0 - e) * 0.03   # settle down
			if k >= 1.0:
				_phase = "flip" if not mv.flips.is_empty() else "pause"
				_flip_fired = {}
				_trigger_drama(mv.cell, mv.flips.size())   # blixt/rök/skak vid stora kaskader
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
				if not _flip_fired.has(i):
					_flip_fired[i] = true
					_sfx("flip", i)
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
		_elapsed += dt
		_update_camera()
		if _sea_mat:
			_sea_mat.set_shader_parameter("t", _elapsed)
		if _sky_mat:
			_sky_mat.set_shader_parameter("t", _elapsed)
		await RenderingServer.frame_post_draw
		if not _record_dir.is_empty():
			var img := get_viewport().get_texture().get_image()
			img.save_png("%s/frame_%04d.png" % [_record_dir, _frame])
		_frame += 1
		if _phase == "done" and _done_t >= END_HOLD:
			break
	if not _record_dir.is_empty():
		_write_events()
	var sc := Rules.score(_final_board())
	print("GAME_OVER dark=%d light=%d winner=%s frames=%d" % [
		sc.dark, sc.light, ("dark" if sc.dark > sc.light else "light"), _frame])
	get_tree().quit()

# en rad per ljudhändelse: "frame kind idx" — läses av mux_audio.py
func _write_events() -> void:
	var lines := PackedStringArray()
	lines.append("fps %f" % _fps)
	for e in _events:
		lines.append("%d %s %d" % [e.f, e.kind, e.idx])
	var f := FileAccess.open("%s/audio_events.txt" % _record_dir, FileAccess.WRITE)
	if f:
		f.store_string("\n".join(lines))
		f.close()

func _final_board() -> PackedInt32Array:
	var b := PackedInt32Array(); b.resize(64); b.fill(0)
	for cell in _disc_side: b[cell] = _disc_side[cell]
	return b

# ------------------------------------------------------------- staging ----
const SEA_Y := -0.16        # havsnivå strax under öns marmorkant
const ISLAND_R := 0.62      # öns radie (liten ö → havet syns runtom)
const COL_R := 0.44         # kolonnernas radie runt brädet

func _build_stage() -> void:
	var env := WorldEnvironment.new()
	var e := Environment.new()
	# Stormig utomhusscen: en mörk, molnig himmel (sky-shader), dis som smälter
	# havet in i horisonten, och låg ambient så det svarta läser svart. AgX-tonemap.
	_sky_mat = ShaderMaterial.new()
	_sky_mat.shader = load("res://sky.gdshader")
	var sky := Sky.new()
	sky.sky_material = _sky_mat
	e.background_mode = Environment.BG_SKY
	e.sky = sky
	e.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	e.ambient_light_sky_contribution = 0.8
	e.ambient_light_color = Color(0.60, 0.68, 0.80)
	e.ambient_light_energy = 0.38          # dagsljus men ej utfrätt
	e.tonemap_mode = Environment.TONE_MAPPER_FILMIC   # punchigare färg än AgX (havet blir blågrönt)
	e.tonemap_white = 1.6
	e.fog_enabled = true
	e.fog_light_color = Color(0.72, 0.78, 0.84)   # ljus dis vid horisonten
	e.fog_density = 0.014                   # lätt dis — havet/himlen behåller färg
	e.fog_sky_affect = 0.0                 # låt shader-himlen stå för horisonten
	e.glow_enabled = false                 # inget bloom → skummet glöder inte
	env.environment = e
	add_child(env)

	# Dagsljus-sol: ljus, en aning varm, mjuka skuggor uppifrån-sidan.
	var key := DirectionalLight3D.new()
	key.light_color = Color(1.0, 0.95, 0.85)   # varmt dagsljus
	key.light_energy = 1.7                 # ljust men marmorns ådring/flöjlar syns
	key.light_angular_distance = 2.0
	key.shadow_enabled = true
	key.rotation = Vector3(deg_to_rad(-52.0), deg_to_rad(-38.0), 0.0)
	add_child(key)
	# himmelsblå fill från kamerahållet
	var fill := OmniLight3D.new()
	fill.light_color = Color(0.70, 0.80, 0.95)
	fill.light_energy = 0.40
	fill.omni_range = 5.0
	fill.position = Vector3(0.1, 0.8, 1.0)
	add_child(fill)

	# blixtljus (mörkt tills det slår till), riktat snett uppifrån
	_bolt = DirectionalLight3D.new()
	_bolt.light_color = Color(0.92, 0.96, 1.0)
	_bolt.light_energy = 0.0
	_bolt.rotation = Vector3(deg_to_rad(-62.0), deg_to_rad(40.0), 0.0)
	add_child(_bolt)

	_build_sea()
	_build_rock_island()
	_build_coast()
	if _sea_mat:
		_sea_mat.set_shader_parameter("island_r", ISLAND_R)

	_cam = Camera3D.new()
	_cam.fov = 60
	add_child(_cam)
	_update_camera()   # sätt startpose (rakt ovanför)

# stort vågplan med den deterministiska hav-shadern
func _build_sea() -> void:
	var sea := MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(60.0, 60.0)
	pm.subdivide_width = 200        # tätare → Gerstner-vågorna löser upp sig nära ön
	pm.subdivide_depth = 200
	sea.mesh = pm
	_sea_mat = ShaderMaterial.new()
	_sea_mat.shader = load("res://sea.gdshader")
	sea.material_override = _sea_mat
	sea.position = Vector3(0.0, SEA_Y, 0.0)
	add_child(sea)

# liten klippö med marmortopp som brädet och kolonnerna står på. Klippans topp
# ligger UNDER marmorplattans undersida (ingen koplanär yta → inget z-fight), och
# marmortoppen ligger en aning under brädets underkant.
# en knölig klippö (vertex-förskjuten sfär via rock.gdshader) med ett par mindre
# klippblock vid vattenlinjen — en ojämn skärgårdsklack, inte en slät skiva
func _build_rock_island() -> void:
	var mat := ShaderMaterial.new()
	mat.shader = load("res://rock.gdshader")
	mat.set_shader_parameter("waterline", SEA_Y)
	# huvudklacken: bred, flack, kraftigt förskjuten sfär (skrovlig utåtklack),
	# topp ~y=0 (piedestalens fot), bred fot ned i havet
	var main := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = ISLAND_R * 1.15
	sm.height = ISLAND_R * 1.6
	sm.radial_segments = 96
	sm.rings = 48
	main.mesh = sm
	main.material_override = mat
	main.scale = Vector3(1.0, 0.52, 1.0)
	main.position = Vector3(0.0, -0.28, 0.0)
	add_child(main)
	# taggiga klippblock runt foten — några sticker upp som spetsar, andra ligger
	# vid vattenlinjen; varierad storlek/höjd för en ojämn skärgårdssilhuett
	for i in 9:
		var a := TAU * float(i) / 9.0 + 0.4 * _nrand(i * 5 + 1)
		var r := ISLAND_R * (0.75 + 0.35 * absf(_nrand(i * 5 + 2)))
		var chunk := MeshInstance3D.new()
		var cs := SphereMesh.new()
		var cr := ISLAND_R * (0.3 + 0.22 * absf(_nrand(i * 5 + 7)))
		cs.radius = cr
		cs.height = cr * (1.6 + 1.4 * absf(_nrand(i * 5 + 13)))   # några spetsiga
		cs.radial_segments = 40
		cs.rings = 24
		chunk.mesh = cs
		chunk.material_override = mat
		var peak := 0.5 + 0.5 * absf(_nrand(i * 5 + 3))
		chunk.scale = Vector3(1.0, peak, 1.0)
		chunk.position = Vector3(r * sin(a), SEA_Y - 0.02 + 0.10 * absf(_nrand(i)), r * cos(a))
		chunk.rotation.y = _nrand(i * 5 + 9) * PI
		add_child(chunk)

# EN central marmorpelare som piedestal: brädet vilar på kapitälet (brädet är
# lyft LIFT). Skalas så toppen når brädets underkant.
func _build_pedestal() -> void:
	var col := _load_glb(_col_glb)
	if col == null:
		return
	col.visible = false
	add_child(col)
	var h := _aabb(col).size.y
	# skala så pelarens TOPP (kapitälet) hamnar strax under brädets underkant, med
	# en liten överlappning — brädet vilar på kapitälet, inget som tränger upp
	# genom brädets ovansida (buggen: tidigare gissad höjd översköt brädet).
	var target_top := _board_bottom_y + 0.01
	var sc := target_top / maxf(h, 0.001)
	var inst: Node3D = col.duplicate()
	inst.visible = true
	inst.scale = Vector3(sc * 1.5, sc, sc * 1.5)   # bredare → stadigare piedestal
	inst.position = Vector3(0.0, 0.0, 0.0)         # fot på klipptoppen (~y=0)
	add_child(inst)
	# pelarens topp i världs-Y (fot vid y=0, base-center-origin → lokal topp ≈ h)
	_pedestal_top_y = inst.position.y + sc * h

# avlägsen kustlinje: några låga, mörka, disiga uddar nära horisonten (fog gör
# dem atmosfäriskt bleka), utspridda på fjärran sidorna som i referensen
func _build_coast() -> void:
	# avlägsna kullar/uddar i lager: en högre bakre ås + lägre främre kullar,
	# atmosfäriskt bleka (ljusare = längre bort) som i referensen
	var ridges := [
		# dist, vinkel°, höjd, bredd, blekhet(0 mörkast..1 ljusast)
		[24.0, -70.0, 2.4, 16.0, 0.85], [17.0, -95.0, 1.2, 10.0, 0.55],
		[26.0, 120.0, 2.0, 18.0, 0.9], [19.0, 150.0, 1.0, 9.0, 0.5],
		[30.0, 40.0, 2.8, 22.0, 0.95],
	]
	for r in ridges:
		var dist: float = r[0]
		var ang := deg_to_rad(r[1])
		var hgt: float = r[2]
		var wid: float = r[3]
		var haze: float = r[4]
		var m := MeshInstance3D.new()
		# tillplattad prismalik kulle (box med avsmalnande topp via skalning)
		var bx := BoxMesh.new()
		bx.size = Vector3(wid, hgt, wid * 0.4)
		m.mesh = bx
		var mm := StandardMaterial3D.new()
		# ju längre bort desto blekare/blåare (luftperspektiv)
		var col := Color(0.42, 0.50, 0.58).lerp(Color(0.74, 0.79, 0.84), haze)
		mm.albedo_color = col
		mm.roughness = 1.0
		m.material_override = mm
		m.position = Vector3(dist * sin(ang), SEA_Y + hgt * 0.5 - 0.15, dist * cos(ang))
		m.rotation.y = ang
		add_child(m)

# Spiralnedstigning: elevationen sjunker mjukt från nästan rakt ovanför till en
# fin sidovy medan azimuten snurrar CAM_SPINS varv och bromsar in i sluts läget.
# En smoothstep på speltiden gör att kameran landar exakt när partiet tar slut.
func _update_camera() -> void:
	var t: float = clampf(_elapsed / _play_dur, 0.0, 1.0)
	var s: float = smoothstep(0.0, 1.0, t)
	var el := deg_to_rad(lerpf(CAM_EL_TOP, CAM_EL_END, s))
	var az: float = TAU * CAM_SPINS * (1.0 - s)     # snurrar, landar på az=0 (sidovy)
	var dist := lerpf(CAM_D_TOP, CAM_D_END, s)
	var pos := CAM_CENTER + dist * Vector3(cos(el) * sin(az), sin(el), cos(el) * cos(az))
	# kameraskak vid stora kaskader (deterministisk pseudo-slump på _frame)
	if _shake_t < _shake_dur:
		var kk: float = 1.0 - _shake_t / _shake_dur
		var amp: float = _shake_amp * kk * kk
		pos += Vector3(_nrand(_frame * 3 + 1), _nrand(_frame * 3 + 7), _nrand(_frame * 3 + 13)) * amp
	_aim_camera(pos, CAM_CENTER, az)

# Bygg kameraorienteringen för hand — look_at() rakt nedåt är degenererad (upp
# blir parallell med blickriktningen). "right" härleds ur azimuten så att den
# är väldefinierad även rakt ovanför, och bilden roterar mjukt genom spiralen.
func _aim_camera(pos: Vector3, target: Vector3, az: float) -> void:
	var backward := (pos - target).normalized()
	var right := Vector3(cos(az), 0.0, -sin(az))
	var up := backward.cross(right).normalized()
	right = up.cross(backward).normalized()
	_cam.global_transform = Transform3D(Basis(right, up, backward), pos)

# deterministisk pseudo-slump i [-1,1] (fract-of-sin-hash) — ingen global RNG
func _nrand(n: int) -> float:
	var v: float = sin(float(n) * 12.9898 + 78.233) * 43758.5453
	return (v - floor(v)) * 2.0 - 1.0
