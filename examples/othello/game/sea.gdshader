// sea.gdshader — stormigt, choppigt hav med vitkammar och skum längs klippkanten.
// Vågor summeras av riktade sinusvågor i vertex, normalen tas ur den förskjutna
// världspositionen (dFdx/dFdy). Skummet bryts upp av brus (vitkammar), plus en
// skumkrage där havet möter ön (island_r). Animeras av uniformen `t` (spelets
// _elapsed) — ALDRIG TIME — så inspelningen förblir deterministisk och FPS-låst.
shader_type spatial;
render_mode cull_back, specular_schlick_ggx;

uniform float t = 0.0;
uniform float amp = 0.05;
uniform float island_r = 0.62;
uniform vec3 deep : source_color = vec3(0.015, 0.10, 0.14);
uniform vec3 shallow : source_color = vec3(0.08, 0.32, 0.34);
uniform vec3 foam_col : source_color = vec3(0.86, 0.90, 0.92);

varying vec3 v_wpos;
varying float v_h;

float wv(vec2 p, vec2 dir, float freq, float speed, float ph) {
	return sin(dot(p, dir) * freq + t * speed + ph);
}

float height(vec2 p) {
	float h = 0.0;
	h += wv(p, normalize(vec2(1.0, 0.3)),  3.0, 1.6, 0.0) * 0.55;
	h += wv(p, normalize(vec2(-0.4, 1.0)), 4.5, 2.1, 1.3) * 0.4;
	h += wv(p, normalize(vec2(0.7, -0.7)), 7.0, 2.8, 2.0) * 0.28;
	h += wv(p, normalize(vec2(0.2, 1.0)), 11.0, 3.5, 0.5) * 0.16;
	h += wv(p, normalize(vec2(-0.9, 0.2)), 17.0, 4.2, 2.7) * 0.09;   // extra chop
	return h;   // ~[-1.48, 1.48]
}

float hash(vec2 p) { return fract(sin(dot(p, vec2(41.3, 289.1))) * 43758.5453); }
float vnoise(vec2 p) {
	vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
	float a = hash(i), b = hash(i + vec2(1.0, 0.0));
	float c = hash(i + vec2(0.0, 1.0)), d = hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

void vertex() {
	float h = height(VERTEX.xz);
	VERTEX.y += h * amp;
	v_h = h / 1.48;                                    // normerad ~[-1,1]
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec3 n = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);

	// vitkammar: skum på topparna, brutet av TVÅ brus-skalor (grova fläckar + fin
	// textur) plus utdragna skumstråk, så det aldrig blir jämna vita band
	float fbig = vnoise(v_wpos.xz * 4.0 + vec2(t * 0.12, -t * 0.08));
	float ffine = vnoise(v_wpos.xz * 16.0 - vec2(t * 0.2, t * 0.15));
	float streak = vnoise(v_wpos.xz * vec2(3.0, 22.0) + vec2(0.0, t * 0.5));
	float capmask = smoothstep(0.30, 0.62, fbig) * (0.6 + 0.4 * ffine) * (0.5 + 0.5 * streak);
	float crest = smoothstep(0.06, 0.5, v_h) * capmask;
	// skumkrage där havet bryter mot ön (bruten så den inte blir en jämn ring)
	float d = length(v_wpos.xz);
	float shore = smoothstep(island_r + 0.26, island_r - 0.02, d)
	            * smoothstep(island_r - 0.22, island_r - 0.02, d)
	            * (0.5 + 0.5 * vnoise(v_wpos.xz * 12.0 + t * 0.6));
	float foam = clamp(max(crest, shore), 0.0, 1.0);

	// djupare i vågdalarna, ljusare mot topparna → mer djup i havet
	vec3 col = mix(deep, shallow, clamp(v_h * 0.6 + 0.45, 0.0, 1.0));
	col = mix(col, foam_col, foam);
	ALBEDO = col;
	ROUGHNESS = mix(0.12, 0.65, foam);
	METALLIC = 0.0;
	SPECULAR = 0.5;
}
