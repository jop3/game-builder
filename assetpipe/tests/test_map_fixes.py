"""Pillow/NumPy map post-processing fixes (spec 10.4, 13.3, App. B S15)."""
import numpy as np
from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.fixes import map_fixes
from assetpipe.fixes.apply import FixContext

C = Contracts.load()


class FakeContracts:
    """Stand-in exposing only the `.profile()` surface `shrink_textures` needs,
    so tests control the file-size cap without depending on the real profile
    numbers in profiles/web.json."""

    def __init__(self, profile: dict):
        self._profile = profile

    def profile(self, name):
        return self._profile


def make_ctx(iter_dir, request=None, contracts=None, config=None):
    return FixContext(iter_dir=iter_dir, request=request or {"seed": 1}, contracts=contracts or C,
                      config=config or {}, param_schema={})


# ---------- reexport_maps ----------

def test_reexport_strips_alpha_from_normal_and_orm_but_not_albedo(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    rgba = np.full((16, 16, 4), 200, dtype=np.uint8)
    rgba[..., 3] = 128  # partial alpha so "had_alpha" is unambiguous
    for name in ("albedo", "normal", "orm", "emissive"):
        Image.fromarray(rgba, mode="RGBA").save(maps_dir / f"{name}.png")

    ctx = make_ctx(tmp_path)
    result = map_fixes.reexport_maps(ctx, {})

    assert Image.open(maps_dir / "normal.png").mode == "RGB"
    assert Image.open(maps_dir / "orm.png").mode == "RGB"
    assert Image.open(maps_dir / "albedo.png").mode == "RGBA"
    assert Image.open(maps_dir / "emissive.png").mode == "RGBA"
    assert result["changed"]["normal"] == {"alpha_stripped": True}
    assert result["changed"]["orm"] == {"alpha_stripped": True}
    assert result["changed"]["albedo"] == {"alpha_stripped": False}


def test_reexport_skips_absent_maps(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8), mode="RGB").save(maps_dir / "albedo.png")

    ctx = make_ctx(tmp_path)
    result = map_fixes.reexport_maps(ctx, {})

    assert list(result["changed"]) == ["albedo"]


# ---------- repack_orm ----------

def test_repack_orm_packs_channel_order_from_separate_bakes(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    ao = np.full((8, 8), 30, dtype=np.uint8)
    rough = np.full((8, 8), 90, dtype=np.uint8)
    metal = np.full((8, 8), 60, dtype=np.uint8)   # < 128 -> should snap toward 0
    Image.fromarray(ao, mode="L").save(maps_dir / "ao.png")
    Image.fromarray(rough, mode="L").save(maps_dir / "roughness.png")
    Image.fromarray(metal, mode="L").save(maps_dir / "metallic.png")

    ctx = make_ctx(tmp_path)
    result = map_fixes.repack_orm(ctx, {})

    orm = np.asarray(Image.open(maps_dir / "orm.png").convert("RGB"))
    assert np.array_equal(orm[..., 0], ao)
    assert np.array_equal(orm[..., 1], rough)
    assert np.all(orm[..., 2] == 0)             # snapped fully toward 0
    assert result["changed"]["orm"]["packed_from_bakes"] is True
    assert result["changed"]["orm"]["metallic_snapped"] is True


def test_repack_orm_snaps_high_metallic_toward_one(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    orm_in = np.zeros((8, 8, 3), dtype=np.uint8)
    orm_in[..., 2] = 200   # >= 128 -> should snap toward 1 (255)
    Image.fromarray(orm_in, mode="RGB").save(maps_dir / "orm.png")

    ctx = make_ctx(tmp_path)
    result = map_fixes.repack_orm(ctx, {})

    orm = np.asarray(Image.open(maps_dir / "orm.png").convert("RGB"))
    assert np.all(orm[..., 2] == 255)
    assert result["changed"]["orm"]["packed_from_bakes"] is False


def test_repack_orm_blended_metal_override_skips_snap(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    orm_in = np.zeros((8, 8, 3), dtype=np.uint8)
    orm_in[..., 2] = 60
    Image.fromarray(orm_in, mode="RGB").save(maps_dir / "orm.png")

    ctx = make_ctx(tmp_path, request={"seed": 1, "material_overrides": {"blended_metal": True}})
    result = map_fixes.repack_orm(ctx, {})

    orm = np.asarray(Image.open(maps_dir / "orm.png").convert("RGB"))
    assert np.all(orm[..., 2] == 60)   # untouched
    assert result["changed"]["orm"]["metallic_snapped"] is False


def test_repack_orm_no_inputs_is_a_documented_noop(tmp_path):
    (tmp_path / "maps").mkdir()
    ctx = make_ctx(tmp_path)
    result = map_fixes.repack_orm(ctx, {})
    assert result["changed"] == {}


# ---------- redither ----------

def _build_f32(base_dir, seed_marker):
    maps_dir = base_dir / "maps"
    maps_dir.mkdir(parents=True)
    arr = np.linspace(0.0, 1.0, 16 * 16 * 3).reshape(16, 16, 3)
    np.save(maps_dir / "albedo_f32.npy", arr)
    return base_dir


def test_redither_from_f32_intermediate_is_deterministic_given_seed(tmp_path):
    d1, d2 = _build_f32(tmp_path / "a", 0), _build_f32(tmp_path / "b", 0)
    ctx1, ctx2 = make_ctx(d1, request={"seed": 42}), make_ctx(d2, request={"seed": 42})

    r1 = map_fixes.redither(ctx1, {})
    r2 = map_fixes.redither(ctx2, {})

    out1 = np.asarray(Image.open(d1 / "maps" / "albedo.png"))
    out2 = np.asarray(Image.open(d2 / "maps" / "albedo.png"))
    assert np.array_equal(out1, out2)
    assert r1["changed"]["albedo"] == {"source": "f32_intermediate"}
    assert r2 == r1


def test_redither_different_seeds_diverge(tmp_path):
    d1, d2 = _build_f32(tmp_path / "a", 0), _build_f32(tmp_path / "b", 0)
    map_fixes.redither(make_ctx(d1, request={"seed": 1}), {})
    map_fixes.redither(make_ctx(d2, request={"seed": 2}), {})

    out1 = np.asarray(Image.open(d1 / "maps" / "albedo.png"))
    out2 = np.asarray(Image.open(d2 / "maps" / "albedo.png"))
    assert not np.array_equal(out1, out2)


def _build_8bit(base_dir):
    maps_dir = base_dir / "maps"
    maps_dir.mkdir(parents=True)
    arr = np.full((16, 16, 3), 128, dtype=np.uint8)
    Image.fromarray(arr, mode="RGB").save(maps_dir / "albedo.png")
    return base_dir


def test_redither_falls_back_to_mild_dither_on_existing_8bit_map(tmp_path):
    d1, d2 = _build_8bit(tmp_path / "a"), _build_8bit(tmp_path / "b")
    r1 = map_fixes.redither(make_ctx(d1, request={"seed": 5}), {})
    map_fixes.redither(make_ctx(d2, request={"seed": 5}), {})

    out1 = np.asarray(Image.open(d1 / "maps" / "albedo.png")).astype(int)
    out2 = np.asarray(Image.open(d2 / "maps" / "albedo.png")).astype(int)
    assert np.array_equal(out1, out2)                 # deterministic given seed
    assert np.abs(out1 - 128).max() <= 1               # amplitude ~1/255 (mild)
    assert r1["changed"]["albedo"] == {"source": "8bit_png"}


# ---------- shrink_textures ----------

def test_shrink_textures_noop_under_cap(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    arr = np.zeros((256, 256, 3), dtype=np.uint8)
    Image.fromarray(arr, mode="RGB").save(maps_dir / "albedo.png")

    fake = FakeContracts({"file_bytes": {"prop_small": 10_000_000}})
    ctx = make_ctx(tmp_path, request={"seed": 1, "platform_profile": "web", "category": "prop_small"},
                  contracts=fake)
    result = map_fixes.shrink_textures(ctx, {})

    assert result["before"] == result["after"]
    assert Image.open(maps_dir / "albedo.png").size == (256, 256)


def test_shrink_textures_halves_until_cap_or_floor(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, size=(256, 256, 4), dtype=np.uint8)  # incompressible noise
    Image.fromarray(arr, mode="RGBA").save(maps_dir / "albedo.png")

    fake = FakeContracts({"file_bytes": {"prop_small": 1}})   # impossible cap -> shrink to floor
    ctx = make_ctx(tmp_path, request={"seed": 1, "platform_profile": "web", "category": "prop_small"},
                  contracts=fake)
    result = map_fixes.shrink_textures(ctx, {})

    assert Image.open(maps_dir / "albedo.png").size == (64, 64)   # floors, never below
    assert result["before"]["albedo"] > result["after"]["albedo"]
    assert result["cap"] == 1


def test_shrink_textures_noop_when_category_has_no_cap(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8), mode="RGB").save(maps_dir / "albedo.png")

    fake = FakeContracts({"file_bytes": {}})   # category absent -> no cap
    ctx = make_ctx(tmp_path, request={"seed": 1, "platform_profile": "web", "category": "skybox"},
                  contracts=fake)
    result = map_fixes.shrink_textures(ctx, {})

    assert result["cap"] is None
    assert Image.open(maps_dir / "albedo.png").size == (32, 32)


def test_shrink_textures_no_maps_present(tmp_path):
    (tmp_path / "maps").mkdir()
    fake = FakeContracts({"file_bytes": {"prop_small": 1}})
    ctx = make_ctx(tmp_path, request={"seed": 1, "platform_profile": "web", "category": "prop_small"},
                  contracts=fake)
    result = map_fixes.shrink_textures(ctx, {})
    assert result == {"before": {}, "after": {}, "cap": 1}


def _noise_map(maps_dir, name, px, seed=0):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(px, px, 3), dtype=np.uint8)  # incompressible
    Image.fromarray(arr, mode="RGB").save(maps_dir / f"{name}.png")
    return (maps_dir / f"{name}.png").stat().st_size


def _shrink_ctx(tmp_path, cap):
    fake = FakeContracts({"file_bytes": {"prop_small": cap}})
    return make_ctx(tmp_path, request={"seed": 1, "platform_profile": "web",
                                       "category": "prop_small"}, contracts=fake)


def test_shrink_textures_pays_with_normal_and_orm_before_albedo(tmp_path):
    """COLOR_WAVE item 2: the painted albedo detail is what the texture wave
    paid for -- normal+orm shrink first, and when that alone satisfies the
    cap the albedo (and emissive) are untouched."""
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    albedo_size = _noise_map(maps_dir, "albedo", 512, seed=1)
    emissive_size = _noise_map(maps_dir, "emissive", 256, seed=2)
    _noise_map(maps_dir, "normal", 512, seed=3)
    _noise_map(maps_dir, "orm", 512, seed=4)

    # Cap chosen so halving normal+orm to 256 is enough, albedo alone is not.
    cap = albedo_size + emissive_size + 2 * (albedo_size // 3)
    map_fixes.shrink_textures(_shrink_ctx(tmp_path, cap), {})

    assert Image.open(maps_dir / "albedo.png").size == (512, 512)     # untouched
    assert Image.open(maps_dir / "emissive.png").size == (256, 256)   # untouched
    assert Image.open(maps_dir / "normal.png").size[0] < 512
    assert Image.open(maps_dir / "orm.png").size[0] < 512


def test_shrink_textures_touches_emissive_last(tmp_path):
    """Emissive carries the window-glow read: albedo must shrink before the
    emissive is ever touched."""
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    _noise_map(maps_dir, "albedo", 512, seed=1)
    emissive_size = _noise_map(maps_dir, "emissive", 256, seed=2)
    normal_256 = _noise_map(maps_dir, "normal", 256, seed=3)

    # normal already at the soft floor; cap forces albedo down to the soft
    # floor but is satisfied before the emissive would have to pay.
    cap = emissive_size + normal_256 + 2 * emissive_size
    map_fixes.shrink_textures(_shrink_ctx(tmp_path, cap), {})

    assert Image.open(maps_dir / "emissive.png").size == (256, 256)   # untouched
    assert Image.open(maps_dir / "normal.png").size == (256, 256)     # soft floor
    assert Image.open(maps_dir / "albedo.png").size[0] <= 256         # paid


def test_shrink_textures_hard_pass_below_soft_floor_still_converges(tmp_path):
    """When every map is at the 256 soft floor and the cap is still busted,
    the hard pass goes below it (same tier order) instead of looping forever."""
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    _noise_map(maps_dir, "albedo", 256, seed=1)
    _noise_map(maps_dir, "normal", 256, seed=2)

    result = map_fixes.shrink_textures(_shrink_ctx(tmp_path, 1), {})  # impossible cap

    assert Image.open(maps_dir / "albedo.png").size == (64, 64)
    assert Image.open(maps_dir / "normal.png").size == (64, 64)
    assert result["cap"] == 1
