"""Contract loader: single point of truth for the defect taxonomy, vision rubric,
fix table, platform profiles, and JSON Schemas.

Everything downstream (prompt builder, vision tool schema, fix planner, validators)
MUST obtain these through :class:`Contracts` — never by re-reading the JSON files —
so the cross-consistency checks in :meth:`Contracts.validate` are the one gate that
keeps taxonomy, rubric, fixes, and prompts from drifting apart (spec Appendix B).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schemas"
PROFILE_DIR = Path(__file__).parent / "profiles"

STAGES = ("G", "M", "B", "X")  # generate < material < background/sky < export
SEVERITIES = ("blocker", "warn", "infra")
MESH_CATEGORIES = (
    "prop_small", "prop_hero", "character_primary", "character_background",
    "environment_piece", "modular_kit_piece",
)
GENERATOR_CATEGORIES = MESH_CATEGORIES + ("tiling_texture_set",)
# tiling_texture_set resolves a generator too: its recipe builds the spec-10.3
# unit-plane bake target. skybox/background_2d are stage-B deliverables with
# no generator (and no stage-B branch yet -- intake rejects them).
ALL_CATEGORIES = GENERATOR_CATEGORIES + ("skybox", "background_2d")


class ContractError(Exception):
    """A contract file is internally inconsistent. Refuse to run (spec 3)."""


@dataclass(frozen=True)
class Contracts:
    defects: dict = field(repr=False)
    rubric: dict = field(repr=False)
    fixes: dict = field(repr=False)
    request_schema: dict = field(repr=False)
    fix_plan_schema: dict = field(repr=False)

    # ---------- loading ----------

    @classmethod
    def load(cls, schema_dir: Path = SCHEMA_DIR) -> "Contracts":
        def read(name: str) -> dict:
            return json.loads((schema_dir / name).read_text())

        c = cls(
            defects=read("defects.json")["defects"],
            rubric=read("rubric.json"),
            fixes=read("fixes.json")["fixes"],
            request_schema=read("asset_request.schema.json"),
            fix_plan_schema=read("fix_plan.schema.json"),
        )
        c.validate()
        return c

    # ---------- cross-consistency (the anti-drift gate) ----------

    def validate(self) -> None:
        errors: list[str] = []
        for did, d in self.defects.items():
            if d["severity"] not in SEVERITIES:
                errors.append(f"defect {did}: bad severity {d['severity']!r}")
            if d["resume_stage"] not in STAGES:
                errors.append(f"defect {did}: bad resume_stage {d['resume_stage']!r}")
            fix = d["table_fix"]
            if fix is not None:
                if fix not in self.fixes:
                    errors.append(f"defect {did}: table_fix {fix!r} not in fixes.json")
                elif STAGES.index(self.fixes[fix]["resume_stage"]) < STAGES.index(d["resume_stage"]):
                    # A table fix repairs the defective artifact IN PLACE on
                    # the previous iteration's state; the pipeline then resumes
                    # at or after the stage that produced the artifact, never
                    # before it -- resuming earlier regenerates the very
                    # artifact the fix just repaired and discards the repair
                    # (observed as a NO-PROGRESS loop against real Blender).
                    # The defect's own resume_stage is where a *param patch*
                    # resumes (regeneration), not where its table fix does.
                    errors.append(
                        f"defect {did}: fix {fix!r} resumes at {self.fixes[fix]['resume_stage']}"
                        f" which is earlier than the defect's stage {d['resume_stage']}"
                        f" -- the resumed stages would regenerate over the repair")
        for fid, f in self.fixes.items():
            if f["resume_stage"] not in STAGES:
                errors.append(f"fix {fid}: bad resume_stage {f['resume_stage']!r}")
        groups = self.rubric.get("category_groups", {})
        for cid, chk in self.rubric["checks"].items():
            for cat in chk["applies_to"]:
                if cat.startswith("@"):
                    if cat not in groups:
                        errors.append(f"rubric {cid}: unknown category group {cat!r}")
                elif cat not in ALL_CATEGORIES:
                    errors.append(f"rubric {cid}: unknown category {cat!r}")
            for d in chk["allowed_defects"]:
                if d not in self.defects:
                    errors.append(f"rubric {cid}: allowed defect {d!r} not in taxonomy")
            if chk["severity"] not in ("blocker", "warn"):
                errors.append(f"rubric {cid}: bad severity {chk['severity']!r}")
            if chk["min_views_for_fail"] not in (1, 2):
                errors.append(f"rubric {cid}: min_views_for_fail must be 1 or 2")
        if errors:
            raise ContractError("; ".join(errors))

    # ---------- taxonomy / rubric queries ----------

    def taxonomy_ids(self) -> list[str]:
        return sorted(self.defects)

    def defect_severity(self, defect_type: str) -> str:
        return self.defects[defect_type]["severity"]

    def resume_stage_for(self, defect_type: str) -> str:
        return self.defects[defect_type]["resume_stage"]

    def table_fix_for(self, defect_type: str) -> str | None:
        return self.defects[defect_type]["table_fix"]

    def _expand_categories(self, applies_to: list[str]) -> set[str]:
        out: set[str] = set()
        groups = self.rubric.get("category_groups", {})
        for cat in applies_to:
            out.update(groups[cat] if cat.startswith("@") else [cat])
        return out

    def applicable_checks(self, category: str) -> dict[str, dict]:
        """Rubric checks that apply to an asset category, in stable R-number order."""
        return {
            cid: chk
            for cid, chk in sorted(self.rubric["checks"].items(),
                                   key=lambda kv: int(kv[0][1:]))
            if category in self._expand_categories(chk["applies_to"])
        }

    def check_severity(self, check_id: str) -> str:
        return self.rubric["checks"][check_id]["severity"]

    @staticmethod
    def view_matches(view_id: str, patterns: list[str]) -> bool:
        return any(fnmatch(view_id, p) for p in patterns)

    # ---------- generated artifacts (never hand-write these elsewhere) ----------

    def report_tool_schema(self, category: str) -> dict:
        """input_schema for the forced `report_inspection` tool (spec 15.4).

        The check-id and defect-type enums are generated here so the tool schema
        can never disagree with rubric.json / defects.json.
        """
        check_ids = list(self.applicable_checks(category))
        return {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "iteration": {"type": "integer", "minimum": 1},
                "checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "check_id": {"enum": check_ids},
                            "verdict": {"enum": ["pass", "fail", "uncertain"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence_views": {"type": "array", "items": {"type": "string"}},
                            "location": {"type": "string"},
                            "defect_type": {"enum": self.taxonomy_ids()},
                            "description": {"type": "string"},
                            "suggested_fix_hint": {"type": "string"},
                        },
                        "required": ["check_id", "verdict", "confidence"],
                        "additionalProperties": False,
                    },
                },
                "checks_not_applicable": {"type": "array", "items": {"enum": check_ids}},
                "overall_impression": {"type": "string"},
                # Open-ended catch-all (borrowed from Snittet's spelbygge feel
                # rubric: "what is the ugliest thing?"). Non-gating and outside
                # the closed R-rubric on purpose -- it surfaces the
                # technically-valid-but-off defects that pass every check yet
                # still make the asset read wrong. Logged, fed to diagnosis.md
                # and the human art-direction spot-check; never an asset verdict.
                "worst_thing": {"type": "string"},
            },
            "required": ["asset_id", "iteration", "checks", "checks_not_applicable",
                         "overall_impression"],
            "additionalProperties": False,
        }

    # ---------- profiles ----------

    @staticmethod
    @lru_cache(maxsize=8)
    def profile(name: str) -> dict:
        path = PROFILE_DIR / f"{name}.json"
        if not path.exists():
            raise ContractError(f"unknown platform profile {name!r}")
        return json.loads(path.read_text())


def stage_order(stage: str) -> int:
    return STAGES.index(stage)


def earliest_stage(stages: list[str]) -> str:
    if not stages:
        raise ValueError("no stages given")
    return min(stages, key=stage_order)
