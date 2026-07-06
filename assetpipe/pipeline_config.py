"""Pipeline configuration loading and the toolchain version gate (spec 3, 20.3).

Two independent responsibilities:

- :func:`load_config` — deep-merge a user override YAML over
  ``assetpipe/config/defaults.yaml``, so every threshold/knob referenced
  throughout the orchestrator (``config["validation"]``, ``config["iteration"]``,
  ``config["render"]``, ...) always comes from one merged dict, never a
  hand-edited copy.
- :func:`toolchain_check` — spec 3's hard version pin: "the pipeline must
  refuse to run ... if a component reports a different major/minor version".
  Probing is injected (``probes``) so this is unit-testable without Blender or
  Godot installed; :func:`default_probes` builds the real subprocess-based
  probes used in production.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

import yaml

DEFAULTS_PATH = Path(__file__).parent / "config" / "defaults.yaml"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base``; dict values merge key by
    key, everything else (including lists) is replaced wholesale."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(overrides_path: Path | None = None) -> dict:
    """Load ``config/defaults.yaml``, deep-merged with ``overrides_path`` (a
    user-supplied YAML file) if given. Returns a fresh dict every call — safe
    for a caller to mutate (e.g. before writing the run's config snapshot)."""
    config = yaml.safe_load(DEFAULTS_PATH.read_text()) or {}
    if overrides_path is not None:
        override = yaml.safe_load(Path(overrides_path).read_text()) or {}
        config = _deep_merge(config, override)
    return config


def _version_tuple(major_minor: str, component: str) -> tuple[int, int]:
    m = _VERSION_RE.fullmatch(major_minor.strip())
    if not m:
        raise RuntimeError(
            f"unparseable major.minor version {major_minor!r} for {component!r}")
    return int(m.group(1)), int(m.group(2))


def _parse_major_minor(version_text: str, binary: str) -> str:
    m = _VERSION_RE.search(version_text)
    if not m:
        raise RuntimeError(
            f"could not parse a major.minor version out of {binary!r} --version "
            f"output: {version_text!r}")
    return f"{m.group(1)}.{m.group(2)}"


def _subprocess_probe(binary: str, args: tuple[str, ...] = ("--version",),
                       timeout: float = 30.0) -> Callable[[], str]:
    """Build a probe that runs ``<binary> <args>`` and extracts the first
    ``major.minor`` token from combined stdout+stderr."""

    def probe() -> str:
        proc = subprocess.run([binary, *args], capture_output=True, text=True,
                              timeout=timeout)
        text = (proc.stdout or "") + (proc.stderr or "")
        return _parse_major_minor(text, binary)

    return probe


def default_probes(blender_bin: str = "blender",
                   godot_bin: str = "godot") -> dict[str, Callable[[], str]]:
    """The production probes: ``<blender_bin> --version`` and
    ``<godot_bin> --version --headless`` (Godot 4 prints its version even
    without a display in headless mode)."""
    return {
        "blender": _subprocess_probe(blender_bin, ("--version",)),
        "godot": _subprocess_probe(godot_bin, ("--version", "--headless")),
    }


def toolchain_check(config: dict, probes: dict[str, Callable[[], str]]) -> list[str]:
    """Compare each probe's reported ``major.minor`` version against
    ``config["toolchain"][name]``. Returns a list of human-readable error
    strings (empty == everything matches); a probe that itself raises (binary
    missing, unparseable output, timeout) is also reported as an error rather
    than propagating, so a batch run can record every toolchain problem at
    once instead of crashing on the first missing binary.

    A pin ending in ``+`` (e.g. ``"4.3+"``) is a floor, not an exact match:
    any probed ``major.minor`` >= the floor passes. This is spec 3's Godot row
    ("**4.3+**"); Blender stays an exact ``"4.2"`` pin because mesh hashes and
    bake output drift across Blender feature releases, while the Godot adapter
    only needs the 4.3 import surface that later 4.x releases keep.

    Components named in ``probes`` but absent from ``config["toolchain"]`` are
    skipped (nothing to compare against); the reverse (a pinned component with
    no probe supplied) is also silently skipped -- callers decide which
    components they care to probe.
    """
    errors: list[str] = []
    toolchain = config.get("toolchain", {})
    for name, probe in probes.items():
        expected = toolchain.get(name)
        if expected is None:
            continue
        try:
            actual = probe()
        except Exception as exc:  # noqa: BLE001 - collected, not raised
            errors.append(f"toolchain probe for {name!r} failed: {exc}")
            continue
        if str(expected).endswith("+"):
            floor = _version_tuple(str(expected)[:-1], name)
            if _version_tuple(actual, name) < floor:
                errors.append(f"toolchain mismatch for {name!r}: "
                              f"expected {expected!r}, got {actual!r}")
        elif actual != expected:
            errors.append(
                f"toolchain mismatch for {name!r}: expected {expected!r}, got {actual!r}")
    return errors
