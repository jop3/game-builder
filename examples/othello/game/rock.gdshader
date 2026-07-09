// rock.gdshader — realistisk våt klippa. Silhuetten byggs av RIDGED fbm-förskjutning
// (skarpa åsar) + ett finare bulle-lager. Ytan får mikro-knottrighet via gradienten
// av 3D-brus (knottriga normaler), och färgen varieras av sprickor (AO i dalarna),
// lutning (lav/torr topp på uppåtytor, grå sten på branta) och allmän brusvariation.
// Blötare/mörkare/blankare nära vattenlinjen. Statiskt (inget TIME) → deterministiskt.
shader_type spatial;

uniform vec3 rock_lo : source_color = vec3(0.06, 0.06, 0.065);   // sprickor / skugga
uniform vec3 rock_hi : source_color = vec3(0.32, 0.30, 0.27);   // upplysta kanter
uniform vec3 lichen : source_color = vec3(0.33, 0.35, 0.28);    // lav på flata toppar
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
float fbm(vec3 p) {
	float s = 0.0, a = 0.5;
	for (int i = 0; i < 4; i++) { s += a * vnoise(p); p *= 2.05; a *= 0.5; }
	return s;
}
float ridged(vec3 p) {
	float s = 0.0, a = 0.5;
	for (int i = 0; i < 5; i++) {
		float n = 1.0 - abs(2.0 * vnoise(p) - 1.0);
		s += a * n * n; p *= 2.07; a *= 0.5;
	}
	return s;
}

void vertex() {
	v_opos = VERTEX;
	float big = ridged(VERTEX * 3.0 + 4.0);          // stora åsar
	float fine = fbm(VERTEX * 9.0);                  // finare bulor
	VERTEX += NORMAL * ((big - 0.5) * disp + (fine - 0.5) * disp * 0.35);
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	// geometrisk normal ur den förskjutna ytan
	vec3 gn = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	// mikro-knottrighet: perturbera normalen med gradienten av 3D-brus (världsrum,
	// ingen UV-stretch), så ljuset fångar korn och små sprickor
	float e = 0.012;
	float d0 = fbm(v_wpos * 15.0);
	vec3 grad = vec3(fbm((v_wpos + vec3(e, 0, 0)) * 15.0) - d0,
	                 fbm((v_wpos + vec3(0, e, 0)) * 15.0) - d0,
	                 fbm((v_wpos + vec3(0, 0, e)) * 15.0) - d0) / e;
	vec3 n = normalize(gn - grad * 0.03);
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);

	// färgvariation
	float m = ridged(v_opos * 7.0);
	float detail = fbm(v_wpos * 24.0);
	float crev = smoothstep(0.18, 0.62, m);                 // 0 i sprickor → mörkt
	vec3 col = mix(rock_lo, rock_hi, crev * (0.55 + 0.45 * detail));
	float flatness = smoothstep(0.55, 0.92, gn.y);          // uppåtvända ytor
	col = mix(col, lichen, flatness * 0.35 * smoothstep(0.3, 0.7, detail));
	col *= 0.85 + 0.3 * detail;

	// blött nära/under vattenlinjen
	float wet = smoothstep(waterline + 0.14, waterline - 0.06, v_wpos.y);
	col *= mix(1.0, 0.45, wet);
	ALBEDO = col;
	ROUGHNESS = clamp(mix(0.9, 0.4, wet) - 0.12 * detail, 0.25, 0.95);
	METALLIC = 0.0;
	SPECULAR = mix(0.25, 0.55, wet);
}
