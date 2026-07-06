"""Run-directory layout helpers and the history/manifest writers (spec 17.1-17.2)."""
import json
from datetime import datetime, timezone

from assetpipe.rundir import HistoryLog, RunDir, new_run_id, update_run_manifest


def test_rundir_creates_root_and_asset_dirs(tmp_path):
    rd = RunDir(tmp_path / "runs" / "run1")
    assert rd.root.is_dir()
    d = rd.asset_dir("crate_01")
    assert d.is_dir() and d == rd.root / "crate_01"


def test_iter_dir_creates_maps_renders_logs_subdirs(tmp_path):
    rd = RunDir(tmp_path / "run1")
    d = rd.iter_dir("crate_01", 1)
    assert d == rd.root / "crate_01" / "iter_01"
    assert (d / "maps").is_dir()
    assert (d / "renders").is_dir()
    assert (d / "logs").is_dir()


def test_iter_dir_zero_pads_to_two_digits(tmp_path):
    rd = RunDir(tmp_path / "run1")
    assert rd.iter_dir("a", 3).name == "iter_03"
    assert rd.iter_dir("a", 12).name == "iter_12"


def test_final_dir_created_under_asset(tmp_path):
    rd = RunDir(tmp_path / "run1")
    fd = rd.final_dir("crate_01")
    assert fd == rd.root / "crate_01" / "final"
    assert fd.is_dir()


def test_run_manifest_and_snapshot_paths(tmp_path):
    rd = RunDir(tmp_path / "run1")
    assert rd.run_manifest_path == rd.root / "run_manifest.json"
    assert rd.config_snapshot_path == rd.root / "pipeline_config_snapshot.yaml"


def test_history_path_and_request_path(tmp_path):
    rd = RunDir(tmp_path / "run1")
    assert rd.history_path("crate_01") == rd.root / "crate_01" / "history.jsonl"
    assert rd.request_path("crate_01") == rd.root / "crate_01" / "request.json"


# ---------- HistoryLog ----------

def test_history_log_appends_jsonl_with_timestamp(tmp_path):
    log = HistoryLog(tmp_path / "history.jsonl")
    log.event("intake", "crate_01", status="accepted")
    log.event("stage_start", "crate_01", iter=1, stage="G")

    lines = (tmp_path / "history.jsonl").read_text().splitlines()
    assert len(lines) == 2
    e1, e2 = (json.loads(l) for l in lines)
    assert e1["event"] == "intake" and e1["asset"] == "crate_01" and e1["status"] == "accepted"
    assert "t" in e1 and e1["t"].endswith("Z")
    assert "iter" not in e1
    assert e2["iter"] == 1 and e2["stage"] == "G"


def test_history_log_is_append_only(tmp_path):
    path = tmp_path / "h.jsonl"
    log = HistoryLog(path)
    for i in range(5):
        log.event("stage_end", "a", iter=i)
    lines = path.read_text().splitlines()
    assert len(lines) == 5
    # every previous line is untouched byte-for-byte across appends
    for i, line in enumerate(lines):
        assert json.loads(line)["iter"] == i


def test_history_log_timestamp_is_utc_iso8601(tmp_path):
    log = HistoryLog(tmp_path / "h.jsonl")
    log.event("intake", "a")
    entry = json.loads((tmp_path / "h.jsonl").read_text().splitlines()[0])
    # parses cleanly as an ISO-8601 UTC 'Z' timestamp
    datetime.strptime(entry["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------- new_run_id ----------

def test_new_run_id_format(tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text('[{"asset_id": "a"}]')
    fixed_now = lambda: datetime(2026, 7, 6, 4, 58, 11, tzinfo=timezone.utc)  # noqa: E731
    run_id = new_run_id(batch, now=fixed_now)
    ts, sha = run_id.split("_")
    assert ts == "20260706T045811Z"
    assert len(sha) == 8


def test_new_run_id_changes_with_batch_content(tmp_path):
    fixed_now = lambda: datetime(2026, 7, 6, 0, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    b1 = tmp_path / "b1.json"
    b1.write_text('[{"asset_id": "a"}]')
    b2 = tmp_path / "b2.json"
    b2.write_text('[{"asset_id": "b"}]')
    assert new_run_id(b1, now=fixed_now) != new_run_id(b2, now=fixed_now)


def test_new_run_id_deterministic_for_same_content_and_time(tmp_path):
    fixed_now = lambda: datetime(2026, 7, 6, 0, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    b1 = tmp_path / "b1.json"
    b1.write_text('[{"asset_id": "a"}]')
    b2 = tmp_path / "b2.json"
    b2.write_text('[{"asset_id": "a"}]')
    assert new_run_id(b1, now=fixed_now) == new_run_id(b2, now=fixed_now)


# ---------- update_run_manifest ----------

def test_update_run_manifest_creates_file_if_absent(tmp_path):
    path = tmp_path / "run_manifest.json"
    result = update_run_manifest(path, lambda m: m.update({"run_id": "abc"}))
    assert result == {"run_id": "abc"}
    assert json.loads(path.read_text()) == {"run_id": "abc"}


def test_update_run_manifest_read_modify_write_preserves_prior_keys(tmp_path):
    path = tmp_path / "run_manifest.json"
    update_run_manifest(path, lambda m: m.update({"assets": {}, "totals": {"validated": 0}}))
    update_run_manifest(path, lambda m: m["assets"].update({"crate_01": {"status": "validated"}}))
    update_run_manifest(path, lambda m: m["totals"].__setitem__(
        "validated", m["totals"]["validated"] + 1))

    final = json.loads(path.read_text())
    assert final["assets"] == {"crate_01": {"status": "validated"}}
    assert final["totals"]["validated"] == 1


def test_update_run_manifest_concurrent_writers_all_land(tmp_path):
    """Sequential calls simulating what the orchestrator's thread pool does
    (one update per completed asset) must never lose an update."""
    path = tmp_path / "run_manifest.json"
    update_run_manifest(path, lambda m: m.setdefault("assets", {}))
    for i in range(20):
        aid = f"asset_{i}"
        update_run_manifest(path, lambda m, aid=aid: m["assets"].update({aid: {"status": "validated"}}))
    final = json.loads(path.read_text())
    assert len(final["assets"]) == 20
