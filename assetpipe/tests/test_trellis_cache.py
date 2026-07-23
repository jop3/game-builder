"""Content-addressed neural cache: key derivation, resolution, and the
store/resolve round-trip (docs/NEURAL_BACKEND.md). Pure Python — no bpy, no
network, no GPU."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from assetpipe.neural import trellis_cache as tc


def _write_image(path: Path, data: bytes = b"fake-png-bytes") -> Path:
    path.write_bytes(data)
    return path


# ---------- image_digest ----------

def test_image_digest_is_sha256_of_bytes(tmp_path):
    img = _write_image(tmp_path / "ref.png", b"hello world")
    assert tc.image_digest(img) == hashlib.sha256(b"hello world").hexdigest()


def test_image_digest_streams_large_files(tmp_path):
    data = b"x" * (tc._READ_CHUNK * 2 + 7)  # spans multiple read chunks
    img = _write_image(tmp_path / "big.png", data)
    assert tc.image_digest(img) == hashlib.sha256(data).hexdigest()


def test_image_digest_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        tc.image_digest(tmp_path / "nope.png")


# ---------- cache_key: content-addressing ----------

def test_cache_key_is_stable_for_same_inputs(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    k1 = tc.cache_key(img, "trellis2-fp8", 0)
    k2 = tc.cache_key(img, "trellis2-fp8", 0)
    assert k1 == k2
    assert k1 == f"{tc.image_digest(img)}__trellis2-fp8__seed0"


def test_same_bytes_different_path_collide(tmp_path):
    a = _write_image(tmp_path / "a.png", b"identical")
    (tmp_path / "sub").mkdir()
    b = _write_image(tmp_path / "sub" / "b.png", b"identical")
    # Keyed on content, not filename: two copies resolve to one artifact.
    assert tc.cache_key(a, "m", 1) == tc.cache_key(b, "m", 1)


def test_one_changed_byte_changes_key(tmp_path):
    a = _write_image(tmp_path / "a.png", b"content-A")
    b = _write_image(tmp_path / "b.png", b"content-B")
    assert tc.cache_key(a, "m", 1) != tc.cache_key(b, "m", 1)


def test_model_version_and_seed_participate_in_key(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    base = tc.cache_key(img, "m1", 0)
    assert tc.cache_key(img, "m2", 0) != base   # model_version matters
    assert tc.cache_key(img, "m1", 1) != base   # seed matters


# ---------- validation ----------

@pytest.mark.parametrize("bad", ["../evil", "a/b", "with space", "", ".hidden", "-lead"])
def test_cache_key_rejects_unsafe_model_version(tmp_path, bad):
    img = _write_image(tmp_path / "ref.png")
    with pytest.raises(ValueError, match="model_version"):
        tc.cache_key(img, bad, 0)


@pytest.mark.parametrize("good", ["trellis2-fp8", "v1.0.3", "model_x", "a+b", "T2"])
def test_cache_key_accepts_safe_model_versions(tmp_path, good):
    img = _write_image(tmp_path / "ref.png")
    assert tc.cache_key(img, good, 0).endswith(f"__{good}__seed0")


@pytest.mark.parametrize("bad", [-1, 1.0, "0", None, True, False])
def test_cache_key_rejects_bad_seed(tmp_path, bad):
    img = _write_image(tmp_path / "ref.png")
    with pytest.raises(ValueError, match="seed"):
        tc.cache_key(img, "m", bad)


# ---------- artifact_path ----------

def test_artifact_path_places_glb_under_cache_root(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    root = tmp_path / "cache"
    p = tc.artifact_path(root, img, "m", 2)
    assert p.parent == root
    assert p.suffix == ".glb"
    assert p.stem == tc.cache_key(img, "m", 2)


def test_artifact_path_does_not_require_artifact_to_exist(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    # Pure path arithmetic: returns a path even though nothing is on disk.
    p = tc.artifact_path(tmp_path / "cache", img, "m", 0)
    assert not p.exists()


# ---------- resolve_or_fail ----------

def test_resolve_or_fail_miss_raises_trelliscachemiss(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    with pytest.raises(tc.TrellisCacheMiss) as exc:
        tc.resolve_or_fail(tmp_path / "cache", img, "trellis2-fp8", 0)
    # Message is actionable: names the model + seed to regenerate with.
    assert "trellis2-fp8" in str(exc.value)


def test_resolve_or_fail_hit_returns_path(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    root = tmp_path / "cache"
    expected = tc.artifact_path(root, img, "m", 0)
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"glb")
    assert tc.resolve_or_fail(root, img, "m", 0) == expected


def test_resolve_or_fail_ignores_wrong_seed_artifact(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    root = tmp_path / "cache"
    tc.store(root, img, "m", 0, _write_image(tmp_path / "out.glb", b"glb"))
    # Seed 0 is cached; seed 1 is still a miss.
    assert tc.resolve_or_fail(root, img, "m", 0).is_file()
    with pytest.raises(tc.TrellisCacheMiss):
        tc.resolve_or_fail(root, img, "m", 1)


# ---------- store round-trip ----------

def test_store_then_resolve_round_trip(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    produced = _write_image(tmp_path / "produced.glb", b"MESH-BYTES")
    root = tmp_path / "cache"

    dest = tc.store(root, img, "trellis2-fp8", 7, produced)
    assert dest.read_bytes() == b"MESH-BYTES"

    resolved = tc.resolve_or_fail(root, img, "trellis2-fp8", 7)
    assert resolved == dest


def test_store_copies_bytes_not_reference(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    produced = _write_image(tmp_path / "produced.glb", b"v1")
    root = tmp_path / "cache"
    dest = tc.store(root, img, "m", 0, produced)

    # Mutating the producer's scratch file must not affect the cached artifact.
    produced.write_bytes(b"v2-changed")
    assert dest.read_bytes() == b"v1"


def test_store_creates_cache_root(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    produced = _write_image(tmp_path / "produced.glb", b"glb")
    root = tmp_path / "deep" / "nested" / "cache"
    assert not root.exists()
    tc.store(root, img, "m", 0, produced)
    assert root.is_dir()


def test_store_is_idempotent_overwrite(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    root = tmp_path / "cache"
    tc.store(root, img, "m", 0, _write_image(tmp_path / "a.glb", b"first"))
    dest = tc.store(root, img, "m", 0, _write_image(tmp_path / "b.glb", b"second"))
    # Same key -> same path, content fully determined by the key, last write wins.
    assert dest.read_bytes() == b"second"
    assert len(list(root.glob("*.glb"))) == 1


def test_store_missing_produced_glb_raises(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    with pytest.raises(FileNotFoundError):
        tc.store(tmp_path / "cache", img, "m", 0, tmp_path / "absent.glb")


# ---------- provenance ----------

def test_provenance_records_frozen_inputs(tmp_path):
    img = _write_image(tmp_path / "ref.png", b"pixels")
    prov = tc.provenance(img, "trellis2-fp8", 3)
    assert prov == {
        "backend": "trellis",
        "reference_image": str(img),
        "image_sha256": hashlib.sha256(b"pixels").hexdigest(),
        "model_version": "trellis2-fp8",
        "seed": 3,
        "cache_key": tc.cache_key(img, "trellis2-fp8", 3),
    }


def test_provenance_validates_inputs(tmp_path):
    img = _write_image(tmp_path / "ref.png")
    with pytest.raises(ValueError):
        tc.provenance(img, "../bad", 0)
