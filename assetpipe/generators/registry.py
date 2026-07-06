"""Generator registry: discovery, validation, and resolution (spec 9.2).

Recipes are plain Python modules (see ``assetpipe/generators/__init__.py`` for
the module contract); this file never imports ``bpy`` and works with zero
recipes registered (there are none shipped yet — see the README's build
order).
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from types import ModuleType

from assetpipe.contracts import MESH_CATEGORIES

_REQUIRED_ATTRS = ("PARAM_SCHEMA", "CATEGORY", "KEYWORDS", "generate")
_NUMERIC_TYPES = ("number", "integer")


class RegistryError(Exception):
    """A recipe module is malformed, or an unknown recipe id was requested."""


class Registry:
    """Indexes generator recipe modules by ``<category_group>/<name>`` id."""

    def __init__(self) -> None:
        self._recipes: dict[str, ModuleType] = {}

    # ---------- registration ----------

    def register(self, recipe_id: str, module: ModuleType) -> None:
        errors: list[str] = []

        for attr in _REQUIRED_ATTRS:
            if not hasattr(module, attr):
                errors.append(f"missing required attribute {attr!r}")
        if errors:
            raise RegistryError(f"{recipe_id}: " + "; ".join(errors))

        if not callable(module.generate):
            errors.append("generate is not callable")

        schema = module.PARAM_SCHEMA
        if not isinstance(schema, dict) or schema.get("type") != "object":
            errors.append("PARAM_SCHEMA must be a JSON Schema object (type: object)")
        else:
            for prop, spec in schema.get("properties", {}).items():
                if not isinstance(spec, dict):
                    errors.append(f"PARAM_SCHEMA.properties.{prop}: not an object")
                    continue
                if spec.get("type") in _NUMERIC_TYPES:
                    if "minimum" not in spec or "maximum" not in spec:
                        errors.append(
                            f"PARAM_SCHEMA.properties.{prop}: numeric params must "
                            f"declare both minimum and maximum")
                    if "default" not in spec:
                        errors.append(
                            f"PARAM_SCHEMA.properties.{prop}: numeric params must "
                            f"declare a default")

        if module.CATEGORY not in MESH_CATEGORIES:
            errors.append(
                f"CATEGORY {module.CATEGORY!r} is not one of {MESH_CATEGORIES}")

        if not isinstance(module.KEYWORDS, list) or not all(
                isinstance(k, str) for k in module.KEYWORDS):
            errors.append("KEYWORDS must be a list[str]")

        if errors:
            raise RegistryError(f"{recipe_id}: " + "; ".join(errors))

        self._recipes[recipe_id] = module

    # ---------- discovery ----------

    @classmethod
    def discover(cls, package: str = "assetpipe.generators") -> "Registry":
        """Walk ``package``'s immediate subpackages and register every recipe
        module found one level below them (spec: ``generators/<group>/<name>.py``).

        Skips private modules/packages (leading ``_``, e.g. ``__init__``) and
        stray top-level modules such as ``registry.py`` itself, which lives
        directly in ``package`` rather than in a subpackage.
        """
        registry = cls()
        pkg = importlib.import_module(package)
        if not hasattr(pkg, "__path__"):
            return registry

        for _, subpkg_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
            if not is_pkg or subpkg_name.startswith("_"):
                continue
            subpkg = importlib.import_module(f"{package}.{subpkg_name}")
            for _, mod_name, is_subpkg in pkgutil.iter_modules(subpkg.__path__):
                if is_subpkg or mod_name.startswith("_"):
                    continue
                module = importlib.import_module(
                    f"{package}.{subpkg_name}.{mod_name}")
                registry.register(f"{subpkg_name}/{mod_name}", module)
        return registry

    # ---------- lookup ----------

    def get(self, recipe_id: str) -> ModuleType:
        try:
            return self._recipes[recipe_id]
        except KeyError:
            raise RegistryError(f"unknown generator recipe {recipe_id!r}") from None

    def __contains__(self, recipe_id: str) -> bool:
        return recipe_id in self._recipes

    def __len__(self) -> int:
        return len(self._recipes)

    def ids(self) -> list[str]:
        return sorted(self._recipes)

    # ---------- resolution (spec 9.2) ----------

    def resolve(self, category: str, description: str) -> str | None:
        """Pick a recipe id for ``category`` by keyword overlap with
        ``description``. Highest score wins; ties break lexicographically by
        recipe id (determinism); an all-zero score returns ``None``.
        """
        tokens = set(re.findall(r"[a-z0-9]+", description.lower()))
        candidates = [
            (recipe_id, module)
            for recipe_id, module in self._recipes.items()
            if module.CATEGORY == category
        ]
        if not candidates:
            return None

        scored = [
            (recipe_id, sum(1 for kw in module.KEYWORDS if kw.lower() in tokens))
            for recipe_id, module in candidates
        ]
        best_score = max(score for _, score in scored)
        if best_score == 0:
            return None
        best_ids = sorted(rid for rid, score in scored if score == best_score)
        return best_ids[0]
