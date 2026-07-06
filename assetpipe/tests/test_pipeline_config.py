"""Toolchain version gate (spec 3): exact pins vs "+"-suffixed floor pins."""
from __future__ import annotations

import pytest

from assetpipe.pipeline_config import load_config, toolchain_check


def _cfg(**pins) -> dict:
    return {"toolchain": {"require_exact": True, **pins}}


def test_exact_pin_match_passes():
    assert toolchain_check(_cfg(blender="4.2"), {"blender": lambda: "4.2"}) == []


def test_exact_pin_mismatch_fails():
    errors = toolchain_check(_cfg(blender="4.2"), {"blender": lambda: "4.3"})
    assert len(errors) == 1 and "blender" in errors[0]


@pytest.mark.parametrize("actual", ["4.3", "4.6", "5.0"])
def test_floor_pin_accepts_equal_and_newer(actual):
    assert toolchain_check(_cfg(godot="4.3+"), {"godot": lambda: actual}) == []


@pytest.mark.parametrize("actual", ["4.2", "3.5"])
def test_floor_pin_rejects_older(actual):
    errors = toolchain_check(_cfg(godot="4.3+"), {"godot": lambda: actual})
    assert len(errors) == 1 and "godot" in errors[0]


def test_floor_pin_compares_numerically_not_lexically():
    # "4.10" > "4.3" numerically but < lexically; the gate must treat it as newer.
    assert toolchain_check(_cfg(godot="4.3+"), {"godot": lambda: "4.10"}) == []


def test_probe_failure_is_collected_not_raised():
    def boom():
        raise FileNotFoundError("no such binary")
    errors = toolchain_check(_cfg(godot="4.3+"), {"godot": boom})
    assert len(errors) == 1 and "probe" in errors[0]


def test_default_config_pins_godot_as_floor():
    cfg = load_config(None)
    assert cfg["toolchain"]["godot"].endswith("+")
    assert cfg["toolchain"]["blender"] == "4.2"
