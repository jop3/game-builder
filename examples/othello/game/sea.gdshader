// sea.gdshader — hav med riktiga GERSTNER-vågor (trochoidala): vertexar rör sig i
// cirkelbanor (horisontellt + vertikalt) → vassa toppar, rundade dalar, korrekt
// analytisk normal. Ovanpå: Fresnel-himmelsreflektion mot grazing-vinkel, skum
// där vågtopparna viker sig (Jacobian-fold) förstärkt av vindsträckta stråk,
// ljusgenomsläpp i topparna (fejkad subsurface) och fin krusning via rullande
// gradientbrus per fragment. Allt animeras av uniformen `t` (spelets _elapsed),
// ALDRIG TIME — så inspelningen förblir deterministisk och FPS-låst.
//
// Ref: GPU Gems 1, kap. 1 (Finding Next Gen — effektiv vattenrendering / Gerstner).
shader_type spatial;
render_mode cull_back, specular_schlick_ggx;

const float TWO_PI = 6.2831853;   // eget namn (TAU är inbyggt och kan ej skuggas)
const int NW = 4;
// riktning, våglängd, amplitud, branthet(Q), fart — kortare/brantare chop än
// tidigare svall: referensfotot är en blåsig kustsjö med vita gäss överallt,
// och foldsumman måste nå över skumtröskeln för att gässen alls ska synas.
const vec2  W_DIR[4] = {vec2(1.0, 0.35), vec2(-0.6, 1.0), vec2(0.9, -0.4), vec2(0.2, 1.0)};
const float W_LEN[4] = {2.4, 1.25, 0.70, 0.45};
const float W_AMP[4] = {0.042, 0.028, 0.016, 0.009};
const float W_Q[4]   = {0.50, 0.62, 0.72, 0.80};
const float W_SPD[4] = {0.9, 1.3, 1.8, 2.3};

uniform float t = 0.0;
uniform float amp = 1.0;             // global amplitud-förstärkning
uniform float island_r = 0.62;
uniform vec3 deep : source_color = vec3(0.008, 0.055, 0.085);   // djupt blågrönt hav
uniform vec3 shallow : source_color = vec3(0.035, 0.20, 0.24);
uniform vec3 crest : source_color = vec3(0.10, 0.36, 0.36);     // ljusgenomsläpp i toppar
uniform vec3 shore_water : source_color = vec3(0.14, 0.46, 0.45);   // ljus turkos, grunt vatten
uniform vec3 sky_tint : source_color = vec3(0.58, 0.68, 0.80);   // reflekterad himmel
uniform vec3 foam_col : source_color = vec3(0.90, 0.93, 0.95);
uniform float shore_reach = 0.55;   // hur långt ut det grunda ljusnar

varying vec3 v_wpos;
varying vec3 v_normal;
varying float v_fold;
varying float v_crest;

float hash(vec2 p) { return fract(sin(dot(p, vec2(41.3, 289.1))) * 43758.5453); }
float vnoise(vec2 p) {
	vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
	float a = hash(i), b = hash(i + vec2(1.0, 0.0));
	float c = hash(i + vec2(0.0, 1.0)), d = hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float fbm2(vec2 p) {
	float s = 0.0, a = 0.5;
	for (int i = 0; i < 3; i++) { s += a * vnoise(p); p *= 2.05; a *= 0.5; }
	return s;
}

void vertex() {
	vec2 pos = VERTEX.xz;
	vec3 disp = vec3(0.0);
	vec3 nrm = vec3(0.0, 1.0, 0.0);
	float fold = 0.0;
	for (int i = 0; i < NW; i++) {
		vec2 d = normalize(W_DIR[i]);
		float k = TWO_PI / W_LEN[i];
		float a = W_AMP[i] * amp;
		float arg = k * dot(d, pos) + t * W_SPD[i];
		float c = cos(arg), s = sin(arg);
		disp.x += W_Q[i] * a * d.x * c;      // horisontell cirkelrörelse
		disp.z += W_Q[i] * a * d.y * c;
		disp.y += a * s;                     // vertikal
		nrm.x -= d.x * k * a * c;            // analytisk Gerstner-normal
		nrm.z -= d.y * k * a * c;
		nrm.y -= W_Q[i] * k * a * s;
		fold  += W_Q[i] * k * a * s;         // Jacobian → skum där toppar viker sig
	}
	VERTEX += disp;
	v_wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	v_normal = normalize(nrm);
	v_fold = clamp(fold, 0.0, 1.0);
	v_crest = clamp(disp.y / (0.09 * amp), -1.0, 1.0);   // normaliserad vågtoppshöjd
}

void fragment() {
	// fin krusning: perturbera normalen med gradienten av rullande brus
	vec2 rp = v_wpos.xz;
	float e = 0.02;
	float n0 = fbm2(rp * 9.0 + vec2(t * 0.30, -t * 0.20));
	float nx = fbm2((rp + vec2(e, 0.0)) * 9.0 + vec2(t * 0.30, -t * 0.20));
	float nz = fbm2((rp + vec2(0.0, e)) * 9.0 + vec2(t * 0.30, -t * 0.20));
	vec3 rn = v_normal;
	rn.x += (nx - n0) / e * 0.020;
	rn.z += (nz - n0) / e * 0.020;
	rn = normalize(rn);
	NORMAL = normalize((VIEW_MATRIX * vec4(rn, 0.0)).xyz);

	// Fresnel: mer himmelsreflektion mot grazing-vinkel — men dämpad, så havet
	// behåller sin djupa färg mot horisonten (referensen är MÖRK långt ut)
	vec3 V = normalize(CAMERA_POSITION_WORLD - v_wpos);
	float fres = 0.02 + 0.98 * pow(1.0 - clamp(dot(rn, V), 0.0, 1.0), 5.0);

	// grundfärg: mörka vågdalar → ljusare toppar; topparna släpper igenom ljus
	// (fejkad subsurface — det blågröna "glaset" i en brytande våg)
	float dd = length(v_wpos.xz);
	float facing = clamp(rn.y, 0.0, 1.0);
	vec3 water = mix(deep, shallow, facing * 0.40 + v_fold * 0.30);
	water = mix(water, crest, clamp(v_crest, 0.0, 1.0) * 0.45);
	// GRUNT VATTEN: närmare ön ljusnar det mot turkos (djup-fejk via avstånd),
	// bruten kant så det inte blir en jämn ring — hålls som ett SMALT band så
	// öppna havet behåller djupblått
	float shallowness = smoothstep(island_r + shore_reach, island_r + 0.04, dd)
	                  * (0.7 + 0.3 * fbm2(rp * 5.0));
	water = mix(water, shore_water, shallowness * 0.55);
	water = mix(water, sky_tint, fres * 0.35);

	// VITA GÄSS: skum där topparna viker sig, brutet av vindsträckta stråk
	// (brus starkt anisotropiskt längs huvudvindens riktning) + strandkrage
	vec2 wind = normalize(vec2(1.0, 0.35));
	vec2 perp = vec2(-wind.y, wind.x);
	float streak = fbm2(vec2(dot(rp, wind) * 1.6 - t * 0.25, dot(rp, perp) * 6.5));
	float capfoam = smoothstep(0.18, 0.34, v_fold) * smoothstep(0.40, 0.70, streak);
	capfoam += smoothstep(0.26, 0.44, v_fold) * 0.7;   // de brantaste topparna skummar alltid
	capfoam *= 0.55 + 0.45 * fbm2(rp * 14.0 - vec2(t * 0.2, 0.0));   // riven skumkant
	// dröjande skumstråk: spetsig brusväv utdragen i vindriktningen som ligger
	// kvar efter gässen (referensens vita filament över hela ytan)
	float lace = smoothstep(0.60, 0.80, fbm2(vec2(dot(rp, wind) * 2.2 - t * 0.18, dot(rp, perp) * 8.0) + 3.7));
	capfoam = max(capfoam, lace * smoothstep(0.10, 0.28, v_fold) * 0.55);
	// bränningskrage: bredare, rivigare och PULSERANDE med vågfolden så bruset
	// mot klippan ser ut att slå och dra sig tillbaka (referensens vita bränningar)
	float shore = smoothstep(island_r + 0.34, island_r - 0.02, dd)
	            * smoothstep(island_r - 0.22, island_r - 0.04, dd)
	            * (0.35 + 0.65 * fbm2(rp * 12.0 + t * 0.6))
	            * (0.55 + 0.45 * smoothstep(0.10, 0.35, v_fold));
	float foam = clamp(max(capfoam, shore), 0.0, 1.0);

	ALBEDO = mix(water, foam_col, foam);
	ROUGHNESS = mix(0.06, 0.75, foam);    // blankt vatten (sol-glitter via nyckelljuset), matt skum
	METALLIC = 0.0;
	SPECULAR = 0.55;
}
