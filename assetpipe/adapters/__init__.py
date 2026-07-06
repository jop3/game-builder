"""Adapter registry (spec §18): `pipeline.yaml -> delivery.adapters: [...]`
selects adapters by name; the orchestrator/CLI never import engine-specific
modules directly, only `get_adapter`.
"""
from __future__ import annotations

from typing import Callable

from assetpipe.adapters.base import AdapterReport, DeliveryRecord, EngineAdapter
from assetpipe.adapters.godot.adapter import GodotAdapter

# name -> constructor. Registering the class object (not an instance) lets
# callers pass adapter-specific kwargs (e.g. godot's `project_path`) through
# get_adapter without the registry needing to know each adapter's __init__.
_REGISTRY: dict[str, Callable[..., EngineAdapter]] = {
    "godot": GodotAdapter,
}


def get_adapter(name: str, **kwargs) -> EngineAdapter:
    """Instantiate the adapter registered under `name`.

    Extra keyword arguments are forwarded to the adapter's constructor (e.g.
    `get_adapter("godot", project_path=..., use_pipeline_lods=True)`); with no
    kwargs, `GodotAdapter` falls back to its own defaults so
    `get_adapter("godot")` alone is always valid.
    """
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown engine adapter {name!r}; registered adapters: {sorted(_REGISTRY)}"
        ) from None
    return factory(**kwargs)


__all__ = ["get_adapter", "EngineAdapter", "DeliveryRecord", "AdapterReport"]
