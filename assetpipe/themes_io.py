"""Theme pack loading and validation (spec 7, 20.1). bpy-free: reads
``theme.json`` files and loads material recipe modules by path, so themes
are inspectable/testable in plain CPython. The actual node-graph building
in a material recipe's ``build()`` still needs Blender -- see
``assetpipe/matlib/nodes.py``.

Themes live at top-level ``themes/<theme_id>/`` (not inside the ``assetpipe``
package) and are exposed to the package via the ``assetpipe/themes ->
../themes`` symlink (spec 20.1), so both ``themes/scifi_industrial/...`` and
``assetpipe/themes/scifi_industrial/...`` resolve to the same files.
"""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

from assetpipe.matlib.palette import is_hex_color

REQUIRED_PALETTE_GROUPS = ("primary", "secondary", "accent", "emissive", "forbidden")

_NUMERIC_TYPES = ("number", "integer")


class ThemeIOError(Exception):
    """A theme or material recipe file could not be loaded from disk."""


def load_theme(themes_root: Path | str, theme_id: str) -> dict:
    """Load and JSON-parse ``<themes_root>/<theme_id>/theme.json``.

    Raises ``ThemeIOError`` if the file is missing or is not valid JSON.
    Does *not* validate the schema -- call :func:`validate_theme` for that.
    """
    path = Path(themes_root) / theme_id / "theme.json"
    if not path.is_file():
        raise ThemeIOError(f"no theme.json for {theme_id!r} at {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ThemeIOError(f"{path}: invalid JSON ({exc})") from exc


def load_material_recipe(themes_root: Path | str, theme_id: str, material_id: str) -> types.ModuleType:
    """Load ``<themes_root>/<theme_id>/materials/<material_id>.py`` as a
    module via ``importlib.util.spec_from_file_location`` (not a normal
    import -- theme material modules are not part of any Python package).

    Raises ``ThemeIOError`` if the file is missing or fails to execute.
    """
    path = Path(themes_root) / theme_id / "materials" / f"{material_id}.py"
    if not path.is_file():
        raise ThemeIOError(f"no material recipe {material_id!r} for theme {theme_id!r} at {path}")

    module_name = f"assetpipe_theme_material__{theme_id}__{material_id}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ThemeIOError(f"could not build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surface any load-time error uniformly
        raise ThemeIOError(f"{path}: failed to import ({exc})") from exc
    return module


def _validate_palette(palette, errors: list[str]) -> None:
    if not isinstance(palette, dict):
        errors.append("palette: must be an object")
        return
    for group in REQUIRED_PALETTE_GROUPS:
        if group not in palette:
            errors.append(f"palette: missing group {group!r}")
            continue
        hexes = palette[group]
        if not isinstance(hexes, list):
            errors.append(f"palette.{group}: must be a list of hex colors")
            continue
        for value in hexes:
            if not is_hex_color(value):
                errors.append(f"palette.{group}: {value!r} is not a #RRGGBB hex color")


def _validate_unit_range(theme: dict, key: str, errors: list[str]) -> None:
    value = theme.get(key)
    if not (isinstance(value, list) and len(value) == 2):
        errors.append(f"{key}: must be a [lo, hi] pair")
        return
    lo, hi = value
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (lo, hi)):
        errors.append(f"{key}: bounds must be numbers")
        return
    if not (0.0 <= lo <= hi <= 1.0):
        errors.append(f"{key}: bounds must satisfy 0 <= lo <= hi <= 1 (got {value!r})")


def validate_theme(theme: dict) -> list[str]:
    """Check a loaded theme dict against the spec 7 schema. Returns a list
    of human-readable error strings (empty == valid)."""
    errors: list[str] = []

    if theme.get("schema_version") != 1:
        errors.append("schema_version: must be 1")
    for key in ("theme_id", "display_name", "silhouette_language", "vision_style_brief"):
        if not isinstance(theme.get(key), str) or not theme.get(key):
            errors.append(f"{key}: must be a non-empty string")

    _validate_palette(theme.get("palette"), errors)

    materials = theme.get("materials")
    if not isinstance(materials, list) or not materials:
        errors.append("materials: must be a non-empty list")
    elif not all(isinstance(m, str) and m for m in materials):
        errors.append("materials: every entry must be a non-empty string")

    _validate_unit_range(theme, "wear_range", errors)
    _validate_unit_range(theme, "detail_density_range", errors)

    skybox = theme.get("skybox_defaults")
    if not isinstance(skybox, dict) or not isinstance(skybox.get("recipe"), str) or not skybox.get("recipe"):
        errors.append("skybox_defaults.recipe: must be a non-empty string")
    elif "sun_elevation_deg" in skybox and not isinstance(skybox["sun_elevation_deg"], (int, float)):
        errors.append("skybox_defaults.sun_elevation_deg: must be a number")

    return errors


def validate_material_recipe(module: types.ModuleType) -> list[str]:
    """Check a loaded material recipe module against the spec 10.2 contract.
    Returns a list of human-readable error strings (empty == valid)."""
    errors: list[str] = []

    schema = getattr(module, "PARAM_SCHEMA", None)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        errors.append("PARAM_SCHEMA must be a JSON Schema object (type: object)")
    else:
        for prop, prop_spec in schema.get("properties", {}).items():
            if not isinstance(prop_spec, dict):
                errors.append(f"PARAM_SCHEMA.properties.{prop}: not an object")
                continue
            if prop_spec.get("type") in _NUMERIC_TYPES:
                if "minimum" not in prop_spec or "maximum" not in prop_spec:
                    errors.append(f"PARAM_SCHEMA.properties.{prop}: numeric params must declare "
                                  f"both minimum and maximum")
                if "default" not in prop_spec:
                    errors.append(f"PARAM_SCHEMA.properties.{prop}: numeric params must declare a default")
                elif not (prop_spec.get("minimum", float("-inf")) <= prop_spec["default"]
                          <= prop_spec.get("maximum", float("inf"))):
                    errors.append(f"PARAM_SCHEMA.properties.{prop}: default out of [minimum, maximum]")

    if not callable(getattr(module, "build", None)):
        errors.append("build must be callable")

    bakes = getattr(module, "BAKES", None)
    allowed_bakes = {"albedo", "normal", "orm", "emissive"}
    if not isinstance(bakes, list) or not set(bakes).issubset(allowed_bakes):
        errors.append(f"BAKES must be a list subset of {sorted(allowed_bakes)}")

    if not isinstance(getattr(module, "TILING", None), bool):
        errors.append("TILING must be a bool")

    return errors
