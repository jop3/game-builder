"""CLI wiring (spec 20.2): every subcommand parses and delegates correctly.
The heavy paths (batch/generate/resume) are exercised end to end in
``test_orchestrator.py``; here they are wired through the real CLI against
the same fake blender + fake vision client, plus the cheap standalone
commands (validate/report/deliver) against fixture files.
"""
from __future__ import annotations

import json

import pytest

from assetpipe.cli import build_parser, main
from assetpipe.tests.test_glb import GOOD, make_glb
from assetpipe.tests.test_orchestrator import FakeVisionClient, GOOD_REQUEST, _config
from assetpipe.tests.test_stages import _FAKE_BLENDER_SRC, _write_executable


@pytest.fixture
def fake_blender(tmp_path):
    return _write_executable(tmp_path / "fake_blender.py", _FAKE_BLENDER_SRC)


def test_parser_covers_all_spec_20_2_commands():
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == {"generate", "batch", "validate", "render",
                                "inspect", "deliver", "resume", "report"}


def test_validate_passes_good_glb(tmp_path, capsys):
    glb_path = tmp_path / "asset.glb"
    glb_path.write_bytes(make_glb(GOOD, b"\x00" * 64))
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(GOOD_REQUEST))

    rc = main(["validate", "--glb", str(glb_path), "--request", str(request_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["verdict"] == "pass"
    assert any(c["check_id"] == "S20b" for c in out["checks"])


def test_validate_fails_oversized_or_bad_glb(tmp_path, capsys):
    glb_path = tmp_path / "asset.glb"
    glb_path.write_bytes(b"not a glb at all")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(GOOD_REQUEST))

    rc = main(["validate", "--glb", str(glb_path), "--request", str(request_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["verdict"] == "fail"


def test_batch_command_end_to_end(tmp_path, fake_blender, capsys, monkeypatch):
    import assetpipe.cli as cli
    monkeypatch.setattr(cli, "_vision_client_factory",
                        lambda cfg: FakeVisionClient)
    config_path = tmp_path / "pipeline.yaml"
    import yaml
    config_path.write_text(yaml.safe_dump(
        {"toolchain": {"require_exact": False}}))
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(json.dumps([GOOD_REQUEST]))

    rc = main(["batch", "--requests", str(batch_path), "--out", str(tmp_path / "runs"),
               "--blender-bin", str(fake_blender), "--config", str(config_path),
               "--parallel", "1"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["totals"]["validated"] == 1


def test_report_summarizes_run(tmp_path, capsys):
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "run_manifest.json").write_text(json.dumps({
        "run_id": "r1",
        "totals": {"validated": 1, "best_effort": 1, "hard_failed": 0, "intake_rejected": 0},
        "assets": {
            "a1": {"status": "validated"},
            "a2": {"status": "best_effort",
                   "remaining_defects": [{"check_id": "R4", "defect_type": "VISIBLE_SEAM"}]},
        },
    }))
    rc = main(["report", "--run", str(run_root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "a1: validated" in out
    assert "a2: best_effort (1 remaining defect(s))" in out


def test_deliver_marks_delivery_failed_on_verify_failure(tmp_path, capsys, monkeypatch):
    """CLI-level deliver flow with a stub adapter: delivery-verification
    failure marks the run manifest but exits nonzero (spec 18)."""
    import assetpipe.cli as cli
    from assetpipe.adapters.base import AdapterReport, DeliveryRecord

    run_root = tmp_path / "run"
    asset_dir = run_root / "a1" / "final"
    asset_dir.mkdir(parents=True)
    (asset_dir / "manifest.json").write_text(json.dumps(
        {"asset_id": "a1", "status": "validated", "category": "prop_small",
         "theme": "scifi_industrial", "container": "glb"}))
    (run_root / "run_manifest.json").write_text(json.dumps(
        {"assets": {"a1": {"status": "validated"}}}))

    class StubAdapter:
        name = "stub"

        def deliver(self, asset_dir, manifest, target_root):
            return DeliveryRecord(asset_id="a1", category="prop_small",
                                  theme="scifi_industrial", container="glb",
                                  delivered_paths=[], target_root=target_root,
                                  manifest=manifest)

        def verify(self, record):
            return AdapterReport(passed=False, errors=["import failed"], details={})

    monkeypatch.setattr(cli, "get_adapter", lambda *a, **k: StubAdapter(),
                        raising=False)
    monkeypatch.setattr("assetpipe.adapters.get_adapter", lambda *a, **k: StubAdapter())

    rc = main(["deliver", "--run", str(run_root), "--adapter", "stub",
               "--project", str(tmp_path / "proj")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["results"]["a1"] == {"delivered": True, "verified": False,
                                    "errors": ["import failed"]}
    manifest = json.loads((run_root / "run_manifest.json").read_text())
    assert manifest["assets"]["a1"]["delivery_failed"] is True
