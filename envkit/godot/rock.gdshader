// rock.gdshader — realistisk torr kustklippa (havsstack). Silhuett: tre lager
// förskjutning (stora RIDGED åsar + mellanåsar + fint korn), samplade med en
// per-instans världsoffset så varje klippblock får EGEN struktur (lokala
// koordinater gav mindre än en brusperiod per litet block → släta vaxklumpar).
// Yta: mikro-knottriga normaler ur 3D-brusets gradient. Färg efter FORM och
// VÄRLDSLÄGE: sedimentära band i världs-Y (sammanhängande lager över hela ön),
// AO i sprickor, ljusa torra kanter, lav på flata toppar, blöt/mörk bara i ett
// smalt band vid vattnet. Statiskt (inget TIME) → deterministiskt.
shader_type spatial;

uniform vec3 rock_dark : source_color = vec3(0.09, 0.08, 0.07);   // sprickor / skugga
uniform vec3 rock_grey : source_color = vec3(0.47, 0.43, 0.37);   // torr varmgrå kalksten
uniform vec3 rock_warm : source_color = vec3(0.56, 0.45, 0.30);   // ockra/tan strata-lager
uniform vec3 lichen : source_color = vec3(0.33, 0.38, 0.24);      // lav/örtgrönt på flata toppar
uniform float disp = 0.24;
uniform float waterline = -0.16;
// per-instans skala på förskjutningen: små klippblock måste förskjutas
// proportionellt mot sin egen radie — fast amplitud gav glasskärvs-spikar
// på blocken (± halva radien) medan huvudön såg rätt ut
instance uniform float dscale = 1.0;

varying vec3 v_wpos;
varying vec3 v_snormal;   // slät (interpolerad) världsnormal — mjukar upp facetterna

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
	// samplas i OBJEKTSTORLEKS-enheter (VERTEX/dscale) + per-instans offset:
	// varje klippblock får egen, självlik struktur — fast frekvens gav mindre
	// än en brusperiod över små block (→ släta potatisar)
	vec3 sp = VERTEX / max(dscale, 0.2) + NODE_POSITION_WORLD * 7.3;
	float big = ridged(sp * 3.0 + 4.0);              // stora åsar
	float mid = ridged(sp * 8.0 + 1.0);              // mellanåsar
	float fine = fbm(sp * 16.0);                     // korn
	// platta av mot lokala polen: sfärens UV-kläm + förskjutning gav ett radiellt
	// "stjärnmönster" rakt under piedestalen; toppen ska ändå vara en hylla
	float pf = 1.0 - 0.8 * smoothstep(0.75, 0.97, NORMAL.y);
	VERTEX += NORMAL * dscale * pf * ((big - 0.5) * disp
	                  + (mid - 0.5) * disp * 0.30
	                  + (fine - 0.5) * disp * 0.12);
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	v_snormal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
}

void fragment() {
	// geometrisk normal ur den förskjutna ytan, uppmjukad mot den släta
	// interpolerade normalen — ren dFdx-normal gav jättelika platta facetter
	// (skrynklat kristallpapper), blandningen läser som nött sten
	vec3 gn = normalize(cross(dFdx(v_wpos), dFdy(v_wpos)));
	gn = normalize(mix(normalize(v_snormal), gn, 0.55));
	// mikro-knottrighet: normal-perturbation ur gradienten av 3D-brus (världsrum)
	float e = 0.010;
	float d0 = fbm(v_wpos * 17.0);
	vec3 grad = vec3(fbm((v_wpos + vec3(e, 0, 0)) * 17.0) - d0,
	                 fbm((v_wpos + vec3(0, e, 0)) * 17.0) - d0,
	                 fbm((v_wpos + vec3(0, 0, e)) * 17.0) - d0) / e;
	vec3 n = normalize(gn - grad * 0.05);
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);

	// sedimentära lager: SMALA horisontella band i världs-Y som löper
	// sammanhängande över hela ön (sin-band + brus-warp, inte fbm-blobbar)
	float warp = fbm(v_wpos * 3.0);
	float band = 0.5 + 0.5 * sin(v_wpos.y * 42.0 + 2.2 * warp);
	float m = ridged(v_wpos * 6.0);                  // sprickor/åsar (världsrum)
	float detail = fbm(v_wpos * 30.0);

	vec3 col = mix(rock_grey, rock_warm, smoothstep(0.55, 0.75, band) * 0.5);
	col = mix(col, rock_dark, (1.0 - smoothstep(0.14, 0.55, m)) * 0.55);   // AO i sprickor
	col *= 0.86 + 0.26 * detail;                     // korn-variation
	float edge = smoothstep(0.68, 0.92, m);          // torra ljusa kanter/åskrön
	col += edge * 0.10;
	float flatness = smoothstep(0.55, 0.92, gn.y);   // uppåtvända ytor → lav
	col = mix(col, lichen, flatness * 0.35 * smoothstep(0.3, 0.7, detail));

	// blött bara i ett SMALT band vid/under vattenlinjen — resten är torr sten
	// (heltäckande våtglans fick klippan att läsa som blöt brun lera)
	float wet = smoothstep(waterline + 0.08, waterline - 0.05, v_wpos.y);
	col *= mix(1.0, 0.50, wet);
	ALBEDO = col;
	ROUGHNESS = clamp(mix(0.96, 0.35, wet) - 0.06 * detail, 0.25, 0.97);
	METALLIC = 0.0;
	// torr sten speglar i praktiken inget — spekulärt > 0 lät reflektionssondens
	// suddiga himmel lägga en vit slöja över hela klippan
	SPECULAR = mix(0.02, 0.50, wet);
}
