"""AgentVisionClient: the file-exchange V2 client (agent's own vision).

No network, no API key, no anthropic import needed -- the whole point of the
agent client is running V2 in environments without credentials.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.loop import InfraError
from assetpipe.vision.agent_client import AgentVisionClient, AgentVisionTimeout
from assetpipe.vision.inspector import inspect_asset

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


def _png_bytes(color=(40, 60, 80), size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _image_block(data: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(data).decode("ascii")}}


def _all_pass_report() -> dict:
    return {
        "asset_id": REQUEST["asset_id"], "iteration": 1,
        "checks": [{"check_id": cid, "verdict": "pass", "confidence": 0.95}
                   for cid in APPLICABLE],
        "checks_not_applicable": [],
        "overall_impression": "clean asset",
    }


def _tool_def() -> dict:
    return {"name": "report_inspection", "description": "d",
            "input_schema": C.report_tool_schema(CATEGORY)}


def _kwargs(prompt="inspect this asset") -> dict:
    return {
        "model": "claude-fable-5", "max_tokens": 4096,
        "tools": [_tool_def()],
        "tool_choice": {"type": "tool", "name": "report_inspection"},
        "messages": [{"role": "user", "content": [
            _image_block(_png_bytes()), _image_block(_png_bytes((90, 20, 20))),
            {"type": "text", "text": prompt}]}],
    }


def _answering_client(tmp_path: Path, make_report, **kw) -> AgentVisionClient:
    """Client whose first poll sleep answers the newest unanswered call --
    stands in for the interactive agent watching the exchange dir."""
    exchange = tmp_path / "exchange"

    def sleep(_s):
        pending = sorted(d for d in exchange.glob("call_*")
                         if not (d / "report.json").exists())
        assert pending, "poll slept with no pending call"
        (pending[-1] / "report.json").write_text(json.dumps(make_report(pending[-1])))

    return AgentVisionClient(exchange, poll_s=0, sleep=sleep, **kw)


def test_create_dumps_request_and_returns_report(tmp_path):
    client = _answering_client(tmp_path, lambda _d: _all_pass_report())
    response = client.messages.create(**_kwargs(prompt="PROMPT_SENTINEL"))

    block = response["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "report_inspection"
    assert block["input"] == _all_pass_report()

    call_dir = tmp_path / "exchange" / "call_0000"
    req = json.loads((call_dir / "request.json").read_text())
    assert req["tool_name"] == "report_inspection"
    assert req["kind"] == "call"
    assert req["input_schema"] == C.report_tool_schema(CATEGORY)
    assert len(req["image_files"]) == 2
    for i, path in enumerate(req["image_files"]):
        img = Image.open(path)
        assert img.size == (8, 8)  # decoded back to a real PNG
    assert "PROMPT_SENTINEL" in (call_dir / "prompt.txt").read_text()


def test_retry_turn_is_flagged_and_feedback_dumped(tmp_path):
    client = _answering_client(tmp_path, lambda _d: _all_pass_report())
    kwargs = _kwargs()
    kwargs["messages"] = kwargs["messages"] + [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1",
                                           "name": "report_inspection", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                      "content": "ERROR_FEEDBACK_SENTINEL",
                                      "is_error": True}]},
    ]
    client.messages.create(**kwargs)
    call_dir = tmp_path / "exchange" / "call_0000"
    assert json.loads((call_dir / "request.json").read_text())["kind"] == "retry"
    assert "ERROR_FEEDBACK_SENTINEL" in (call_dir / "prompt.txt").read_text()


def test_partial_json_keeps_polling(tmp_path):
    exchange = tmp_path / "exchange"
    writes = iter(['{"asset_id": "trunc',  # write in progress
                   json.dumps(_all_pass_report())])

    def sleep(_s):
        (exchange / "call_0000" / "report.json").write_text(next(writes))

    client = AgentVisionClient(exchange, poll_s=0, sleep=sleep)
    response = client.messages.create(**_kwargs())
    assert response["content"][0]["input"] == _all_pass_report()


def test_timeout_raises_agent_vision_timeout(tmp_path):
    ticks = iter(range(100))
    client = AgentVisionClient(tmp_path / "exchange", poll_s=0, timeout_s=3,
                               sleep=lambda _s: None, clock=lambda: next(ticks))
    with pytest.raises(AgentVisionTimeout):
        client.messages.create(**_kwargs())


def test_call_dirs_are_sequential_and_collision_safe(tmp_path):
    exchange = tmp_path / "exchange"
    client = _answering_client(tmp_path, lambda _d: _all_pass_report())
    client.messages.create(**_kwargs())
    # A second client sharing the exchange dir (parallel batch) steps past
    # existing call dirs instead of clobbering them.
    other = _answering_client(tmp_path, lambda _d: _all_pass_report())
    other.messages.create(**_kwargs())
    assert sorted(d.name for d in exchange.glob("call_*")) == ["call_0000", "call_0001"]


def test_inspect_asset_end_to_end_with_agent_client(tmp_path):
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    sheet = renders_dir / "contact_sheet_0.png"
    sheet.write_bytes(_png_bytes(size=(64, 64)))

    client = _answering_client(tmp_path, lambda _d: _all_pass_report())
    result = inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.5-1.5m",
                           contact_sheets=[sheet], renders_dir=renders_dir,
                           iteration=1, contracts=C, config=CONFIG,
                           log_path=tmp_path / "vision_call.json")
    assert result.passed and not result.blockers and not result.warns
    # The call log records the report, exactly as with the API client.
    entries = [json.loads(l) for l in (tmp_path / "vision_call.json").read_text().splitlines()]
    assert entries[0]["kind"] == "inspect"
    assert entries[0]["response"]["checks"]


def test_inspect_asset_timeout_becomes_infra_error(tmp_path):
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    sheet = renders_dir / "contact_sheet_0.png"
    sheet.write_bytes(_png_bytes(size=(64, 64)))

    ticks = iter(range(1000))
    client = AgentVisionClient(tmp_path / "exchange", poll_s=0, timeout_s=3,
                               sleep=lambda _s: None, clock=lambda: next(ticks))
    with pytest.raises(InfraError):
        inspect_asset(client, request=REQUEST, theme=THEME, bbox_range="0.5-1.5m",
                      contact_sheets=[sheet], renders_dir=renders_dir,
                      iteration=1, contracts=C, config=CONFIG)


def test_cli_factory_selects_agent_client(tmp_path):
    from assetpipe.cli import _vision_client_factory
    cfg = {"vision": {"client": "agent",
                      "agent_exchange_dir": str(tmp_path / "exchange"),
                      "agent_poll_s": 1, "agent_timeout_s": 60}}
    client = _vision_client_factory(cfg)()
    assert isinstance(client, AgentVisionClient)
    assert client.timeout_s == 60


def test_cli_factory_requires_exchange_dir():
    from assetpipe.cli import _vision_client_factory
    with pytest.raises(SystemExit):
        _vision_client_factory({"vision": {"client": "agent"}})()
