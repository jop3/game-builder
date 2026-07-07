"""Vision inspector: API call shape, retry policy, and uncertain resolution
(spec 15, 17.2, 22). Uses a fake client -- no network, no API key."""
from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.loop import InfraError, StageResult
from assetpipe.vision.inspector import inspect_asset

anthropic = pytest.importorskip("anthropic")

C = Contracts.load()
CATEGORY = "prop_small"
APPLICABLE = list(C.applicable_checks(CATEGORY))

REQUEST = {
    "asset_id": "scifi_crate_small_01", "category": CATEGORY,
    "theme": "scifi_industrial", "seed": 1,
    "description": "A small reinforced sci-fi supply crate with glowing status strip",
}
THEME = {
    "display_name": "Sci-Fi Industrial",
    "palette": {"primary": ["#2E3A46"], "secondary": ["#8C959D"],
                "accent": ["#00C2A8"], "emissive": ["#FFD24A"], "forbidden": ["#8B4513"]},
    "silhouette_language": "Chamfered boxes, panel lines, greebles.",
    "vision_style_brief": "Functional industrial sci-fi.",
}
CONFIG = {"vision": {"model": "claude-fable-5", "temperature": 0, "max_retries": 1,
                     "max_recheck_rounds": 1, "max_concurrent_calls": 4}}


# ---------- fixtures / doubles ----------

class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def make_response(report, tool_use_id="toolu_1", usage=(100, 50)):
    block = SimpleNamespace(type="tool_use", id=tool_use_id, input=report)
    u = SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]) if usage else None
    return SimpleNamespace(content=[block], usage=u)


def _status_error(code, message="server error"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status_code=code, request=req)
    return anthropic.APIStatusError(message, response=resp, body=None)


def _connection_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=req)


def _entry(cid, verdict="pass", defect=None, views=(), location="", confidence=0.95,
          description=""):
    e = {"check_id": cid, "verdict": verdict, "confidence": confidence,
        "evidence_views": list(views), "location": location, "description": description}
    if defect is not None:
        e["defect_type"] = defect
    return e


def _report(entries, category=CATEGORY, not_applicable=None):
    covered = {e["check_id"] for e in entries}
    na = not_applicable if not_applicable is not None else \
        [c for c in C.applicable_checks(category) if c not in covered]
    return {"asset_id": REQUEST["asset_id"], "iteration": 1, "checks": entries,
            "checks_not_applicable": na, "overall_impression": "fine"}


def _all_pass_report(category=CATEGORY):
    return _report([_entry(cid) for cid in C.applicable_checks(category)], category)


def _png(path, size=(64, 64), color=(120, 130, 140)):
    Image.new("RGB", size, color).save(path)


def _stub_render_views(renders_dir, view_ids):
    renders_dir.mkdir(parents=True, exist_ok=True)
    for v in view_ids:
        _png(renders_dir / f"{v}.png")


def _contact_sheets(tmp_path, n=2):
    paths = []
    for i in range(n):
        p = tmp_path / f"sheet_{i}.png"
        _png(p, size=(32, 32))
        paths.append(p)
    return paths


def _no_sleep(_seconds):
    pass


# ---------- happy path ----------

def test_all_pass_report_yields_passed_stage_result(tmp_path):
    client = FakeClient([make_response(_all_pass_report())])
    sheets = _contact_sheets(tmp_path)
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                           contracts=C, config=CONFIG, sleep=_no_sleep)

    assert isinstance(result, StageResult)
    assert result.passed and not result.blockers and not result.warns
    assert len(client.messages.calls) == 1


def test_blocker_fail_yields_not_passed_with_finding(tmp_path):
    entries = [_entry(cid) for cid in APPLICABLE if cid != "R4"]
    entries.append(_entry("R4", verdict="fail", defect="VISIBLE_SEAM",
                          views=("close_034", "turn_045"), location="front edge"))
    client = FakeClient([make_response(_report(entries))])
    sheets = _contact_sheets(tmp_path)
    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=tmp_path / "renders",
                           iteration=1, contracts=C, config=CONFIG, sleep=_no_sleep)
    assert not result.passed
    assert len(result.blockers) == 1
    assert result.blockers[0].check_id == "R4"
    assert result.blockers[0].defect_type == "VISIBLE_SEAM"


# ---------- semantic validation retry ----------

def test_semantically_invalid_report_then_valid_on_retry_succeeds(tmp_path):
    bad = _all_pass_report()
    bad["checks"].pop()  # drop a check's entry entirely -> missing from report -> invalid
    good = _all_pass_report()
    client = FakeClient([make_response(bad, tool_use_id="toolu_bad"),
                        make_response(good, tool_use_id="toolu_good")])
    sheets = _contact_sheets(tmp_path)

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=tmp_path / "renders",
                           iteration=1, contracts=C, config=CONFIG, sleep=_no_sleep)

    assert result.passed
    assert len(client.messages.calls) == 2
    second_kwargs = client.messages.calls[1]
    msgs = second_kwargs["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "user"
    tool_result = msgs[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_bad"
    assert tool_result["is_error"] is True


def test_semantically_invalid_twice_raises_infra_error(tmp_path):
    bad1 = _all_pass_report()
    bad1["checks"].pop()
    bad2 = _all_pass_report()
    bad2["checks"].pop()
    client = FakeClient([make_response(bad1), make_response(bad2)])
    sheets = _contact_sheets(tmp_path)

    with pytest.raises(InfraError):
        inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                     contact_sheets=sheets, renders_dir=tmp_path / "renders",
                     iteration=1, contracts=C, config=CONFIG, sleep=_no_sleep)
    assert len(client.messages.calls) == 2


# ---------- uncertain resolution ----------

def test_uncertain_recheck_uses_four_crops_and_resolves_to_pass(tmp_path):
    entries = [_entry(cid) for cid in APPLICABLE if cid != "R4"]
    entries.append(_entry("R4", verdict="uncertain", defect="VISIBLE_SEAM",
                          views=("close_034",), description="maybe a seam"))
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["close_034"])

    recheck_report = {"checks": [_entry("R4", verdict="pass")]}
    client = FakeClient([make_response(_report(entries)),
                        make_response(recheck_report)])
    sheets = _contact_sheets(tmp_path)

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                           contracts=C, config=CONFIG, sleep=_no_sleep)

    assert result.passed
    assert len(client.messages.calls) == 2
    recheck_kwargs = client.messages.calls[1]
    content = recheck_kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if b["type"] == "image"]
    text_blocks = [b for b in content if b["type"] == "text"]
    assert len(image_blocks) == 4
    assert len(text_blocks) == 1
    assert "[R4]" in text_blocks[0]["text"]
    assert recheck_kwargs["tools"][0]["input_schema"] == C.report_tool_schema(CATEGORY)


def test_uncertain_still_uncertain_after_recheck_becomes_blocker_fail(tmp_path):
    entries = [_entry(cid) for cid in APPLICABLE if cid != "R4"]
    entries.append(_entry("R4", verdict="uncertain", defect="VISIBLE_SEAM",
                          views=("close_034",)))
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["close_034"])

    recheck_report = {"checks": [_entry("R4", verdict="uncertain", defect="VISIBLE_SEAM")]}
    client = FakeClient([make_response(_report(entries)),
                        make_response(recheck_report)])
    sheets = _contact_sheets(tmp_path)

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                           contracts=C, config=CONFIG, sleep=_no_sleep)

    assert not result.passed
    assert len(result.blockers) == 1
    assert result.blockers[0].verdict == "fail"
    assert result.blockers[0].confidence == 0.5
    assert len(client.messages.calls) == 2  # single recheck round, no third call


def test_two_view_rule_fail_with_one_view_flows_through_uncertain_path(tmp_path):
    # R3 requires 2 distinct views for a fail; only one is cited here, so the
    # orchestrator must downgrade to 'uncertain' and run the recheck round.
    entries = [_entry(cid) for cid in APPLICABLE if cid != "R3"]
    entries.append(_entry("R3", verdict="fail", defect="INVERTED_NORMALS",
                          views=("normals_045",), location="left panel"))
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["normals_045"])

    recheck_report = {"checks": [_entry("R3", verdict="pass")]}
    client = FakeClient([make_response(_report(entries)),
                        make_response(recheck_report)])
    sheets = _contact_sheets(tmp_path)

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                           contracts=C, config=CONFIG, sleep=_no_sleep)

    assert result.passed
    assert len(client.messages.calls) == 2
    recheck_text = client.messages.calls[1]["messages"][0]["content"][-1]["text"]
    assert "[R3]" in recheck_text


def test_recheck_resolves_view_from_check_pattern_when_no_evidence_view(tmp_path):
    # No evidence_views cited -> fall back to a rendered view matching one of
    # R4's declared view patterns (close_034 is a concrete pattern).
    entries = [_entry(cid) for cid in APPLICABLE if cid != "R4"]
    entries.append(_entry("R4", verdict="uncertain", defect="VISIBLE_SEAM", views=()))
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["close_034"])

    recheck_report = {"checks": [_entry("R4", verdict="pass")]}
    client = FakeClient([make_response(_report(entries)),
                        make_response(recheck_report)])
    sheets = _contact_sheets(tmp_path)

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                           contracts=C, config=CONFIG, sleep=_no_sleep)
    assert result.passed
    assert len(client.messages.calls) == 2


# ---------- API error / retry policy (spec 22) ----------

def test_api_500_twice_then_success_succeeds_with_backoff_sleeps(tmp_path):
    client = FakeClient([_status_error(500), _status_error(500),
                        make_response(_all_pass_report())])
    sheets = _contact_sheets(tmp_path)
    sleeps: list[float] = []

    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=tmp_path / "renders",
                           iteration=1, contracts=C, config=CONFIG, sleep=sleeps.append)

    assert result.passed
    assert sleeps == [5, 30]
    assert len(client.messages.calls) == 3


def test_persistent_connection_errors_raise_infra_error(tmp_path):
    client = FakeClient([_connection_error(), _connection_error(), _connection_error()])
    sheets = _contact_sheets(tmp_path)
    sleeps: list[float] = []

    with pytest.raises(InfraError):
        inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                     contact_sheets=sheets, renders_dir=tmp_path / "renders",
                     iteration=1, contracts=C, config=CONFIG, sleep=sleeps.append)
    assert sleeps == [5, 30]
    assert len(client.messages.calls) == 3


def test_non_retryable_api_error_raises_infra_error_immediately(tmp_path):
    client = FakeClient([_status_error(400, "bad request")])
    sheets = _contact_sheets(tmp_path)
    sleeps: list[float] = []

    with pytest.raises(InfraError):
        inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                     contact_sheets=sheets, renders_dir=tmp_path / "renders",
                     iteration=1, contracts=C, config=CONFIG, sleep=sleeps.append)
    assert sleeps == []
    assert len(client.messages.calls) == 1


# ---------- logging (spec 17.2) ----------

def test_log_file_written_without_image_bytes(tmp_path):
    sheets = _contact_sheets(tmp_path)
    sheet_bytes = sheets[0].read_bytes()
    sheet_b64 = base64.b64encode(sheet_bytes).decode("ascii")
    log_path = tmp_path / "vision_call.json"

    client = FakeClient([make_response(_all_pass_report())])
    inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                 contact_sheets=sheets, renders_dir=tmp_path / "renders", iteration=1,
                 contracts=C, config=CONFIG, log_path=log_path, sleep=_no_sleep)

    text = log_path.read_text()
    assert sheet_b64 not in text
    lines = [json.loads(l) for l in text.splitlines() if l.strip()]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["kind"] == "inspect"
    assert entry["model"] == CONFIG["vision"]["model"]
    assert entry["prompt_chars"] > 0
    assert entry["usage"] == {"input_tokens": 100, "output_tokens": 50}
    for img in entry["images"]:
        assert set(img) == {"path", "sha256"}
        assert img["path"] in {str(p) for p in sheets}


def test_log_records_recheck_calls_too(tmp_path):
    entries = [_entry(cid) for cid in APPLICABLE if cid != "R4"]
    entries.append(_entry("R4", verdict="uncertain", defect="VISIBLE_SEAM",
                          views=("close_034",)))
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["close_034"])
    recheck_report = {"checks": [_entry("R4", verdict="pass")]}
    client = FakeClient([make_response(_report(entries)), make_response(recheck_report)])
    sheets = _contact_sheets(tmp_path)
    log_path = tmp_path / "vision_call.json"

    inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                 contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                 contracts=C, config=CONFIG, log_path=log_path, sleep=_no_sleep)

    lines = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    kinds = [l["kind"] for l in lines]
    assert kinds == ["inspect", "recheck"]
    assert lines[1]["images"][0]["path"] == str(renders_dir / "close_034.png")


# ---------- request shape assertions ----------

def test_tool_schema_matches_contracts_exactly_and_no_sampling_params(tmp_path):
    client = FakeClient([make_response(_all_pass_report())])
    sheets = _contact_sheets(tmp_path)

    inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                 contact_sheets=sheets, renders_dir=tmp_path / "renders", iteration=1,
                 contracts=C, config=CONFIG, sleep=_no_sleep)

    kwargs = client.messages.calls[0]
    assert kwargs["tools"] == [{
        "name": "report_inspection",
        "description": kwargs["tools"][0]["description"],
        "input_schema": C.report_tool_schema(CATEGORY),
    }]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "report_inspection"}
    assert kwargs["model"] == CONFIG["vision"]["model"]
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    content = kwargs["messages"][0]["content"]
    assert len(content) == len(sheets) + 1
    assert all(b["type"] == "image" for b in content[:-1])
    assert content[-1]["type"] == "text"


# ---------- image delivery (vision.image_source, docs/VISION_BACKENDS.md) ----------

def _decode_block(block):
    return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))


def test_views_mode_sends_labeled_full_res_views_not_sheets(tmp_path):
    """image_source: views -- one full-resolution image per render, each
    preceded by a text block naming its view_id; contact sheets excluded;
    prompt wording says ids come from the text lines."""
    client = FakeClient([make_response(_all_pass_report())])
    sheets = _contact_sheets(tmp_path)
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["turn_000", "turn_090", "normals_045"])
    _png(renders_dir / "contact_sheet_L1.png")   # must be skipped

    config = {"vision": {**CONFIG["vision"], "image_source": "views"}}
    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                           contracts=C, config=config, sleep=_no_sleep)
    assert result.passed

    content = client.messages.calls[0]["messages"][0]["content"]
    labels = [b["text"] for b in content if b["type"] == "text"]
    images = [b for b in content if b["type"] == "image"]
    assert labels[:-1] == ["view_id: normals_045", "view_id: turn_000",
                           "view_id: turn_090"]           # sorted stems
    assert len(images) == 3                                # sheets excluded
    # label directly precedes its image
    assert content[0]["type"] == "text" and content[1]["type"] == "image"
    # prompt (last text block) explains the text-line labeling
    assert "immediately BEFORE each image" in labels[-1]


def test_views_mode_falls_back_to_sheets_when_no_renders(tmp_path):
    client = FakeClient([make_response(_all_pass_report())])
    sheets = _contact_sheets(tmp_path)
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()

    config = {"vision": {**CONFIG["vision"], "image_source": "views"}}
    inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                  contact_sheets=sheets, renders_dir=renders_dir, iteration=1,
                  contracts=C, config=config, sleep=_no_sleep)
    content = client.messages.calls[0]["messages"][0]["content"]
    images = [b for b in content if b["type"] == "image"]
    assert len(images) == len(sheets)


def test_oversize_images_are_resized_before_sending(tmp_path):
    """Anything above the provider downscale threshold is resampled by US
    (LANCZOS), not by opaque provider code: a 2048x3072 sheet goes out at
    <=1568 on the long edge; a 1024 view passes through untouched."""
    client = FakeClient([make_response(_all_pass_report())])
    big_sheet = tmp_path / "contact_sheet_big.png"
    _png(big_sheet, size=(2048, 3072))
    renders_dir = tmp_path / "renders"
    _stub_render_views(renders_dir, ["turn_000"])
    _png(renders_dir / "turn_000.png", size=(1024, 1024))

    inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                  contact_sheets=[big_sheet], renders_dir=renders_dir, iteration=1,
                  contracts=C, config=CONFIG, sleep=_no_sleep)
    content = client.messages.calls[0]["messages"][0]["content"]
    sheet_img = _decode_block(next(b for b in content if b["type"] == "image"))
    assert max(sheet_img.size) <= 1568

    client2 = FakeClient([make_response(_all_pass_report())])
    config = {"vision": {**CONFIG["vision"], "image_source": "views"}}
    inspect_asset(client2, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                  contact_sheets=[big_sheet], renders_dir=renders_dir, iteration=1,
                  contracts=C, config=config, sleep=_no_sleep)
    content = client2.messages.calls[0]["messages"][0]["content"]
    view_img = _decode_block(next(b for b in content if b["type"] == "image"))
    assert view_img.size == (1024, 1024)


# ---------- detail scout integration (docs/VISION_BACKENDS.md) ----------

class FakeScoutClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete_text(self, *, model, content, max_tokens=1024):
        self.calls.append({"model": model})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_scout_hints_are_appended_to_judge_prompt(tmp_path):
    client = FakeClient([make_response(_all_pass_report())])
    scout = FakeScoutClient('{"turn_045": ["thin seam on the ridge"]}')
    sheets = _contact_sheets(tmp_path)
    renders = tmp_path / "renders"
    _stub_render_views(renders, ["turn_045", "turn_090"])

    config = {"vision": {**CONFIG["vision"], "scout": {"model": "qwen2.5vl"}}}
    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders, iteration=1,
                           contracts=C, config=config, sleep=_no_sleep,
                           scout_client=scout)
    assert result.passed
    assert scout.calls and scout.calls[0]["model"] == "qwen2.5vl"
    # the judge's prompt (last text block) carries the advisory hint
    content = client.messages.calls[0]["messages"][0]["content"]
    prompt = content[-1]["text"]
    assert "DETAIL-SCOUT HINTS" in prompt
    assert "thin seam on the ridge" in prompt
    assert "advisory" in prompt.lower()


def test_scout_failure_does_not_change_inspection(tmp_path):
    """A scout that errors is swallowed: the judge prompt has no hints block
    and the asset still passes."""
    client = FakeClient([make_response(_all_pass_report())])
    scout = FakeScoutClient(RuntimeError("ollama offline"))
    sheets = _contact_sheets(tmp_path)
    renders = tmp_path / "renders"
    _stub_render_views(renders, ["turn_045"])

    config = {"vision": {**CONFIG["vision"], "scout": {"model": "m"}}}
    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                           contact_sheets=sheets, renders_dir=renders, iteration=1,
                           contracts=C, config=config, sleep=_no_sleep,
                           scout_client=scout)
    assert result.passed
    prompt = client.messages.calls[0]["messages"][0]["content"][-1]["text"]
    assert "DETAIL-SCOUT HINTS" not in prompt


def test_no_scout_client_leaves_prompt_unchanged(tmp_path):
    client = FakeClient([make_response(_all_pass_report())])
    sheets = _contact_sheets(tmp_path)
    renders = tmp_path / "renders"
    _stub_render_views(renders, ["turn_045"])

    inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.3-1.2 m",
                  contact_sheets=sheets, renders_dir=renders, iteration=1,
                  contracts=C, config=CONFIG, sleep=_no_sleep, scout_client=None)
    prompt = client.messages.calls[0]["messages"][0]["content"][-1]["text"]
    assert "DETAIL-SCOUT HINTS" not in prompt
