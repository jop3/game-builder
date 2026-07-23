"""Content-addressed cache for neural image-to-3D outputs (docs/NEURAL_BACKEND.md).

A neural backend (TRELLIS / trellis.cpp) turns a reference image into a raw
mesh via a heavy, **non-deterministic** CUDA process. That step cannot live
inside a generator recipe's ``generate()`` — recipes must be deterministic
given ``(params, rng)`` (spec 9.1) and the Blender core must stay GPU-free.

This module is the seam that reconciles the two: the model output is produced
**once**, out of band, and frozen as a cache artifact keyed by the tuple that
fully determines it —

    (sha256 of the reference-image bytes, model_version, seed)

On a cache hit the recipe's ``generate()`` merely imports the frozen ``.glb``
and runs the same finishing passes as any procedural recipe, so it is
deterministic and re-runnable (spec 21.2). On a cache miss it fails clean via
:class:`TrellisCacheMiss` — it never runs a model in-process, keeping Blender
CUDA-free.

Keying on the image **content** (not its path) is deliberate: the same image
under a different filename resolves to the same artifact, and flipping a
single byte invalidates it. Everything here is pure CPython — no ``bpy``, no
network — and is covered by ``assetpipe/tests/test_trellis_cache.py``.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

__all__ = [
    "TrellisCacheMiss",
    "image_digest",
    "cache_key",
    "artifact_path",
    "resolve_or_fail",
    "store",
    "provenance",
]

# model_version becomes a path segment, so it must be filesystem-safe and free
# of traversal. Allow a conservative slug charset only; anything else is a
# programming/config error, surfaced as ValueError rather than silently
# sanitised (a silent rewrite could collide two distinct versions onto one key).
_MODEL_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")

_READ_CHUNK = 1 << 20  # 1 MiB streaming read; reference images can be large.

_ARTIFACT_SUFFIX = ".glb"


class TrellisCacheMiss(Exception):
    """No cached artifact exists for the requested (image, model, seed).

    The generator recipe catches this and re-raises it as ``loop.InfraError``
    (spec 4.3) so the orchestrator treats a missing neural artifact as an
    infrastructure gap — run the out-of-band generation service to populate
    the cache — not as an asset-quality failure that the fix loop should chew
    on. This module stays free of that coupling on purpose.
    """


def _validate_model_version(model_version: str) -> str:
    if not isinstance(model_version, str) or not _MODEL_VERSION_RE.match(model_version):
        raise ValueError(
            f"model_version {model_version!r} is not a filesystem-safe slug "
            f"(allowed: letters, digits, and . _ + - ; must not start with a "
            f"separator)")
    return model_version


def _validate_seed(seed: int) -> int:
    # bool is an int subclass; reject it explicitly so True/False can't stand
    # in for a seed and produce surprising keys.
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be a non-negative int, got {seed!r}")
    return seed


def image_digest(image_path: str | Path) -> str:
    """SHA-256 (hex) of the reference image's raw bytes, streamed.

    Raises ``FileNotFoundError`` if the image is missing — a clearer signal
    than a downstream cache miss, since a mistyped reference path is a
    different problem from an un-generated artifact.
    """
    path = Path(image_path)
    h = hashlib.sha256()
    with path.open("rb") as fh:  # raises FileNotFoundError with the path in it
        for chunk in iter(lambda: fh.read(_READ_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(image_path: str | Path, model_version: str, seed: int) -> str:
    """Deterministic cache key for one neural generation.

    Shape: ``<sha256>__<model_version>__seed<seed>``. Stable across machines
    and runs; two byte-identical images at different paths collide (by design)
    and any change to image/model/seed produces a fresh key.
    """
    _validate_model_version(model_version)
    _validate_seed(seed)
    digest = image_digest(image_path)
    return f"{digest}__{model_version}__seed{seed}"


def artifact_path(
    cache_root: str | Path,
    image_path: str | Path,
    model_version: str,
    seed: int,
) -> Path:
    """Absolute path where the frozen ``.glb`` for this generation lives.

    Pure path arithmetic — does not touch the filesystem beyond hashing the
    image, and does not require the artifact to exist.
    """
    key = cache_key(image_path, model_version, seed)
    return Path(cache_root) / f"{key}{_ARTIFACT_SUFFIX}"


def resolve_or_fail(
    cache_root: str | Path,
    image_path: str | Path,
    model_version: str,
    seed: int,
) -> Path:
    """Return the cached artifact path, or raise :class:`TrellisCacheMiss`.

    This is what the in-Blender recipe calls: a hit yields a path it can
    import; a miss is an actionable, non-quality failure.
    """
    path = artifact_path(cache_root, image_path, model_version, seed)
    if not path.is_file():
        raise TrellisCacheMiss(
            f"no cached neural mesh at {path}; generate it out of band with "
            f"model_version={model_version!r}, seed={seed} for image "
            f"{Path(image_path).name!r} and store it via trellis_cache.store()")
    return path


def store(
    cache_root: str | Path,
    image_path: str | Path,
    model_version: str,
    seed: int,
    produced_glb: str | Path,
) -> Path:
    """Freeze a freshly generated ``.glb`` into the cache; return its path.

    Called by the **out-of-band** generation service after the CUDA model
    runs — never by the Blender core. Copies bytes (not a move/symlink) so the
    cache owns an immutable artifact independent of the producer's scratch
    space. Creates ``cache_root`` if needed. Idempotent: re-storing the same
    (image, model, seed) overwrites in place, which is safe because the key
    fully determines the content.
    """
    src = Path(produced_glb)
    if not src.is_file():
        raise FileNotFoundError(f"produced glb not found: {src}")
    dest = artifact_path(cache_root, image_path, model_version, seed)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return dest


def provenance(image_path: str | Path, model_version: str, seed: int) -> dict:
    """The frozen inputs that identify a cached artifact, for run history.

    A plain JSON-able dict suitable for dropping into the run manifest /
    ``history.jsonl`` (spec 17) so a delivered neural asset records exactly
    which image + model + seed produced it.
    """
    return {
        "backend": "trellis",
        "reference_image": str(image_path),
        "image_sha256": image_digest(image_path),
        "model_version": _validate_model_version(model_version),
        "seed": _validate_seed(seed),
        "cache_key": cache_key(image_path, model_version, seed),
    }
