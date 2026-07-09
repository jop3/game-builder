// rock.gdshader — knölig våt klippa. Vertexar förskjuts längs normalen av fbm-brus
// (statiskt, inget TIME → deterministiskt); normalen räknas om ur den förskjutna
// världspositionen (dFdx/dFdy) så ytan blir fasetterad och skrovlig. Mörkare och
// blötare nära vattenlinjen.
shader_type spatial;

uniform vec3 rock_lo : source_color = vec3(0.05, 0.055, 0.06);
uniform vec3 rock_hi : source_color = vec3(0.22, 0.21, 0.19);
uniform float disp = 0.13;
uniform float waterline = -0.12;

varying vec3 v_wpos;
varying vec3 v_opos;

float hash(vec3 p) { return fract(sin(dot(p, vec3(27.1, 61.7, 12.4))) * 43758.5453); }
float vnoise(vec3 p) {
	vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
	float n000 = hash(i), n100 = hash(i + vec3(1, 0, 0));
	float n010 = hash(i + vec3(0, 1, 0)), n110 = hash(i + vec3(1, 1, 0));
	float n001 = hash(i + vec3(0, 0, 1)), n101 = hash(i + vec3(1, 0, 1));
	float n011 = hash(i + vec3(0, 1, 1)), n111 = hash(i + vec3(1, 1, 1));
	return mix(mix(mix(n000, n100, f.x), mix(n010, n110, f.x), f.y),
	           mix(mix(n001, n101, f.x), mix(n011, n111, f.x), f.y), f.z);
}
float fbm(vec3 p) {
	float s = 0.0, a = 0.5;
	for (int i = 0; i < 4; i++) { s += a * vnoise(p); p *= 2.03; a *= 0.5; }
	return s;
}

void vertex() {
	v_opos = VERTEX;
	float n = fbm(VERTEX * 3.2 + 5.0);
	VERTEX += NORMAL * (n - 0.5) * disp;
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec3 n = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);
	float m = fbm(v_opos * 6.0);
	vec3 col = mix(rock_lo, rock_hi, m);
	// blötare/mörkare nära och under vattenlinjen
	float wet = smoothstep(waterline + 0.14, waterline - 0.05, v_wpos.y);
	col *= mix(1.0, 0.5, wet);
	ALBEDO = col;
	ROUGHNESS = mix(0.9, 0.5, wet);   // blött = blankare
	METALLIC = 0.0;
	SPECULAR = mix(0.2, 0.5, wet);
}
