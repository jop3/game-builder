"""Detail scout: advisory local high-res pre-pass (docs/VISION_BACKENDS.md).
No network -- a fake complete_text client stands in for the local VLM."""
from __future__ import annotations

import json

from PIL import Image

from assetpipe.vision import detail_scout


class FakeScout:
    """Minimal client exposing complete_text; records calls, returns a queued
    reply or raises a queued exception."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    def complete_text(self, *, model, content, max_tokens=1024):
        self.calls.append({"model": model, "content": content, "max_tokens": max_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _renders(tmp_path, names):
    d = tmp_path / "renders"
    d.mkdir()
    for n in names:
        Image.new("RGB", (64, 64), (120, 130, 140)).save(d / f"{n}.png")
    return d


# ---------- parsing ----------

def test_parse_plain_json():
    out = detail_scout._parse_hints('{"turn_045": ["thin seam on ridge"]}')
    assert out == {"turn_045": ["thin seam on ridge"]}


def test_parse_fenced_json_with_prose():
    text = "Sure!\n```json\n{\"turn_000\": [\"smeared texels\"]}\n```\nhope that helps"
    assert detail_scout._parse_hints(text) == {"turn_000": ["smeared texels"]}


def test_parse_coerces_string_to_list_and_caps_per_view():
    out = detail_scout._parse_hints('{"a": "one", "b": ["1","2","3","4","5"]}')
    assert out["a"] == ["one"]
    assert out["b"] == ["1", "2", "3"]          # _MAX_HINTS_PER_VIEW


def test_parse_garbage_is_empty():
    assert detail_scout._parse_hints("no json at all") == {}
    assert detail_scout._parse_hints("{not valid}") == {}


# ---------- formatting ----------

def test_format_empty_is_blank():
    assert detail_scout.format_hints_block({}) == ""


def test_format_marks_hints_advisory_and_lists_them():
    block = detail_scout.format_hints_block({"turn_045": ["seam"], "top": ["stray texel"]})
    assert "advisory" in block.lower()
    assert "NOT" in block and "verdict" in block.lower()
    assert "- top: stray texel" in block
    assert "- turn_045: seam" in block


# ---------- scout_hints (end-to-end, isolated failures) ----------

def test_scout_hints_happy_path(tmp_path):
    renders = _renders(tmp_path, ["turn_000", "turn_045"])
    client = FakeScout('{"turn_045": ["thin seam along the roof ridge"]}')
    log = tmp_path / "vision_call.json"

    block = detail_scout.scout_hints(client, model="qwen2.5vl", renders_dir=renders,
                                     log_path=log)
    assert "roof ridge" in block
    assert "advisory" in block.lower()
    # one call, images labeled, scout prompt present
    content = client.calls[0]["content"]
    labels = [b["text"] for b in content if b["type"] == "text"]
    assert "view_id: turn_000" in labels
    assert any("detail spotter" in t.lower() for t in labels)
    # logged
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["kind"] == "detail_scout"
    assert entry["hints"] == {"turn_045": ["thin seam along the roof ridge"]}


def test_scout_skips_debug_and_sheet_views(tmp_path):
    renders = _renders(tmp_path, ["turn_000", "silhouette_000", "normals_045",
                                  "uvcheck_045"])
    (renders / "contact_sheet_L1.png").write_bytes(
        (renders / "turn_000.png").read_bytes())
    client = FakeScout("{}")
    detail_scout.scout_hints(client, model="m", renders_dir=renders)
    content = client.calls[0]["content"]
    labels = [b["text"] for b in content if b["type"] == "text" and b["text"].startswith("view_id")]
    assert labels == ["view_id: turn_000"]      # debug/sheets/silhouettes skipped


def test_scout_never_raises_on_client_error(tmp_path):
    renders = _renders(tmp_path, ["turn_000"])
    client = FakeScout(RuntimeError("ollama down"))
    log = tmp_path / "vision_call.json"
    # must swallow and return "" -- inspection proceeds unchanged
    assert detail_scout.scout_hints(client, model="m", renders_dir=renders,
                                    log_path=log) == ""
    entry = json.loads(log.read_text().splitlines()[-1])
    assert "ollama down" in entry["error"]


def test_scout_no_scoutable_views_returns_blank(tmp_path):
    renders = _renders(tmp_path, ["silhouette_000", "normals_045"])
    client = FakeScout('{"x": ["y"]}')
    assert detail_scout.scout_hints(client, model="m", renders_dir=renders) == ""
    assert client.calls == []                    # never even called
