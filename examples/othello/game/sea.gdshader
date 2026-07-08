// sea.gdshader — stormigt hav. Vågor summeras av riktade sinusvågor i vertex,
// normalen tas ur den förskjutna världspositionen (dFdx/dFdy) och skum läggs på
// vågtopparna. Animeras av uniformen `t` (matas från spelets _elapsed) — ALDRIG
// TIME — så inspelningen förblir deterministisk och FPS-låst.
shader_type spatial;
render_mode cull_back, specular_schlick_ggx;

uniform float t = 0.0;
uniform float amp = 0.05;
uniform vec3 deep : source_color = vec3(0.035, 0.075, 0.11);
uniform vec3 shallow : source_color = vec3(0.07, 0.16, 0.19);
uniform vec3 foam_col : source_color = vec3(0.72, 0.78, 0.84);

varying vec3 v_wpos;
varying float v_h;

float wv(vec2 p, vec2 dir, float freq, float speed, float ph) {
	return sin(dot(p, dir) * freq + t * speed + ph);
}

float height(vec2 p) {
	float h = 0.0;
	h += wv(p, normalize(vec2(1.0, 0.3)),  3.0, 1.6, 0.0) * 0.6;
	h += wv(p, normalize(vec2(-0.4, 1.0)), 4.5, 2.1, 1.3) * 0.4;
	h += wv(p, normalize(vec2(0.7, -0.7)), 7.0, 2.8, 2.0) * 0.18;
	h += wv(p, normalize(vec2(0.2, 1.0)), 11.0, 3.5, 0.5) * 0.06;
	return h;   // ~[-1.24, 1.24]
}

void vertex() {
	float h = height(VERTEX.xz);
	VERTEX.y += h * amp;
	v_h = h / 1.24;                                    // normerad ~[-1,1]
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	// geometrisk normal ur den faktiskt förskjutna ytan
	vec3 n = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);

	float crest = smoothstep(0.6, 1.05, v_h) * 0.75;   // skum bara på högsta topparna
	vec3 col = mix(deep, shallow, clamp(v_h * 0.5 + 0.5, 0.0, 1.0));
	col = mix(col, foam_col, crest);
	ALBEDO = col;
	ROUGHNESS = mix(0.12, 0.55, crest);
	METALLIC = 0.0;
	SPECULAR = 0.4;
}
