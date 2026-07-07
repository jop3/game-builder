"""OpenAI-compatible vision client (docs/VISION_BACKENDS.md): request/response
translation, weak-model fallbacks, transport retry, and an end-to-end
inspect_asset run -- no network, no SDK."""
from __future__ import annotations

import base64
import json

from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.vision.inspector import inspect_asset
from assetpipe.vision.openai_client import (
    OpenAIVisionClient,
    OpenAIVisionError,
    from_chat_response,
    to_chat_payload,
)

C = Contracts.load()
CATEGORY = "prop_small"

TOOL = {"name": "report_inspection", "description": "report",
        "input_schema": {"type": "object"}}

PNG_B64 = base64.b64encode(b"fakepng").decode("ascii")

BASE_KWARGS = {
    "model": "some-vision-model",
    "max_tokens": 4096,
    "tools": [TOOL],
    "tool_choice": {"type": "tool", "name": "report_inspection"},
    "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": PNG_B64}},
        {"type": "text", "text": "inspect this"},
    ]}],
}


def _chat_response(arguments, *, as_content=False, usage=(100, 50)):
    if as_content:
        message = {"role": "assistant", "content": arguments}
    else:
        message = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "call_abc", "type": "function",
                                   "function": {"name": "report_inspection",
                                                "arguments": arguments}}]}
    return {"choices": [{"message": message, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


# ---------- request translation ----------

def test_payload_translates_images_tools_and_forced_choice():
    payload = to_chat_payload(BASE_KWARGS)
    assert payload["model"] == "some-vision-model"
    assert payload["max_tokens"] == 4096
    parts = payload["messages"][0]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"] == f"data:image/png;base64,{PNG_B64}"
    assert parts[1] == {"type": "text", "text": "inspect this"}
    fn = payload["tools"][0]["function"]
    assert fn["name"] == "report_inspection"
    assert fn["parameters"] == {"type": "object"}
    assert payload["tool_choice"] == {"type": "function",
                                      "function": {"name": "report_inspection"}}


def test_payload_translates_corrective_retry_turn():
    """The inspector's corrective retry replays the assistant tool_use turn
    and sends a tool_result -- OpenAI needs tool_calls + a role:tool message
    that directly follows the assistant turn."""
    kwargs = dict(BASE_KWARGS)
    kwargs["messages"] = BASE_KWARGS["messages"] + [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "report_inspection",
             "input": {"checks": []}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": "errors: fix it", "is_error": True}]},
    ]
    payload = to_chat_payload(kwargs)
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assistant = payload["messages"][1]
    call = assistant["tool_calls"][0]
    assert call["id"] == "toolu_1"
    assert json.loads(call["function"]["arguments"]) == {"checks": []}
    tool_msg = payload["messages"][2]
    assert tool_msg["tool_call_id"] == "toolu_1"
    assert "fix it" in tool_msg["content"]


# ---------- response translation ----------

def test_response_tool_call_arguments_string():
    resp = from_chat_response(_chat_response(json.dumps({"iteration": 1})),
                              "report_inspection")
    block = resp["content"][0]
    assert block["type"] == "tool_use"
    assert block["input"] == {"iteration": 1}
    assert resp["usage"] == {"input_tokens": 100, "output_tokens": 50}


def test_response_tool_call_arguments_already_decoded_or_double_encoded():
    resp = from_chat_response(_chat_response({"iteration": 2}), "report_inspection")
    assert resp["content"][0]["input"] == {"iteration": 2}
    double = json.dumps(json.dumps({"iteration": 3}))
    resp = from_chat_response(_chat_response(double), "report_inspection")
    assert resp["content"][0]["input"] == {"iteration": 3}


def test_response_json_in_content_fallback_with_fences():
    """A weaker model ignoring the forced tool call but answering with a
    fenced JSON report is still accepted."""
    text = "Here is my report:\n```json\n" + json.dumps({"iteration": 4}) + "\n```"
    resp = from_chat_response(_chat_response(text, as_content=True),
                              "report_inspection")
    assert resp["content"][0]["input"] == {"iteration": 4}


def test_response_without_tool_call_or_json_raises():
    try:
        from_chat_response(_chat_response("no json here at all", as_content=True),
                           "report_inspection")
    except OpenAIVisionError:
        pass
    else:
        raise AssertionError("expected OpenAIVisionError")


# ---------- transport retry ----------

def _client_with(transport_results, sleeps):
    results = list(transport_results)

    def fake_transport(url, headers, payload, timeout_s):
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return OpenAIVisionClient(base_url="http://fake.local/v1", api_key="k",
                              transport=fake_transport, sleep=sleeps.append)


def _http_error(status):
    err = OpenAIVisionError(f"HTTP {status}")
    err.status = status
    return err


def test_transport_retries_429_then_succeeds():
    sleeps: list = []
    ok = _chat_response(json.dumps({"iteration": 1}))
    client = _client_with([_http_error(429), ok], sleeps)
    resp = client.messages.create(**BASE_KWARGS)
    assert resp["content"][0]["input"] == {"iteration": 1}
    assert sleeps == [5]


def test_transport_non_retryable_400_raises_immediately():
    sleeps: list = []
    client = _client_with([_http_error(400)], sleeps)
    try:
        client.messages.create(**BASE_KWARGS)
    except OpenAIVisionError:
        pass
    else:
        raise AssertionError("expected OpenAIVisionError")
    assert sleeps == []


def test_transport_exhausted_retries_raise():
    sleeps: list = []
    client = _client_with([_http_error(503)] * 3, sleeps)
    try:
        client.messages.create(**BASE_KWARGS)
    except OpenAIVisionError:
        pass
    else:
        raise AssertionError("expected OpenAIVisionError")
    assert sleeps == [5, 30]


# ---------- end-to-end through inspect_asset ----------

def _all_pass_report():
    checks = [{"check_id": cid, "verdict": "pass", "confidence": 0.9,
               "evidence_views": [], "location": "", "description": ""}
              for cid in C.applicable_checks(CATEGORY)]
    return {"asset_id": "x", "iteration": 1, "checks": checks,
            "checks_not_applicable": [], "overall_impression": "fine"}


def test_inspect_asset_runs_end_to_end_with_openai_client(tmp_path):
    """The whole V2 loop -- prompt build, forced tool call, semantic
    validation -- works against an OpenAI-compatible endpoint, proving the
    iteration loop is not tied to one provider (docs/VISION_BACKENDS.md)."""
    sheet = tmp_path / "contact_sheet_L1.png"
    Image.new("RGB", (64, 64), (120, 130, 140)).save(sheet)
    renders = tmp_path / "renders"
    renders.mkdir()

    client = _client_with([_chat_response(json.dumps(_all_pass_report()))], [])
    result = inspect_asset(
        client,
        request={"asset_id": "x", "category": CATEGORY, "theme": "scifi_industrial",
                 "seed": 1, "description": "a crate"},
        theme={"display_name": "Sci-Fi Industrial", "palette": {}},
        bbox_range="unspecified", contact_sheets=[sheet], renders_dir=renders,
        iteration=1, contracts=C,
        config={"vision": {"model": "some-vision-model", "max_recheck_rounds": 1}},
        log_path=tmp_path / "vision_call.json")
    assert result.passed
    assert (tmp_path / "vision_call.json").exists()


# ---------- complete_text (detail-scout transport) ----------

def test_complete_text_returns_message_content(monkeypatch):
    captured = {}

    def fake_transport(url, headers, payload, timeout_s):
        captured["payload"] = payload
        return {"choices": [{"message": {"role": "assistant",
                                         "content": "{\"turn_000\": [\"seam\"]}"}}]}

    client = OpenAIVisionClient(base_url="http://fake/v1", api_key="k",
                                transport=fake_transport, sleep=lambda _s: None)
    text = client.complete_text(model="qwen2.5vl", content=[
        {"type": "text", "text": "view_id: turn_000"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": PNG_B64}},
        {"type": "text", "text": "spot defects"},
    ], max_tokens=512)
    assert text == '{"turn_000": ["seam"]}'
    # no tools/tool_choice on a plain completion; images translated
    assert "tools" not in captured["payload"]
    assert captured["payload"]["max_tokens"] == 512
    parts = captured["payload"]["messages"][0]["content"]
    assert any(p["type"] == "image_url" for p in parts)


def test_complete_text_raises_without_text_content():
    def fake_transport(url, headers, payload, timeout_s):
        return {"choices": [{"message": {"role": "assistant", "content": None}}]}

    client = OpenAIVisionClient(base_url="http://fake/v1", api_key="k",
                                transport=fake_transport, sleep=lambda _s: None)
    try:
        client.complete_text(model="m", content=[{"type": "text", "text": "hi"}])
    except OpenAIVisionError:
        pass
    else:
        raise AssertionError("expected OpenAIVisionError")
