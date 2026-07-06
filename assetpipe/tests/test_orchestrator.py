"""End-to-end orchestration over a fake ``blender`` executable and a fake
vision client (spec 4, 16.5, 17, 20; README item 2). Reuses the fake-Blender
harness from ``test_stages.py`` so both suites exercise the same file-boundary
contract.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assetpipe.contracts import Contracts
from assetpipe.orchestrator import resume_run, run_batch
from assetpipe.rundir import RunDir
from assetpipe.tests.test_stages import _FAKE_BLENDER_SRC, _write_executable

C = Contracts.load()

GOOD_REQUEST = {
    "schema_version": 1,
    "asset_id": "scifi_crate_small_01",
    "category": "prop_small",
    "theme": "scifi_industrial",
    "platform_profile": "web",
    "seed": 421337,
    "description": "A small reinforced sci-fi supply crate",
    "generator": "props/crate",
}


class FakeVisionClient:
    """All-pass vision client: every applicable check reported not-applicable
    (a valid report shape per vision/report.py's exactly-once rule)."""

    def __init__(self, category: str = "prop_small"):
        self.calls = 0
        applicable = list(C.applicable_checks(category))
        report = {"asset_id": "x", "iteration": 1, "checks": [],
                  "checks_not_applicable": applicable, "overall_impression": "ok"}
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls += 1
                block = SimpleNamespace(type="tool_use", id="t1", input=report)
                return SimpleNamespace(content=[block], usage=None)

        self.messages = _Messages()


def _config(**overrides) -> dict:
    from assetpipe.pipeline_config import load_config
    cfg = load_config()
    cfg["toolchain"]["require_exact"] = False  # fake blender has no --version
    cfg["parallelism"]["assets"] = 2
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


def _write_batch(tmp_path, requests) -> "Path":
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(requests))
    return path


@pytest.fixture
def fake_blender(tmp_path):
    return _write_executable(tmp_path / "fake_blender.py", _FAKE_BLENDER_SRC)


def test_run_batch_happy_path_validates_asset(tmp_path, fake_blender):
    batch = _write_batch(tmp_path, [GOOD_REQUEST])
    client = FakeVisionClient()
    manifest = run_batch(batch, tmp_path / "runs", config=_config(),
                         blender_bin=str(fake_blender),
                         vision_client_factory=lambda: client)

    assert manifest["totals"] == {"validated": 1, "best_effort": 0,
                                  "hard_failed": 0, "intake_rejected": 0}
    entry = manifest["assets"]["scifi_crate_small_01"]
    assert entry["status"] == "validated"
    assert entry["shipped_iteration"] == 1
    assert client.calls >= 1

    run_root = tmp_path / "runs" / manifest["run_id"]
    run_dir = RunDir(run_root)
    assert run_dir.config_snapshot_path.exists()
    asset_dir = run_root / "scifi_crate_small_01"
    assert (asset_dir / "request.json").exists()
    assert (asset_dir / "final" / "asset.glb").exists()
    final_manifest = json.loads((asset_dir / "final" / "manifest.json").read_text())
    assert final_manifest["status"] == "validated"
    assert final_manifest["remaining_defects"] == []

    events = [json.loads(l)["event"]
              for l in (asset_dir / "history.jsonl").read_text().splitlines()]
    assert events[0] == "intake"
    assert "terminal" in events


def test_intake_rejection_consumes_zero_iterations(tmp_path, fake_blender):
    bad = dict(GOOD_REQUEST, asset_id="bad_profile_01", platform_profile="nonexistent")
    batch = _write_batch(tmp_path, [bad])
    manifest = run_batch(batch, tmp_path / "runs", config=_config(),
                         blender_bin=str(fake_blender),
                         vision_client_factory=FakeVisionClient)

    assert manifest["totals"]["intake_rejected"] == 1
    entry = manifest["assets"]["bad_profile_01"]
    assert entry["status"] == "intake_rejected"
    assert entry["errors"]
    run_root = tmp_path / "runs" / manifest["run_id"]
    assert not (run_root / "bad_profile_01" / "iter_01").exists()


def test_toolchain_mismatch_aborts_before_any_asset(tmp_path, fake_blender):
    cfg = _config()
    cfg["toolchain"]["require_exact"] = True  # fake blender fails --version probe
    batch = _write_batch(tmp_path, [GOOD_REQUEST])
    manifest = run_batch(batch, tmp_path / "runs", config=cfg,
                         blender_bin=str(fake_blender))

    assert manifest["aborted"] is True
    assert manifest["toolchain_errors"]
    assert manifest["assets"] == {}


def test_blender_failure_hard_fails_asset_but_batch_continues(tmp_path, fake_blender):
    always_fail = _write_executable(tmp_path / "always_fail.py",
                                    "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
    # Two assets: both go through the failing blender; each independently
    # lands hard_failed (proving one failure never stops the batch loop).
    second = dict(GOOD_REQUEST, asset_id="scifi_crate_small_02", seed=7)
    batch = _write_batch(tmp_path, [GOOD_REQUEST, second])
    manifest = run_batch(batch, tmp_path / "runs", config=_config(),
                         blender_bin=str(always_fail),
                         vision_client_factory=FakeVisionClient)

    assert manifest["totals"]["hard_failed"] == 2
    for asset_id in ("scifi_crate_small_01", "scifi_crate_small_02"):
        assert manifest["assets"][asset_id]["status"] == "hard_failed"
        assert manifest["assets"][asset_id]["error"]


def test_accepted_assets_marked_pending_before_running(tmp_path, fake_blender, monkeypatch):
    """A crash mid-run must leave 'pending' entries resume_run can find."""
    import assetpipe.orchestrator as orch

    seen = {}
    real = orch._run_one_asset

    def spying(request, run_dir, *args, **kwargs):
        manifest = json.loads(run_dir.run_manifest_path.read_text())
        seen[request["asset_id"]] = manifest["assets"][request["asset_id"]]["status"]
        return real(request, run_dir, *args, **kwargs)

    monkeypatch.setattr(orch, "_run_one_asset", spying)
    batch = _write_batch(tmp_path, [GOOD_REQUEST])
    run_batch(batch, tmp_path / "runs", config=_config(),
              blender_bin=str(fake_blender), vision_client_factory=FakeVisionClient)
    assert seen == {"scifi_crate_small_01": "pending"}


def test_resume_reruns_only_non_terminal_assets(tmp_path, fake_blender):
    # Manufacture an interrupted run: one asset pending with its request.json
    # on disk, one already validated.
    run_root = tmp_path / "runs" / "20260706T000000Z_deadbeef"
    run_dir = RunDir(run_root)
    run_dir.request_path("scifi_crate_small_01").write_text(json.dumps(GOOD_REQUEST))
    done = dict(GOOD_REQUEST, asset_id="already_done_01")
    run_dir.request_path("already_done_01").write_text(json.dumps(done))
    run_dir.run_manifest_path.write_text(json.dumps({
        "run_id": "20260706T000000Z_deadbeef",
        "totals": {"validated": 1, "best_effort": 0, "hard_failed": 0, "intake_rejected": 0},
        "assets": {"scifi_crate_small_01": {"status": "pending"},
                   "already_done_01": {"status": "validated"}},
    }))

    client = FakeVisionClient()
    manifest = resume_run(run_root, config=_config(), blender_bin=str(fake_blender),
                          vision_client_factory=lambda: client)

    assert manifest["assets"]["scifi_crate_small_01"]["status"] == "validated"
    assert manifest["assets"]["already_done_01"]["status"] == "validated"
    assert manifest["totals"]["validated"] == 2
    # only the pending asset actually ran
    assert (run_root / "scifi_crate_small_01" / "iter_01").exists()
    assert not (run_root / "already_done_01" / "iter_01").exists()
