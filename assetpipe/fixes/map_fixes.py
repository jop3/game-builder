"""Pillow/NumPy texture map post-processing fixes (spec 10.4, 13.3, App. B S15).

Each function is a table-fix `implementation` resolved and invoked by
`assetpipe.fixes.apply.apply_fix_plan` as `fn(ctx, action) -> dict`, operating
on PNGs under `ctx.iter_dir / "maps"`. Map filenames follow the pipeline
convention: `albedo.png`, `normal.png`, `orm.png`, `emissive.png`.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

MAP_NAMES = ("albedo", "normal", "orm", "emissive")
# spec S15: normal/ORM maps must not carry a stray alpha channel (it can be
# misread as an extra data channel by exporters); albedo/emissive are allowed
# to keep one (e.g. cutout materials), so we leave those alone here.
ALPHA_STRIP = {"normal", "orm"}


def _maps_dir(ctx):
    return ctx.iter_dir / "maps"


def reexport_maps(ctx, action: dict) -> dict:
    """`reexport_maps` / MISSING_TEXTURE, BANDING (re-export step): re-save
    every present map as an 8-bit PNG, stripping any alpha channel from
    normal.png / orm.png (spec S15). Albedo/emissive are re-encoded as-is
    (RGBA preserved if present) -- this fix's job is the normal/orm alpha
    strip, not a format change for the color maps.
    """
    maps_dir = _maps_dir(ctx)
    changed = {}
    for name in MAP_NAMES:
        path = maps_dir / f"{name}.png"
        if not path.exists():
            continue
        img = Image.open(path)
        had_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
        if name in ALPHA_STRIP:
            if img.mode != "RGB":
                img = img.convert("RGB")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if had_alpha else "RGB")
        arr = np.asarray(img).astype(np.uint8)
        Image.fromarray(arr).save(path)
        changed[name] = {"alpha_stripped": name in ALPHA_STRIP and had_alpha}
    return {"changed": changed}


def _snap_tolerance(ctx) -> float | None:
    """Look for a metallic-snap tolerance in config['validation']; spec 16.2
    says "snap toward {0,1}" without naming a key, so any config key that
    mentions both "metallic" and "snap"/"tolerance" is honored if present,
    else the fix snaps fully (tolerance 0 == hard round to 0/1)."""
    validation_cfg = (ctx.config or {}).get("validation", {})
    for key, value in validation_cfg.items():
        kl = key.lower()
        if "metallic" in kl and ("snap" in kl or "tolerance" in kl):
            return float(value)
    return None


def repack_orm(ctx, action: dict) -> dict:
    """`repack_orm` / channel-pack fix: pack `ao.png`/`roughness.png`/
    `metallic.png` bakes into `orm.png` (R=AO, G=roughness, B=metallic) if
    present; otherwise operate on the existing `orm.png`. Snaps the metallic
    channel toward {0, 1} unless `request.material_overrides.blended_metal`
    is true, in which case the metallic channel is left untouched.
    """
    maps_dir = _maps_dir(ctx)
    ao_p, rough_p, metal_p = (maps_dir / n for n in
                              ("ao.png", "roughness.png", "metallic.png"))
    orm_p = maps_dir / "orm.png"

    packed_from_bakes = ao_p.exists() and rough_p.exists() and metal_p.exists()
    if packed_from_bakes:
        ao = np.asarray(Image.open(ao_p).convert("L"))
        rough = np.asarray(Image.open(rough_p).convert("L"))
        metal = np.asarray(Image.open(metal_p).convert("L"))
        orm = np.stack([ao, rough, metal], axis=-1).astype(np.uint8)
    elif orm_p.exists():
        orm = np.asarray(Image.open(orm_p).convert("RGB")).astype(np.uint8)
    else:
        return {"changed": {}, "note": "no orm inputs present"}

    blended = bool((ctx.request or {}).get("material_overrides", {}).get("blended_metal"))
    snapped = False
    if not blended:
        tol = _snap_tolerance(ctx)
        strength = 1.0 if tol is None else max(0.0, min(1.0, 1.0 - tol))
        metallic = orm[..., 2].astype(np.float64) / 255.0
        target = np.where(metallic < 0.5, 0.0, 1.0)
        metallic = metallic + strength * (target - metallic)
        orm = orm.copy()
        orm[..., 2] = np.clip(np.round(metallic * 255.0), 0, 255).astype(np.uint8)
        snapped = True

    Image.fromarray(orm, mode="RGB").save(orm_p)
    return {"changed": {"orm": {"packed_from_bakes": packed_from_bakes,
                                "metallic_snapped": snapped}}}


def redither(ctx, action: dict) -> dict:
    """`redither` / BANDING: requantize from a 16-bit-float intermediate
    (`<map>_f32.npy`, values in [0, 1]) to 8-bit with seeded uniform (TPDF-ish)
    dither if present; else add a mild seeded dither (+-0.5 LSB, i.e.
    amplitude 1/255 in normalized space) to the existing 8-bit PNG. Seeded
    from `ctx.request["seed"]` via `numpy.random.default_rng` so the result is
    deterministic given the seed and inputs.
    """
    maps_dir = _maps_dir(ctx)
    seed = (ctx.request or {}).get("seed", 0)
    rng = np.random.default_rng(seed)
    changed = {}
    for name in MAP_NAMES:
        f32_path = maps_dir / f"{name}_f32.npy"
        png_path = maps_dir / f"{name}.png"
        if f32_path.exists():
            arr = np.load(f32_path).astype(np.float64)
            dither = rng.uniform(-0.5, 0.5, size=arr.shape)
            quant = np.clip(np.round(arr * 255.0 + dither), 0, 255).astype(np.uint8)
            mode = "RGBA" if quant.ndim == 3 and quant.shape[-1] == 4 else (
                "RGB" if quant.ndim == 3 else "L")
            Image.fromarray(quant, mode=mode).save(png_path)
            changed[name] = {"source": "f32_intermediate"}
        elif png_path.exists():
            img = Image.open(png_path)
            mode = img.mode if img.mode in ("L", "RGB", "RGBA") else "RGB"
            arr = np.asarray(img.convert(mode)).astype(np.float64)
            dither = rng.uniform(-0.5, 0.5, size=arr.shape)
            out = np.clip(np.round(arr + dither), 0, 255).astype(np.uint8)
            Image.fromarray(out, mode=mode).save(png_path)
            changed[name] = {"source": "8bit_png"}
    return {"changed": changed}


# shrink_textures priority tiers (docs/COLOR_WAVE.md item 2): the painted
# per-plank/per-tile albedo detail is what the texture wave paid for, and the
# emissive carries the whole window-glow read (it is already 512-capped on
# web) -- so pay with normal+orm first (their detail is least visible at this
# art style), then albedo, emissive last. Lower tier number shrinks first.
SHRINK_TIERS = {"normal": 0, "orm": 0, "albedo": 1, "emissive": 2}
# Above this edge length a map is fair game for its tier; below it the fix
# only returns to the map after EVERY map is at the soft floor (the hard pass
# exists so the loop still converges when e.g. the albedo alone busts the cap).
SHRINK_SOFT_FLOOR_PX = 256
SHRINK_HARD_FLOOR_PX = 64


def shrink_textures(ctx, action: dict) -> dict:
    """`shrink_textures` / OVER_BUDGET (file size): while the total bytes of
    `maps/` exceed the profile's file-size cap for the request's category,
    halve one map at a time (LANCZOS), priority-aware (COLOR_WAVE item 2):

    1. normal + orm first (largest first within the tier), re-measuring
       after every halving, down to a 256 px soft floor;
    2. then albedo, then emissive, likewise down to the soft floor;
    3. only if EVERY map sits at the soft floor and the total still busts
       the cap, a hard pass repeats the same tier order down to 64 px --
       the fix must terminate even when the albedo alone busts the cap.

    Never shrinks below 64 px on either axis. If the category has no
    file-size cap in the profile (e.g. skybox/tiling sets), this is a no-op.
    Shrinking only ever lowers resolutions, so S14 per-map budget checks
    can't newly fail.
    """
    maps_dir = _maps_dir(ctx)
    profile = ctx.contracts.profile(ctx.request["platform_profile"])
    cap = profile.get("file_bytes", {}).get(ctx.request.get("category"))

    paths = {name: maps_dir / f"{name}.png" for name in MAP_NAMES
             if (maps_dir / f"{name}.png").exists()}
    sizes_before = {name: p.stat().st_size for name, p in paths.items()}

    if cap is None or not paths:
        return {"before": sizes_before, "after": dict(sizes_before), "cap": cap}

    def total_bytes() -> int:
        return sum(p.stat().st_size for p in paths.values())

    def next_victim(floor: int):
        """Shrinkable map with the lowest tier, largest file first within
        the tier; None when every map is at/below ``floor``."""
        shrinkable = [(name, p) for name, p in paths.items()
                      if min(Image.open(p).size) > floor]
        if not shrinkable:
            return None
        return min(shrinkable,
                   key=lambda kv: (SHRINK_TIERS.get(kv[0], 99),
                                   -kv[1].stat().st_size))

    guard = 0
    while total_bytes() > cap and guard < 64:
        guard += 1
        victim = next_victim(SHRINK_SOFT_FLOOR_PX) or next_victim(SHRINK_HARD_FLOOR_PX)
        if victim is None:
            break
        name, p = victim
        img = Image.open(p)
        w, h = img.size
        new_size = (max(SHRINK_HARD_FLOOR_PX, w // 2),
                    max(SHRINK_HARD_FLOOR_PX, h // 2))
        img.resize(new_size, Image.LANCZOS).save(p)

    sizes_after = {name: p.stat().st_size for name, p in paths.items()}
    return {"before": sizes_before, "after": sizes_after, "cap": cap}
