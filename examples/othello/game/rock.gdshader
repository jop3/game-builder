// rock.gdshader — realistisk våt kustklippa (havsstack). Silhuett: tre lager
// förskjutning (stora RIDGED åsar + mellanåsar + fint korn). Yta: mikro-knottriga
// normaler ur 3D-brusets gradient. Färg efter FORM: sedimentära lager (strata),
// AO i sprickor, ljusa torra kanter, lav på flata toppar, blöt/mörk vid vattnet.
// Statiskt (inget TIME) → deterministiskt.
shader_type spatial;

uniform vec3 rock_dark : source_color = vec3(0.05, 0.05, 0.055);   // sprickor / skugga
uniform vec3 rock_grey : source_color = vec3(0.30, 0.29, 0.27);   // torr grå sten
uniform vec3 rock_warm : source_color = vec3(0.36, 0.29, 0.21);   // ockra/brunt strata-lager
uniform vec3 lichen : source_color = vec3(0.34, 0.36, 0.28);      // lav på flata toppar
uniform float disp = 0.30;
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
	float big = ridged(VERTEX * 2.6 + 4.0);          // stora åsar
	float mid = ridged(VERTEX * 6.0 + 1.0);          // mellanåsar
	float fine = fbm(VERTEX * 12.0);                 // korn
	VERTEX += NORMAL * ((big - 0.5) * disp
	                  + (mid - 0.5) * disp * 0.4
	                  + (fine - 0.5) * disp * 0.18);
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	// geometrisk normal ur den förskjutna ytan
	vec3 gn = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	// mikro-knottrighet: normal-perturbation ur gradienten av 3D-brus (världsrum)
	float e = 0.010;
	float d0 = fbm(v_wpos * 17.0);
	vec3 grad = vec3(fbm((v_wpos + vec3(e, 0, 0)) * 17.0) - d0,
	                 fbm((v_wpos + vec3(0, e, 0)) * 17.0) - d0,
	                 fbm((v_wpos + vec3(0, 0, e)) * 17.0) - d0) / e;
	vec3 n = normalize(gn - grad * 0.04);
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);

	// sedimentära lager (strata): nästan horisontella band som warpas lite
	float warp = fbm(v_opos * 2.2);
	float strat = fbm(vec3(v_opos.x * 1.4, (v_opos.y + 0.18 * warp) * 7.5, v_opos.z * 1.4));
	float m = ridged(v_opos * 7.0);                  // sprickor/åsar
	float detail = fbm(v_wpos * 26.0);

	vec3 col = mix(rock_grey, rock_warm, smoothstep(0.35, 0.7, strat));
	col = mix(col, rock_dark, (1.0 - smoothstep(0.16, 0.6, m)) * 0.85);   // AO i sprickor
	col *= 0.82 + 0.32 * detail;                     // korn-variation
	float edge = smoothstep(0.62, 0.85, m);          // torra ljusa kanter/åskrön
	col += edge * 0.10;
	float flatness = smoothstep(0.55, 0.92, gn.y);   // uppåtvända ytor → lav
	col = mix(col, lichen, flatness * 0.4 * smoothstep(0.3, 0.7, detail));

	// blött nära/under vattenlinjen
	float wet = smoothstep(waterline + 0.14, waterline - 0.06, v_wpos.y);
	col *= mix(1.0, 0.42, wet);
	ALBEDO = col;
	ROUGHNESS = clamp(mix(0.92, 0.38, wet) - 0.1 * detail, 0.25, 0.96);
	METALLIC = 0.0;
	SPECULAR = mix(0.25, 0.55, wet);
}
