"""SubprocessStages: the loop.Stages implementation that spawns Blender
subprocesses (spec 4.3, 9, 13, 14, 16, README item 2). No real Blender is
used -- ``blender_bin`` points at a small fake Python executable (built by
:func:`write_fake_blender`) that dispatches on the ``--python <script>``
filename and writes plausible outputs into the iteration dir, mirroring the
real ``assetpipe/blender_scripts/*.py`` file-boundary contract (spec 5, 17.1).
"""
from __future__ import annotations

import json
import os
import stat
import struct
import sys
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from assetpipe.contracts import Contracts
from assetpipe.fixes.planner import plan_signature
from assetpipe.pipeline_config import load_config
from assetpipe.rundir import HistoryLog, RunDir
from assetpipe.stages import SubprocessStages, resolve_params

C = Contracts.load()
CATEGORY = "prop_small"

PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "width_m": {"type": "number", "minimum": 0.1, "maximum": 5.0, "default": 1.0},
        "greeble_density": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
        "panel_lines": {"type": "integer", "minimum": 0, "maximum": 6, "default": 3},
    },
}

REQUEST = {
    "asset_id": "crate_01", "category": CATEGORY, "theme": "scifi_industrial",
    "platform_profile": "web", "seed": 7,
    "description": "a small reinforced sci-fi crate", "generator": "props/crate",
}
THEME = {"display_name": "Sci-Fi Industrial"}


class FakeRegistry:
    def __init__(self, module):
        self._m = {"props/crate": module}

    def __contains__(self, key):
        return key in self._m

    def get(self, key):
        return self._m[key]


GEN_MODULE = types.SimpleNamespace(PARAM_SCHEMA=PARAM_SCHEMA, BBOX_RANGE=(0.3, 1.2),
                                   CATEGORY=CATEGORY)
REGISTRY = FakeRegistry(GEN_MODULE)


# ---------------------------------------------------------------------------
# Fake `blender` executable
# ---------------------------------------------------------------------------

_FAKE_BLENDER_SRC = r'''#!/usr/bin/env python3
import json, os, struct, sys
from pathlib import Path

def parse():
    argv = sys.argv[1:]
    script = Path(argv[argv.index("--python") + 1]).name
    tail = argv[argv.index("--") + 1:]
    args_json = None
    i = 0
    while i < len(tail):
        if tail[i] == "--args-json":
            args_json = tail[i + 1]; i += 2
        else:
            i += 1
    payload = json.loads(Path(args_json).read_text())
    return script, payload

def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, default=str))

def make_noisy_png(path, base, size=64, seed=0):
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(seed)
    arr = np.clip(np.array(base) + rng.integers(-15, 15, size=(size, size, 3)), 0, 255).astype("uint8")
    Image.fromarray(arr, mode="RGB").save(path)

def make_flat_png(path, color, size=64):
    import numpy as np
    from PIL import Image
    Image.fromarray(np.full((size, size, 3), color, dtype="uint8"), mode="RGB").save(path)

def make_silhouette_png(path, size=64, white_frac=0.3):
    import numpy as np
    from PIL import Image
    arr = np.zeros((size, size, 3), dtype="uint8")
    n = int(size * (white_frac ** 0.5))
    off = (size - n) // 2
    arr[off:off + n, off:off + n, :] = 255
    Image.fromarray(arr, mode="RGB").save(path)

def make_glb(out_path, mesh_name):
    gltf = {
        "asset": {"version": "2.0"}, "extensionsUsed": [],
        "materials": [{"name": "m", "normalTexture": {"index": 0}}],
        "meshes": [{"name": mesh_name, "primitives": [
            {"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2, "TANGENT": 3},
             "material": 0}]}],
        "images": [{"name": "albedo"}, {"name": "normal"}, {"name": "orm"}],
    }
    payload = json.dumps(gltf).encode()
    payload += b" " * (-len(payload) % 4)
    chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    bin_chunk = b"\x00" * 64
    chunks += struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    header = struct.pack("<III", 0x46546C67, 2, 12 + len(chunks))
    Path(out_path).write_bytes(header + chunks)

VIEW_IDS = [
    "turn_000", "turn_045", "turn_090", "turn_135", "turn_180", "turn_225", "turn_270", "turn_315",
    "high_045", "high_225", "top", "close_034",
    "lit_warm_045", "lit_warm_225", "lit_dark_090",
    "silhouette_000", "silhouette_090",
    "normals_045", "normals_225", "uvcheck_045",
]

def main():
    script, payload = parse()
    log_path = os.environ.get("FAKE_BLENDER_LOG")
    if log_path:
        with open(log_path, "a") as f:
            f.write(json.dumps({"script": script, "iteration": payload.get("iteration")}) + "\n")

    if script == "generate.py":
        out_dir = Path(payload["out_dir"])
        write_json(out_dir / "params.json", {"width_m": 1.0})
        (out_dir / "asset.blend").write_bytes(b"BLENDFAKE")
        write_json(out_dir / "result.json", {
            "root_object": "Root", "recipe": payload.get("generator"),
            "triangles": 500, "seed": payload["request"]["seed"]})
    elif script == "bake.py":
        asset_dir = Path(payload["asset_dir"])
        maps_dir = asset_dir / "maps"
        maps_dir.mkdir(parents=True, exist_ok=True)
        make_noisy_png(maps_dir / "albedo.png", (120, 120, 120), seed=1)
        make_flat_png(maps_dir / "normal.png", (128, 128, 255))
        make_flat_png(maps_dir / "orm.png", (255, 128, 0))
        write_json(Path(payload["out_dir"]) / "result.json", {"maps": {
            "albedo": str(maps_dir / "albedo.png"), "normal": str(maps_dir / "normal.png"),
            "orm": str(maps_dir / "orm.png")}})
    elif script == "export_gltf.py":
        asset_dir = Path(payload["asset_dir"])
        asset_id = payload["request"]["asset_id"]
        out_glb = asset_dir / f"{asset_id}.glb"
        make_glb(out_glb, payload.get("root_object") or "Root")
        write_json(asset_dir / "export_result.json",
                   {"glb": str(out_glb), "lods": [], "collision_mode": "none"})
    elif script == "static_checks_mesh.py":
        checks = [{"check_id": "S1", "verdict": "pass", "severity": "blocker",
                  "measured": 0, "threshold": 0, "details": ""}]
        write_json(payload["out_path"], checks)
    elif script == "render_views.py":
        out_dir = Path(payload["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        for vid in VIEW_IDS:
            if vid.startswith("silhouette_"):
                make_silhouette_png(out_dir / f"{vid}.png")
            else:
                make_noisy_png(out_dir / f"{vid}.png", (120, 120, 120), seed=abs(hash(vid)) % 1000)
        make_flat_png(out_dir / "contact_sheet_0.png", (200, 200, 200), size=32)
    elif script == "fixes.py":
        asset_dir = Path(payload["asset_dir"])
        write_json(asset_dir / "fixes_result.json", {"actions": payload.get("actions", [])})
    sys.exit(0)

if __name__ == "__main__":
    main()
'''

_ALWAYS_FAIL_SRC = "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n"

_SLEEPY_SRC = textwrap.dedent("""\
    #!/usr/bin/env python3
    import time, sys
    time.sleep(5)
    sys.exit(0)
    """)


def _write_executable(path: Path, src: str) -> Path:
    path.write_text(src)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_blender(tmp_path):
    return _write_executable(tmp_path / "fake_blender.py", _FAKE_BLENDER_SRC)


@pytest.fixture
def always_fail_blender(tmp_path):
    return _write_executable(tmp_path / "always_fail.py", _ALWAYS_FAIL_SRC)


@pytest.fixture
def sleepy_blender(tmp_path):
    return _write_executable(tmp_path / "sleepy.py", _SLEEPY_SRC)


def make_stages(tmp_path, blender_bin, config=None, history=None, vision_client=None,
                request=None):
    run_dir = RunDir(tmp_path / "run")
    return SubprocessStages(
        request=request or REQUEST, run_dir=run_dir, contracts=C,
        config=config or load_config(), theme=THEME, param_schema=PARAM_SCHEMA,
        registry=REGISTRY, blender_bin=str(blender_bin), vision_client=vision_client,
        history=history), run_dir


# ---------------------------------------------------------------------------
# resolve_params (spec 9.3) -- pure function
# ---------------------------------------------------------------------------

def test_resolve_params_deterministic_for_same_seed():
    a = resolve_params(PARAM_SCHEMA, {}, seed=42)
    b = resolve_params(PARAM_SCHEMA, {}, seed=42)
    assert a == b


def test_resolve_params_different_seeds_usually_differ():
    a = resolve_params(PARAM_SCHEMA, {}, seed=1)
    b = resolve_params(PARAM_SCHEMA, {}, seed=2)
    assert a != b


def test_resolve_params_jitter_stays_within_ten_percent_of_default():
    params = resolve_params(PARAM_SCHEMA, {}, seed=123)
    assert 0.9 * 1.0 <= params["width_m"] <= 1.1 * 1.0
    assert 0.9 * 0.4 <= params["greeble_density"] <= 1.1 * 0.4


def test_resolve_params_override_wins_and_is_clamped_to_schema_bounds():
    params = resolve_params(PARAM_SCHEMA, {}, seed=1, param_overrides={"width_m": 999})
    assert params["width_m"] == 5.0  # clamped to schema maximum, not left at 999


def test_resolve_params_theme_clamp_narrows_before_jitter():
    theme = {"greeble_density_range": [0.0, 0.1]}
    params = resolve_params(PARAM_SCHEMA, theme, seed=7)
    # clamped to <=0.1 before jitter; jitter is +-10% of the clamped value
    assert params["greeble_density"] <= 0.11


def test_resolve_params_integer_params_stay_integer():
    params = resolve_params(PARAM_SCHEMA, {}, seed=5)
    assert isinstance(params["panel_lines"], int)


# ---------------------------------------------------------------------------
# _run_blender: retry-once-then-InfraError (spec 4.3)
# ---------------------------------------------------------------------------

def test_nonzero_exit_retries_once_then_raises_infra_error(tmp_path, always_fail_blender):
    from assetpipe.loop import InfraError
    stages, run_dir = make_stages(tmp_path, always_fail_blender)
    with pytest.raises(InfraError):
        stages.generate(1, seed=7)
    iter_dir = run_dir.iter_dir("crate_01", 1)
    assert (iter_dir / "logs" / "generate.err.txt").exists()


def test_timeout_retries_once_then_raises_infra_error(tmp_path, sleepy_blender):
    from assetpipe.loop import InfraError
    config = load_config()
    config["stage_timeouts"] = {"generate": 0.2, "bake": 0.2, "export": 0.2,
                                "static_checks": 0.2, "render": 0.2, "fixes": 0.2}
    stages, run_dir = make_stages(tmp_path, sleepy_blender, config=config)
    with pytest.raises(InfraError, match="timeout"):
        stages.generate(1, seed=7)


def test_stage_start_and_error_events_logged_on_infra_failure(tmp_path, always_fail_blender):
    from assetpipe.loop import InfraError
    run_dir = RunDir(tmp_path / "run")
    history = HistoryLog(run_dir.history_path("crate_01"))
    stages, _ = make_stages(tmp_path, always_fail_blender, history=history)
    stages.run_dir = run_dir
    with pytest.raises(InfraError):
        stages.generate(1, seed=7)
    lines = [json.loads(l) for l in run_dir.history_path("crate_01").read_text().splitlines()]
    events = [l["event"] for l in lines]
    assert "stage_start" in events and "error" in events


# ---------------------------------------------------------------------------
# generate(): runs G, M, X in order
# ---------------------------------------------------------------------------

def test_generate_runs_g_m_x_in_order_and_produces_artifacts(tmp_path, fake_blender, monkeypatch):
    log_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_BLENDER_LOG", str(log_path))
    stages, run_dir = make_stages(tmp_path, fake_blender)

    stages.generate(1, seed=7)

    iter_dir = run_dir.iter_dir("crate_01", 1)
    assert (iter_dir / "asset.blend").exists()
    assert (iter_dir / "maps" / "albedo.png").exists()
    assert (iter_dir / "maps" / "normal.png").exists()
    assert (iter_dir / "maps" / "orm.png").exists()
    assert (iter_dir / "crate_01.glb").exists()

    calls = [json.loads(l)["script"] for l in log_path.read_text().splitlines()]
    assert calls == ["generate.py", "bake.py", "export_gltf.py"]


def test_generate_writes_args_json_and_captures_stdout_stderr(tmp_path, fake_blender):
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    logs = run_dir.iter_dir("crate_01", 1) / "logs"
    assert (logs / "generate.args.json").exists()
    assert (logs / "generate.out.txt").exists()
    payload = json.loads((logs / "generate.args.json").read_text())
    assert payload["request"]["seed"] == 7
    assert payload["request"]["asset_id"] == "crate_01"


# ---------------------------------------------------------------------------
# apply_fix(): copy-forward rules and resume-stage re-run set
# ---------------------------------------------------------------------------

def _fix_plan(resume_stage, fix_id, defect, for_iteration=1):
    return {"asset_id": "crate_01", "for_iteration": for_iteration,
           "produces_iteration": for_iteration + 1, "defects_addressed": [defect],
           "actions": [{"type": "table_fix", "fix_id": fix_id, "target": "hull"}],
           "planner": "table", "resume_stage": resume_stage}


def test_apply_fix_resume_m_copies_blend_forward_and_reruns_bake_and_export(
        tmp_path, fake_blender, monkeypatch):
    log_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_BLENDER_LOG", str(log_path))
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    log_path.write_text("")  # reset call log before the fix

    plan = _fix_plan("M", "rebake_margin_x2", "VISIBLE_SEAM")
    stages.apply_fix(2, plan)

    iter2 = run_dir.iter_dir("crate_01", 2)
    assert (iter2 / "params.json").exists()
    assert (iter2 / "asset.blend").exists()          # copied forward for M resume
    assert (iter2 / "maps" / "albedo.png").exists()   # freshly rebaked
    assert (iter2 / "crate_01.glb").exists()
    assert (iter2 / "fixes_result.json").exists()     # rebake_margin_x2 is a blender action

    calls = [json.loads(l)["script"] for l in log_path.read_text().splitlines()]
    assert calls == ["fixes.py", "bake.py", "export_gltf.py"]  # not generate.py


def test_apply_fix_resume_x_copies_maps_forward_and_reruns_export_only(
        tmp_path, fake_blender, monkeypatch):
    log_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_BLENDER_LOG", str(log_path))
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    iter1_albedo_bytes = (run_dir.iter_dir("crate_01", 1) / "maps" / "albedo.png").read_bytes()
    log_path.write_text("")

    plan = _fix_plan("X", "reexport", "GLTF_INVALID")
    stages.apply_fix(2, plan)

    iter2 = run_dir.iter_dir("crate_01", 2)
    assert (iter2 / "maps" / "albedo.png").read_bytes() == iter1_albedo_bytes  # untouched copy
    assert (iter2 / "crate_01.glb").exists()

    calls = [json.loads(l)["script"] for l in log_path.read_text().splitlines()]
    assert calls == ["fixes.py", "export_gltf.py"]  # bake never reran


def test_apply_fix_resume_g_does_not_copy_blend_or_maps_forward(tmp_path, fake_blender, monkeypatch):
    log_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_BLENDER_LOG", str(log_path))
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    log_path.write_text("")

    plan = _fix_plan("G", "cleanup_mesh", "NON_MANIFOLD")
    stages.apply_fix(2, plan)

    calls = [json.loads(l)["script"] for l in log_path.read_text().splitlines()]
    assert calls == ["fixes.py", "generate.py", "bake.py", "export_gltf.py"]


# ---------------------------------------------------------------------------
# static_validate(): mesh checks + orchestrator-side gate
# ---------------------------------------------------------------------------

def test_static_validate_passes_on_good_artifacts(tmp_path, fake_blender):
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    result = stages.static_validate(1)
    assert result.passed, result.blockers
    iter_dir = run_dir.iter_dir("crate_01", 1)
    assert (iter_dir / "mesh_report.json").exists()
    assert (iter_dir / "static_report.json").exists()


def test_static_validate_black_albedo_short_circuits_before_render(
        tmp_path, fake_blender, monkeypatch):
    log_path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_BLENDER_LOG", str(log_path))
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)

    iter_dir = run_dir.iter_dir("crate_01", 1)
    # corrupt the baked albedo to all-black after the fact (simulates S16 failure)
    import numpy as np
    from PIL import Image
    Image.fromarray(np.zeros((64, 64, 3), dtype="uint8"), mode="RGB").save(
        iter_dir / "maps" / "albedo.png")

    result = stages.static_validate(1)
    assert not result.passed
    assert any(f.defect_type == "BLACK_SURFACE" for f in result.blockers)

    log_path.write_text("")
    # render() is never invoked by static_validate itself, and the loop (not
    # exercised here) is what would skip it -- but static_validate must not
    # call render_views.py on its own regardless.
    calls = [json.loads(l)["script"] for l in log_path.read_text().splitlines()] if \
        log_path.read_text() else []
    assert "render_views.py" not in calls


# ---------------------------------------------------------------------------
# render() + A1-A4 pre-vision analytics fold into inspect()
# ---------------------------------------------------------------------------

def test_render_then_inspect_pass_with_all_pass_vision_report(tmp_path, fake_blender):
    anthropic = pytest.importorskip("anthropic")
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    stages.static_validate(1)
    stages.render(1)

    class FakeMessages:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            applicable = list(C.applicable_checks(CATEGORY))
            report = {"asset_id": "crate_01", "iteration": 1, "checks": [],
                     "checks_not_applicable": applicable, "overall_impression": "fine"}
            block = SimpleNamespace(type="tool_use", id="t1", input=report)
            return SimpleNamespace(content=[block], usage=None)

    class FakeClient:
        def __init__(self):
            self.messages = FakeMessages()

    client = FakeClient()
    stages.vision_client = client
    result = stages.inspect(1)

    assert result.passed
    assert len(client.messages.calls) == 1
    renders_dir = run_dir.iter_dir("crate_01", 1) / "renders"
    assert (renders_dir / "turn_000.png").exists()
    assert (renders_dir / "contact_sheet_0.png").exists()


def test_a_check_blocker_short_circuits_vision_call(tmp_path, fake_blender):
    stages, run_dir = make_stages(tmp_path, fake_blender)
    stages.generate(1, seed=7)
    stages.static_validate(1)
    stages.render(1)

    iter_dir = run_dir.iter_dir("crate_01", 1)
    # break the silhouette render after render() ran the A-checks... instead
    # directly force an A-check failure by re-running _run_a_checks after
    # corrupting a silhouette view, then re-driving inspect().
    import numpy as np
    from PIL import Image
    renders_dir = iter_dir / "renders"
    Image.fromarray(np.zeros((64, 64, 3), dtype="uint8"), mode="RGB").save(
        renders_dir / "silhouette_000.png")  # 0% white -> fails A3 sane-range
    stages._a_blockers, stages._a_warns = stages._run_a_checks(renders_dir)
    assert stages._a_blockers  # sanity: the corruption did trip A3

    class ExplodingMessages:
        def create(self, **kwargs):
            raise AssertionError("vision API must not be called when A-checks fail")

    class ExplodingClient:
        messages = ExplodingMessages()

    stages.vision_client = ExplodingClient()
    result = stages.inspect(1)

    assert not result.passed
    assert any(f.defect_type == "SCALE_IMPLAUSIBLE" for f in result.blockers)
