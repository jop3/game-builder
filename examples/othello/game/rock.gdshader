// rock.gdshader — vass, skrovlig våt klippa. Vertexar förskjuts av RIDGED fbm
// (skarpa åsar, inte mjuka bulor) längs normalen; normalen räknas om ur den
// förskjutna världspositionen (dFdx/dFdy) → fasetterad sten. Varmare, ljusare
// torr sten upptill, mörk blöt sten vid vattenlinjen. Statiskt (inget TIME).
shader_type spatial;

uniform vec3 rock_lo : source_color = vec3(0.10, 0.095, 0.085);
uniform vec3 rock_hi : source_color = vec3(0.34, 0.31, 0.27);
uniform float disp = 0.26;
uniform float waterline = -0.16;

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
// ridged fbm: skarpa åsar och sprickor snarare än mjuka kullar
float ridged(vec3 p) {
	float s = 0.0, a = 0.5;
	for (int i = 0; i < 5; i++) {
		float n = 1.0 - abs(2.0 * vnoise(p) - 1.0);
		s += a * n * n;
		p *= 2.07; a *= 0.5;
	}
	return s;
}

void vertex() {
	v_opos = VERTEX;
	float n = ridged(VERTEX * 3.0 + 4.0);
	VERTEX += NORMAL * (n - 0.5) * disp;
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec3 n = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);
	float m = ridged(v_opos * 7.0);
	// mörkare i sprickorna (åsdalar), ljusare på kanterna
	vec3 col = mix(rock_lo, rock_hi, smoothstep(0.2, 0.7, m));
	float wet = smoothstep(waterline + 0.14, waterline - 0.06, v_wpos.y);
	col *= mix(1.0, 0.45, wet);
	ALBEDO = col;
	ROUGHNESS = mix(0.92, 0.45, wet);
	METALLIC = 0.0;
	SPECULAR = mix(0.2, 0.55, wet);
}
