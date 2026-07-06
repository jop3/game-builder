"""Image analytics on synthetic fixtures — every check proven to catch its
seeded defect AND to pass a clean input (spec 21.1 discipline, in miniature)."""
import numpy as np

from assetpipe.validation import image_checks as ic

RNG = np.random.default_rng(0)


def noisy(h=64, w=64, base=0.4, amp=0.2):
    return np.clip(base + amp * RNG.standard_normal((h, w, 3)), 0, 1)


def test_not_empty():
    assert ic.check_not_empty(noisy())["verdict"] == "pass"
    assert ic.check_not_empty(np.zeros((8, 8, 3)))["verdict"] == "fail"
    assert ic.check_not_empty(np.ones((8, 8, 3)))["verdict"] == "fail"
    assert ic.check_not_empty(np.full((8, 8, 3), 0.5))["verdict"] == "fail"  # flat


def test_backface_fraction():
    img = np.zeros((100, 100, 3)); img[..., 2] = 1.0        # all normal-blue
    assert ic.check_backface_fraction(img)["verdict"] == "pass"
    img[:2, :10] = [1.0, 0.0, 0.0]                           # 20/10000 = 0.2% red
    r = ic.check_backface_fraction(img)
    assert r["verdict"] == "fail" and r["measured"] == 0.002


def test_silhouette_area():
    img = np.zeros((100, 100, 3)); img[20:80, 20:80] = 1.0   # 36% white
    assert ic.check_silhouette_area(img)["verdict"] == "pass"
    assert ic.check_silhouette_area(np.zeros((10, 10, 3)))["verdict"] == "fail"
    assert ic.check_silhouette_area(np.ones((10, 10, 3)))["verdict"] == "fail"


def test_clipping_is_warn_severity():
    img = noisy(); r = ic.check_clipping(img)
    assert r["verdict"] == "pass" and r["severity"] == "warn"
    img[:20] = 1.0                                           # 31% clipped
    assert ic.check_clipping(img)["verdict"] == "fail"


def _tileable(h=64, w=64):
    """Genuinely periodic pattern: sum of integer-frequency sinusoids."""
    y, x = np.mgrid[0:h, 0:w]
    v = (np.sin(2 * np.pi * 3 * x / w) + np.cos(2 * np.pi * 2 * y / h)
         + np.sin(2 * np.pi * (2 * x / w + 5 * y / h))) / 6 + 0.5
    return np.repeat(v[..., None], 3, axis=-1)


def test_edge_wrap_passes_periodic_fails_broken():
    tile = _tileable()
    assert ic.check_edge_wrap(tile, axis=0)["verdict"] == "pass"
    assert ic.check_edge_wrap(tile, axis=1)["verdict"] == "pass"
    # high-frequency-but-periodic must still pass: the adjacent-texel steps are
    # ~0.12 (would fail any absolute 2/255 edge test) yet the tiling is perfect
    y, x = np.mgrid[0:32, 0:32]
    hf = 0.15 * np.sin(2 * np.pi * 13 * x / 32)
    hf_tile = np.clip(_tileable(32, 32) + np.repeat(hf[..., None], 3, axis=-1), 0, 1)
    assert ic.check_edge_wrap(hf_tile, axis=1)["verdict"] == "pass"
    broken = tile.copy(); broken[:, -4:] += 0.25             # brightened right band
    r = ic.check_edge_wrap(broken, axis=1)
    assert r["verdict"] == "fail" and r["measured"] > 1.5
    # top/bottom wrap unaffected by a right-edge break
    assert ic.check_edge_wrap(broken, axis=0)["verdict"] == "pass"


def test_rolled_seam_catches_forged_edges():
    assert ic.check_rolled_seam(_tileable())["verdict"] == "pass"
    # Classic naive "make seamless": outer texel column forged to match the
    # opposite edge, discontinuity pushed one texel inside the border.
    img = _tileable().copy()
    img[:, : img.shape[1] // 2] = np.clip(img[:, : img.shape[1] // 2] + 0.4, 0, 1)
    img[:, 0] = img[:, -1]                                   # forge matching edges
    assert ic.check_edge_wrap(img, axis=1)["verdict"] == "pass"   # S19a is fooled...
    assert ic.check_rolled_seam(img)["verdict"] == "fail"         # ...S19b is not


def test_normal_map_stats():
    flat = np.zeros((32, 32, 3)); flat[...] = [0.5, 0.5, 1.0]   # perfect flat normal
    assert ic.check_normal_map_stats(flat)["verdict"] == "pass"
    srgbish = flat.copy(); srgbish[..., 2] = 0.6; srgbish[..., 0] = 0.7
    assert ic.check_normal_map_stats(srgbish)["verdict"] == "fail"
    swapped = flat[..., [2, 1, 0]]                              # blue<->red swap
    assert ic.check_normal_map_stats(swapped)["verdict"] == "fail"


def test_albedo_stats_with_flat_color_exemption():
    assert ic.check_albedo_stats(noisy())["verdict"] == "pass"
    black = np.full((16, 16, 3), 0.005)
    assert ic.check_albedo_stats(black)["verdict"] == "fail"
    flat = np.full((16, 16, 3), 0.5)
    assert ic.check_albedo_stats(flat)["verdict"] == "fail"
    assert ic.check_albedo_stats(flat, flat_color_ok=True)["verdict"] == "pass"
