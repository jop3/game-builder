"""Contract cross-consistency: the anti-drift gate (spec Appendix B rationale)."""
import json
from pathlib import Path

import jsonschema
import pytest

from assetpipe.contracts import (ALL_CATEGORIES, MESH_CATEGORIES, ContractError,
                                 Contracts, earliest_stage)

C = Contracts.load()


def test_loads_and_cross_validates():
    assert len(C.defects) >= 40
    assert len(C.rubric["checks"]) == 12


def test_every_rubric_defect_in_taxonomy_and_every_fix_exists():
    # (Contracts.load() already ran validate(); prove it fails when broken.)
    broken = Contracts(
        defects=dict(C.defects), rubric=json.loads(json.dumps(C.rubric)),
        fixes=dict(C.fixes), request_schema=C.request_schema,
        fix_plan_schema=C.fix_plan_schema)
    broken.rubric["checks"]["R1"]["allowed_defects"] = ["NOT_A_DEFECT"]
    with pytest.raises(ContractError, match="NOT_A_DEFECT"):
        broken.validate()


def test_fix_resume_stage_never_earlier_than_defect_stage():
    """A table fix repairs the previous iteration's artifact in place, so it
    resumes at (or after) the stage that produced that artifact; resuming
    earlier would regenerate over the repair (observed on real Blender)."""
    for did, d in C.defects.items():
        if d["table_fix"]:
            fix_stage = C.fixes[d["table_fix"]]["resume_stage"]
            assert earliest_stage([fix_stage, d["resume_stage"]]) == d["resume_stage"], did


def test_schemas_are_valid_draft_2020_12():
    for schema in (C.request_schema, C.fix_plan_schema):
        jsonschema.Draft202012Validator.check_schema(schema)


def test_example_request_validates():
    req = {
        "schema_version": 1, "asset_id": "scifi_crate_small_01",
        "category": "prop_small", "theme": "scifi_industrial",
        "platform_profile": "web", "seed": 421337,
        "description": "A small reinforced sci-fi supply crate with glowing status strip",
        "topology": "closed", "lods": "auto", "tags": ["kit:supply"],
    }
    jsonschema.validate(req, C.request_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**req, "category": "spaceship"}, C.request_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**req, "seed": -1}, C.request_schema)


def test_applicable_checks_per_category():
    for cat in MESH_CATEGORIES:
        ids = set(C.applicable_checks(cat))
        assert {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R12"} <= ids
        assert "R10" not in ids and "R11" not in ids
    assert set(C.applicable_checks("skybox")) == {"R1", "R11", "R12"}
    tiling = set(C.applicable_checks("tiling_texture_set"))
    assert "R10" in tiling and "R5" not in tiling


def test_report_tool_schema_is_generated_and_valid():
    for cat in ALL_CATEGORIES:
        schema = C.report_tool_schema(cat)
        jsonschema.Draft202012Validator.check_schema(schema)
        enum = schema["properties"]["checks"]["items"]["properties"]["defect_type"]["enum"]
        assert enum == C.taxonomy_ids()   # generated from defects.json — cannot drift


def test_profiles_load_and_tighten_monotonically():
    profs = {n: Contracts.profile(n) for n in
             ("desktop_high", "desktop_mid", "web", "mobile")}
    for cat in MESH_CATEGORIES:
        tris = [profs[n]["triangles"][cat]["max"] for n in
                ("desktop_high", "desktop_mid", "web", "mobile")]
        assert tris == sorted(tris, reverse=True), cat   # budgets tighten toward mobile
        for p in profs.values():
            assert p["triangles"][cat]["min"] >= 12
            assert p["triangles"][cat]["min"] < p["triangles"][cat]["max"]
    with pytest.raises(ContractError):
        Contracts.profile("playstation2")
