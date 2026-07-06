"""Bpy-free helpers shared by every stage script (spec 4.3, 9.3).

Deliberately importable with plain CPython (no ``bpy``/``bmesh``/``mathutils``
anywhere in this module) so it is unit-testable without Blender installed —
see ``assetpipe/tests/test_blender_scripts.py``.

``deterministic_scene_settings(scene)`` is *not* defined here: it necessarily
touches ``bpy.types.Scene``, so it lives in
:func:`assetpipe.blender_scripts.generate.deterministic_scene_settings`
instead (the first stage to need a scene) and is imported from there by
``bake.py`` and ``render_views.py``. It pins, per spec 3: Cycles engine, CPU
device, ``cycles.seed = 0``, ``use_animated_seed = False``, metric units at
``scale_length = 1.0`` — the same values every stage script must agree on so
renders/bakes are bit-identical across runs.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any


def parse_args(argv: list[str] | None = None) -> dict:
    """Parse the ``-- --args-json <path> [--flag [value]] ...`` convention
    (spec 4.3) that every ``blender --background --python <script>.py --
    ...`` invocation in this pipeline uses.

    Blender puts its own flags (and the ``.blend`` path, if any) before a bare
    ``--``; everything after that belongs to the script. This function:

    1. Extracts the tail after the first bare ``--`` (falls back to
       ``argv[1:]`` if there is no ``--``, so this is also callable in plain
       Python scripts/tests without a Blender-style argv).
    2. Parses ``--key value`` / boolean ``--flag`` pairs into a dict (dashes
       kept as given, e.g. ``--out renders/`` -> ``{"out": "renders/"}``).
    3. Requires ``--args-json <path>``, loads that file as JSON (the payload
       every stage script actually operates on — request/theme/profile/params
       per spec 5's file-boundary table), and returns it with the raw parsed
       CLI flags stashed under the reserved ``"_argv"`` key so a script can
       still see e.g. ``--out`` without a second parse.

    Raises ``ValueError`` if ``--args-json`` is missing (a stage script must
    never run with an ambiguous or absent payload).
    """
    argv = sys.argv if argv is None else list(argv)
    if "--" in argv:
        tail = argv[argv.index("--") + 1:]
    else:
        tail = argv[1:]

    opts: dict[str, Any] = {}
    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok.startswith("--"):
            key = tok[2:]
            if i + 1 < len(tail) and not tail[i + 1].startswith("--"):
                opts[key] = tail[i + 1]
                i += 2
            else:
                opts[key] = True
                i += 1
        else:
            i += 1

    if "args-json" not in opts or opts["args-json"] is True:
        raise ValueError("missing required '--args-json <path>' argument")

    payload = json.loads(Path(opts["args-json"]).read_text())
    payload["_argv"] = opts
    return payload


def write_result(path: str | Path, data: dict) -> Path:
    """Write ``data`` as pretty, deterministically-ordered JSON to ``path``,
    creating parent directories as needed. Every stage script's terminal
    artifact (``params.json``, ``result.json``, ``static_report.json``, ...)
    goes through this so the on-disk shape is consistent (spec 17.1/17.2:
    "the exact inputs/evidence/decision are on disk, keyed by iteration")."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
    return out


def seeded_random(seed: int) -> random.Random:
    """The one sanctioned source of randomness for generation (spec 3):
    ``random.Random(request.seed)``, never the module-level ``random.*``
    functions and never wall-clock-derived seeds."""
    return random.Random(seed)


def seeded_numpy_rng(seed: int):
    """``numpy.random.default_rng(seed)`` for vectorized seeded randomness
    (e.g. dithering, spec 10.4). Numpy is an optional import here (only
    pulled in when this function is actually called) so ``common.py`` itself
    has zero hard dependencies beyond the standard library."""
    import numpy as np

    return np.random.default_rng(seed)


_NUMERIC_TYPES = ("number", "integer")


def resolve_params(param_schema: dict, theme: dict, overrides: dict, rng: random.Random) -> dict:
    """Resolve a generator/material recipe's final parameter dict (spec 9.3):

        recipe defaults -> theme clamps (``<param>_range`` in theme.json,
        e.g. ``wear_range``) -> seeded jitter (uniform +-10% on numeric
        params, from ``rng``) -> ``param_overrides`` (clamped to the
        schema's own ``minimum``/``maximum``, never silently out of bounds).

    Pure function of its inputs (given the same ``rng`` state) so it is
    unit-testable without Blender and is exactly what makes ``params.json``
    reproducible: the caller writes this dict to disk *before* generation
    (spec 9.3) so a mid-generation crash still leaves the exact inputs.
    """
    props = param_schema.get("properties", {})
    params: dict = {name: spec["default"] for name, spec in props.items() if "default" in spec}

    for name, spec in props.items():
        if spec.get("type") not in _NUMERIC_TYPES or name not in params:
            continue
        range_key = f"{name}_range"
        if range_key in theme:
            lo, hi = theme[range_key]
            params[name] = min(max(params[name], lo), hi)

    for name, spec in props.items():
        if spec.get("type") not in _NUMERIC_TYPES or name not in params:
            continue
        lo = spec.get("minimum", float("-inf"))
        hi = spec.get("maximum", float("inf"))
        jittered = params[name] * (1.0 + rng.uniform(-0.10, 0.10))
        jittered = min(max(jittered, lo), hi)
        params[name] = int(round(jittered)) if spec["type"] == "integer" else jittered

    for name, value in overrides.items():
        if name not in props:
            continue
        spec = props[name]
        if spec.get("type") in _NUMERIC_TYPES and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            lo = spec.get("minimum", float("-inf"))
            hi = spec.get("maximum", float("inf"))
            value = min(max(value, lo), hi)
            if spec["type"] == "integer":
                value = int(round(value))
        params[name] = value

    return params
