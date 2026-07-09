# audio.gd — procedurell ljudmotor för Othello. Genererar all PCM i kod (inga
# ljud-assets), i samma anda som Snittets audio.gd. Tre korta effekter:
#   place  — ett mjukt trä-"tok" när en bricka läggs
#   flip   — ett lätt "tick" när en bricka vänds (liten tonhöjdsvariation)
#   win    — en mjuk stigande klocka när partiet är slut
#
# Effekterna byggs som AudioStreamWAV (16-bit mono). Vid inspelning sparas de
# till .wav (headless-drivern spelar inget ljud) så en Python-muxer kan lägga
# dem på ljudspåret vid rätt tidpunkter; i interaktivt läge spelas de direkt.
class_name OthelloAudio

const RATE := 22050

# ---- PCM → AudioStreamWAV ------------------------------------------------
static func _wav(samples: PackedFloat32Array) -> AudioStreamWAV:
	var st := AudioStreamWAV.new()
	st.format = AudioStreamWAV.FORMAT_16_BITS
	st.mix_rate = RATE
	st.stereo = false
	var data := PackedByteArray()
	data.resize(samples.size() * 2)
	for i in samples.size():
		var s: float = clampf(samples[i], -1.0, 1.0)
		data.encode_s16(i * 2, int(s * 32767.0))
	st.data = data
	return st

# ---- effekterna ----------------------------------------------------------

# Mjukt trä-"tok": två låga partialer med snabb exponentiell avklingning plus
# en kort ljus transient (anslaget mot brädet).
static func make_place() -> AudioStreamWAV:
	var n := int(RATE * 0.16)
	var s := PackedFloat32Array(); s.resize(n)
	for i in n:
		var t := float(i) / RATE
		var env: float = exp(-t * 24.0)
		var body: float = sin(TAU * 172.0 * t) * 0.6 + sin(TAU * 315.0 * t) * 0.28
		var click := 0.0
		if t < 0.010:
			click = (1.0 - t / 0.010) * sin(TAU * 1300.0 * t)
		s[i] = (body * env + click * 0.35) * 0.5
	return _wav(s)

# Lätt "tick" när en bricka vänds; seedv ger en liten tonhöjdsvariation så en
# vändningskaskad inte låter helt mekanisk.
static func make_flip(seedv: int) -> AudioStreamWAV:
	var n := int(RATE * 0.09)
	var s := PackedFloat32Array(); s.resize(n)
	var base: float = 540.0 + float((seedv * 67) % 150)
	for i in n:
		var t := float(i) / RATE
		var env: float = exp(-t * 42.0)
		var tone: float = sin(TAU * base * t) * 0.5 + sin(TAU * base * 2.02 * t) * 0.18
		s[i] = tone * env * 0.45
	return _wav(s)

# Åska: ett lågt muller — brusband som byggs upp och klingar av långsamt, med
# en låg sinus-svep under. Deterministiskt brus via hash (ingen global RNG).
static func make_thunder() -> AudioStreamWAV:
	var dur := 1.8
	var n := int(RATE * dur)
	var s := PackedFloat32Array(); s.resize(n)
	var prev := 0.0
	for i in n:
		var t := float(i) / RATE
		# långsam attack + lång svans
		var env: float = clampf(t / 0.12, 0.0, 1.0) * exp(-t * 1.9)
		# lågpassat brus (enkel en-pols) för mullret; deterministiskt hash-brus
		var hv: float = sin(float(i) * 12.9898) * 43758.5453
		var white: float = (hv - floor(hv)) * 2.0 - 1.0
		prev = prev + 0.06 * (white - prev)         # lågpass
		var sub: float = sin(TAU * (48.0 - 18.0 * t) * t) * 0.4   # låg svep
		s[i] = clampf((prev * 2.6 + sub) * env * 0.8, -1.0, 1.0)
	return _wav(s)

# Bränningsskvätt: bandpassat brus med snabb attack och medellång svans —
# ett "SCHWOSCH" när en våg slår mot klippan. seedv varierar brusfärgen.
static func make_surf(seedv: int) -> AudioStreamWAV:
	var dur := 1.1
	var n := int(RATE * dur)
	var s := PackedFloat32Array(); s.resize(n)
	var lp := 0.0
	var lp2 := 0.0
	var seedf := 12.9898 + 0.37 * float(seedv)
	for i in n:
		var t := float(i) / RATE
		var env: float = clampf(t / 0.05, 0.0, 1.0) * exp(-t * 3.4)
		var hv: float = sin(float(i) * seedf) * 43758.5453
		var white: float = (hv - floor(hv)) * 2.0 - 1.0
		lp = lp + 0.30 * (white - lp)                # snabb lågpass
		lp2 = lp2 + 0.045 * (white - lp2)            # långsam lågpass
		s[i] = clampf((lp - lp2) * 2.2 * env * 0.6, -1.0, 1.0)   # bandet = skvättet
	return _wav(s)

# Havsbrus-loop: två lågpassade bruslager med långsam dyning (hela LFO-cykler
# per loop) och korsfadad skarv → sömlös bakgrund under hela partiet.
static func make_sea_loop() -> AudioStreamWAV:
	var dur := 6.0
	var fade := int(RATE * 0.30)
	var n := int(RATE * dur)
	var tmp := PackedFloat32Array(); tmp.resize(n + fade)
	var lp := 0.0
	var lp2 := 0.0
	for i in n + fade:
		var t := float(i) / RATE
		var hv: float = sin(float(i) * 7.4321) * 43758.5453
		var white: float = (hv - floor(hv)) * 2.0 - 1.0
		lp = lp + 0.12 * (white - lp)
		lp2 = lp2 + 0.02 * (white - lp2)
		# dyning: 2 + 3 hela cykler per loop → skarven är fasren
		var swell: float = 0.55 + 0.30 * sin(TAU * 2.0 * t / dur) + 0.15 * sin(TAU * 3.0 * t / dur + 1.3)
		tmp[i] = (lp * 0.7 + lp2 * 1.6) * swell * 0.5
	var s := PackedFloat32Array(); s.resize(n)
	for i in n:
		s[i] = tmp[i]
	for i in fade:   # korsfada svansen in i huvudet → inget klick vid loopning
		var k := float(i) / float(fade)
		s[i] = tmp[i] * k + tmp[n + i] * (1.0 - k)
	var st := _wav(s)
	st.loop_mode = AudioStreamWAV.LOOP_FORWARD
	st.loop_begin = 0
	st.loop_end = n
	return st

# Mjuk stigande klocka (C–E–G–C arpeggio) med klockliknande övertoner.
static func make_win() -> AudioStreamWAV:
	var dur := 1.5
	var n := int(RATE * dur)
	var s := PackedFloat32Array(); s.resize(n)
	var notes := [523.25, 659.25, 783.99, 1046.5]   # C5 E5 G5 C6
	for k in notes.size():
		var f: float = notes[k]
		var start: float = k * 0.15
		for i in n:
			var t := float(i) / RATE - start
			if t < 0.0:
				continue
			var env: float = exp(-t * 2.8)
			s[i] += sin(TAU * f * t) * env * 0.20 * (1.0 + 0.14 * sin(TAU * f * 2.0 * t))
	for i in n:
		s[i] = clampf(s[i], -1.0, 1.0)
	return _wav(s)
