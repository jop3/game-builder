"""Asset Request intake: fail-fast validation before any Blender process runs
(spec 6). Intake failures consume zero iterations — they are reported per-asset
in the run manifest, never fed into the repair loop.

Checks, all independent and all collected (never first-fail):

1. JSON-Schema validation against ``contracts.request_schema`` (draft 2020-12).
2. The named platform profile exists (``Contracts.profile``).
3. The named theme exists as ``<themes_root>/<theme>/theme.json`` (only checked
   when a ``themes_root`` is supplied — themes are not implemented yet).
4. The named generator (if any) exists and its ``CATEGORY`` matches the
   request; if omitted for a mesh category, it is resolved via the generator
   registry's keyword index (``Registry.resolve``) — no match is a
   ``NO_GENERATOR`` error. Only checked when a ``registry`` is supplied.
5. ``budget_overrides`` may only *tighten* the platform profile's budgets
   (spec 6, 8) — see :func:`_check_budget_overrides` for the exact per-key
   mapping onto ``profiles/*.json``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator

from assetpipe.contracts import MESH_CATEGORIES, ContractError, Contracts

if TYPE_CHECKING:
    from assetpipe.generators.registry import Registry

from assetpipe.generators.registry import RegistryError


class IntakeError(Exception):
    """One request failed intake. ``.errors`` holds every problem found;
    ``str()``/the default message joins them so a bare ``print(exc)`` is
    still useful."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _check_budget_overrides(overrides: dict, profile: dict, category: str) -> list[str]:
    """Validate that ``overrides`` only tighten ``profile`` for ``category``.

    Key -> profile mapping (see e.g. ``assetpipe/profiles/web.json``):

    - ``max_triangles``  -> ``profile["triangles"][category]["max"]``
    - ``max_file_bytes`` -> ``profile["file_bytes"][category]``
    - ``max_texture_px`` -> the *tightest* per-map value in
      ``profile["textures"][category]`` (albedo/normal/orm/emissive differ;
      ``max_texture_px`` is a single scalar cap applied to every map, so the
      only value that is guaranteed to tighten *every* map is <= the smallest
      of them — anything looser would loosen whichever map already had the
      smallest budget, e.g. emissive).

    A key with no corresponding entry for ``category`` in the profile (e.g.
    ``max_triangles`` on ``tiling_texture_set``, which has no triangle budget
    at all) is rejected: there is nothing to tighten against.
    """
    errors: list[str] = []
    for key, value in overrides.items():
        if key == "max_triangles":
            budget = profile.get("triangles", {}).get(category)
            if budget is None:
                errors.append(
                    f"budget_overrides.max_triangles: category {category!r} has no "
                    f"triangle budget to tighten")
            elif value > budget["max"]:
                errors.append(
                    f"budget_overrides.max_triangles={value} exceeds (loosens) the "
                    f"profile max of {budget['max']} for category {category!r}")
        elif key == "max_file_bytes":
            budget = profile.get("file_bytes", {}).get(category)
            if budget is None:
                errors.append(
                    f"budget_overrides.max_file_bytes: category {category!r} has no "
                    f"file-size budget to tighten")
            elif value > budget:
                errors.append(
                    f"budget_overrides.max_file_bytes={value} exceeds (loosens) the "
                    f"profile max of {budget} for category {category!r}")
        elif key == "max_texture_px":
            textures = profile.get("textures", {}).get(category)
            if textures is None:
                errors.append(
                    f"budget_overrides.max_texture_px: category {category!r} has no "
                    f"texture budget to tighten")
            else:
                tightest = min(textures.values())
                if value > tightest:
                    errors.append(
                        f"budget_overrides.max_texture_px={value} exceeds (loosens) "
                        f"the tightest per-map profile budget of {tightest} for "
                        f"category {category!r}")
        else:
            errors.append(f"budget_overrides: unknown key {key!r}")
    return errors


def validate_request(request: dict, contracts: Contracts, *,
                     themes_root: Path | None = None,
                     registry: "Registry | None" = None) -> dict:
    """Validate one request; return a normalized copy (with ``generator``
    resolved when a registry is given). Raise :class:`IntakeError` listing
    every problem found."""
    errors: list[str] = []
    normalized = dict(request)

    validator = Draft202012Validator(contracts.request_schema)
    for err in sorted(validator.iter_errors(request), key=lambda e: list(map(str, e.path))):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"schema error at {loc}: {err.message}")

    profile = None
    profile_name = request.get("platform_profile")
    if isinstance(profile_name, str):
        try:
            profile = contracts.profile(profile_name)
        except ContractError as exc:
            errors.append(f"platform_profile: {exc}")

    theme = request.get("theme")
    if themes_root is not None and isinstance(theme, str):
        theme_file = Path(themes_root) / theme / "theme.json"
        if not theme_file.exists():
            errors.append(f"theme {theme!r} not found (expected {theme_file})")

    category = request.get("category")
    generator = request.get("generator")
    if registry is not None and category is not None:
        if generator is not None:
            try:
                module = registry.get(generator)
            except RegistryError as exc:
                errors.append(f"generator: {exc}")
            else:
                if getattr(module, "CATEGORY", None) != category:
                    errors.append(
                        f"generator {generator!r} has CATEGORY {module.CATEGORY!r}, "
                        f"which does not match request category {category!r}")
        elif category in MESH_CATEGORIES or category == "tiling_texture_set":
            # tiling_texture_set resolves a generator too: its recipe builds
            # the spec-10.3 unit-plane bake target rather than a prop.
            description = request.get("description", "")
            resolved = registry.resolve(category, description)
            if resolved is None:
                errors.append(
                    f"NO_GENERATOR: no generator recipe could be resolved for "
                    f"category {category!r} from description {description!r}")
            else:
                normalized["generator"] = resolved
        elif category in ("skybox", "background_2d"):
            # Fail fast (spec 6: zero iterations consumed on rejection): the
            # loop has no stage-B branch yet, so accepting these would spawn
            # Blender only to hard-fail on generator resolution mid-loop.
            errors.append(
                f"NOT_IMPLEMENTED: category {category!r} has no pipeline branch "
                f"yet (stage B is unimplemented); rejected at intake")

    if profile is not None and category is not None:
        overrides = request.get("budget_overrides") or {}
        if isinstance(overrides, dict):
            errors.extend(_check_budget_overrides(overrides, profile, category))

    if errors:
        raise IntakeError(errors)
    return normalized


def validate_batch(requests: list[dict], contracts: Contracts, *,
                   themes_root: Path | None = None,
                   registry: "Registry | None" = None,
                   ) -> tuple[list[dict], dict[str, list[str]]]:
    """Validate each request independently. Returns ``(accepted, rejected)``
    where ``rejected`` maps ``asset_id`` (or a positional placeholder, if the
    id itself is missing/malformed) to the list of intake errors.

    Duplicate ``asset_id``s within the batch are themselves an intake error:
    the first occurrence is validated normally; every later occurrence sharing
    that id is rejected outright for the duplication, without further checks.
    """
    accepted: list[dict] = []
    rejected: dict[str, list[str]] = {}
    seen: set[str] = set()

    for idx, request in enumerate(requests):
        raw_id = request.get("asset_id") if isinstance(request, dict) else None
        key = raw_id if isinstance(raw_id, str) and raw_id else f"<request #{idx}>"

        if isinstance(raw_id, str) and raw_id in seen:
            rejected.setdefault(key, []).append(
                f"duplicate asset_id {raw_id!r} in batch")
            continue
        if isinstance(raw_id, str):
            seen.add(raw_id)

        try:
            normalized = validate_request(
                request, contracts, themes_root=themes_root, registry=registry)
        except IntakeError as exc:
            rejected.setdefault(key, []).extend(exc.errors)
        else:
            accepted.append(normalized)

    return accepted, rejected


def load_requests(path: Path) -> list[dict]:
    """Read a JSON file containing one request object or an array of them."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    return [data]
