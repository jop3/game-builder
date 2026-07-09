// sky.gdshader — mörk, stormig himmel med drivande moln (fbm). Molnen rör sig
// med uniformen `t` (spelets _elapsed), inte TIME, så inspelningen är
// deterministisk. Ljusare dis vid horisonten, mörk zenit, tunga moln högre upp.
shader_type sky;

uniform float t = 0.0;
uniform vec3 horizon : source_color = vec3(0.74, 0.78, 0.82);   // ljus dis vid horisonten
uniform vec3 zenith : source_color = vec3(0.28, 0.42, 0.60);    // blå dagsljushimmel
uniform vec3 cloud_dark : source_color = vec3(0.42, 0.45, 0.50);
uniform vec3 cloud_light : source_color = vec3(0.92, 0.94, 0.96);
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
	vec3 base = mix(horizon, zenith, el);
	// projicera blickriktningen på ett molnplan
	vec2 uv = EYEDIR.xz / (EYEDIR.y + 0.35);
	float mask = fbm(uv * 1.5 + vec2(t * 0.02, t * 0.012));
	mask = smoothstep(0.42, 0.82, mask) * smoothstep(0.02, 0.20, EYEDIR.y);   // mer molntäcke
	vec3 cloud = mix(cloud_dark, cloud_light, fbm(uv * 3.0 + 5.0));
	cloud += flash * 0.8;                       // blixten lyser upp molnen
	COLOR = mix(base + flash * 0.25, cloud, mask);
}
