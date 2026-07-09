// sky.gdshader — mörk, stormig himmel med drivande moln (fbm). Molnen rör sig
// med uniformen `t` (spelets _elapsed), inte TIME, så inspelningen är
// deterministisk. Ljusare dis vid horisonten, mörk zenit, tunga moln högre upp.
shader_type sky;

uniform float t = 0.0;
uniform vec3 horizon : source_color = vec3(0.68, 0.73, 0.80);   // dis vid horisonten (blågrått)
uniform vec3 zenith : source_color = vec3(0.17, 0.35, 0.61);    // djupare blå dagsljushimmel
uniform vec3 cloud_dark : source_color = vec3(0.40, 0.43, 0.50);  // molnbas (mörkgrå, dramatik)
uniform vec3 cloud_light : source_color = vec3(0.97, 0.98, 0.99); // molntopp (vit)
uniform float flash = 0.0;   // höjs vid blixtnedslag (lyser upp molnen)

float hash(vec2 p) { return fract(sin(dot(p, vec2(41.3, 289.1))) * 43758.5453); }

float vnoise(vec2 p) {
	vec2 i = floor(p); vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	float a = hash(i), b = hash(i + vec2(1.0, 0.0));
	float c = hash(i + vec2(0.0, 1.0)), d = hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

float fbm(vec2 p) {
	float s = 0.0, a = 0.5;
	for (int i = 0; i < 5; i++) { s += a * vnoise(p); p *= 2.0; a *= 0.5; }
	return s;
}

void sky() {
	float el = clamp(EYEDIR.y, 0.0, 1.0);
	vec3 base = mix(horizon, zenith, pow(el, 0.7));   // djupare blått högre upp
	// projicera blickriktningen på ett molnplan
	vec2 uv = EYEDIR.xz / (EYEDIR.y + 0.28);
	float dens = fbm(uv * 1.2 + vec2(t * 0.02, t * 0.012));
	float detail = fbm(uv * 3.6 + 9.0);
	float field = dens * 0.7 + detail * 0.3;
	// molnbank vid horisonten (som referensen: stackade cumulus över kustlinjen)
	// — extra täthet när blicken är låg, i stället för att molnen tonas bort där
	field += smoothstep(0.22, 0.02, el) * 0.16;
	// pösiga cumulus-fläckar med blå luckor mellan; täcket når ända ned
	float cover = smoothstep(0.40, 0.62, field) * smoothstep(-0.02, 0.04, EYEDIR.y);
	// molnskuggning: täta partier (toppar) vita, tunnare (baser) grå
	float shade = smoothstep(0.30, 0.84, field);
	vec3 cloud = mix(cloud_dark, cloud_light, shade);
	cloud += flash * 0.8;                       // blixten lyser upp molnen
	COLOR = mix(base + flash * 0.25, cloud, cover);
	// under horisonten (syns bara bortom havsplanets kant): mörk sjödis i
	// stället för vitt sken, så havet ser ut att fortsätta till horisontlinjen
	COLOR = mix(COLOR, vec3(0.28, 0.38, 0.46), smoothstep(0.0, -0.05, EYEDIR.y));
}
