"""Vision inspection stage V2: the Anthropic API caller (spec 15, 17.2, 22).

Orchestrates exactly the call structure spec 15.1 mandates: one forced-tool-use
call per iteration with the contact sheets as image blocks, semantic
validation with one corrective retry (spec 15.4), and the single uncertain
crop re-query round (spec 15.5). API transport errors are retried with
backoff and, if unrecoverable, raised as :class:`assetpipe.loop.InfraError`
per the "API outage mid-run" mitigation (spec 22) -- never as an asset
verdict.

The tool schema always comes from :meth:`Contracts.report_tool_schema`; the
prompt text always comes from :mod:`assetpipe.vision.prompts`. This module
never paraphrases rubric criteria or hand-writes the schema (README
invariants).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from assetpipe.contracts import Contracts
from assetpipe.loop import InfraError, StageResult
from assetpipe.vision import detail_scout
from assetpipe.vision.prompts import build_inspection_prompt, build_recheck_prompt
from assetpipe.vision.report import aggregate, extract_findings, validate_report

TOOL_NAME = "report_inspection"
TOOL_DESCRIPTION = ("Report structured pass/fail/uncertain verdicts, with cited "
                    "evidence, for every rubric check applicable to this asset.")

# Backoff schedule for transient API errors (spec 22: "retry with backoff, 3
# attempts over <=5 min"). Two sleeps between three attempts.
_BACKOFFS_S = (5, 30)

# A 512x512 crop centered at 1/3 or 2/3 of an axis needs that axis to be at
# least 768px so the crop never needs clamping; smaller renders are resized
# up first (spec 15.5). The crop loop also clamps defensively.
_CROP_SIZE = 512
_MIN_CROP_SOURCE_DIM = 768

# Providers downscale big images before the model sees them (Anthropic: long
# edge capped ~1568 px, then a ~1.15-megapixel cap). Anything we send above
# that is resampled by code we don't control; resize ourselves (LANCZOS) so
# what the model inspects is predictable. 1024^2 renders pass through
# untouched; a 2048x3072 contact sheet would land at ~875x1313 -- each 1024
# cell inspected at ~437 px, which is why vision.image_source: views exists
# (docs/VISION_BACKENDS.md).
_MAX_SEND_EDGE = 1568


def _anthropic_module():
    """Import the SDK lazily so this module still imports without it (spec
    22 resilience note); callers that never hit the network never need it."""
    try:
        import anthropic
        return anthropic
    except ImportError:
        return None


def _is_retryable(exc: Exception, anthropic) -> bool:
    if anthropic is None:
        return False
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", 0) >= 500:
        return True
    return False


def _call_with_retry(client, kwargs: dict, sleep: Callable[[float], None]):
    """client.messages.create with the spec-22 backoff/retry policy.

    Non-retryable API errors, and retryable ones that stay exhausted after
    len(_BACKOFFS_S) sleeps, become InfraError -- never an asset verdict.
    """
    anthropic = _anthropic_module()
    attempts = len(_BACKOFFS_S) + 1
    for attempt in range(attempts):
        try:
            return client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised as InfraError below
            if not _is_retryable(exc, anthropic):
                raise InfraError(f"vision API error: {exc}") from exc
            if attempt == attempts - 1:
                raise InfraError(f"vision API error after retries: {exc}") from exc
            sleep(_BACKOFFS_S[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def _image_block(data: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(data).decode("ascii")}}


def _read_capped(path: Path) -> bytes:
    """PNG bytes, resized down to _MAX_SEND_EDGE on the long edge if needed
    so the provider never resamples behind our back."""
    data = path.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        if max(w, h) <= _MAX_SEND_EDGE:
            return data
        scale = _MAX_SEND_EDGE / max(w, h)
        resized = im.convert("RGB").resize(
            (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    return _png_bytes(resized)


def _load_image_blocks(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    blocks, meta = [], []
    for p in paths:
        data = _read_capped(p)
        blocks.append(_image_block(data))
        meta.append({"path": str(p), "sha256": hashlib.sha256(data).hexdigest()})
    return blocks, meta


def _view_content_blocks(renders_dir: Path) -> tuple[list[dict], list[dict]]:
    """vision.image_source: views -- every render as its OWN image block at
    full resolution, preceded by a text block naming its view_id (the sheet
    cells carry burned-in labels; bare views need the text pairing). A
    2x3-sheet pixel budget after provider downscaling leaves each 1024 cell
    at ~437 px; individual 1024 views pass through untouched, so small
    defects stay visible to API models (docs/VISION_BACKENDS.md)."""
    blocks, meta = [], []
    for p in sorted(renders_dir.glob("*.png")):
        if p.stem.startswith("contact_sheet"):
            continue
        data = _read_capped(p)
        blocks.append({"type": "text", "text": f"view_id: {p.stem}"})
        blocks.append(_image_block(data))
        meta.append({"path": str(p), "sha256": hashlib.sha256(data).hexdigest(),
                     "view_id": p.stem})
    return blocks, meta


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _center_crops(img: Image.Image) -> list[Image.Image]:
    """Center-weighted 2x2 grid of four 512x512 crops (spec 15.5)."""
    w, h = img.size
    if w < _MIN_CROP_SOURCE_DIM or h < _MIN_CROP_SOURCE_DIM:
        img = img.resize((max(w, _MIN_CROP_SOURCE_DIM), max(h, _MIN_CROP_SOURCE_DIM)))
        w, h = img.size
    half = _CROP_SIZE // 2
    crops = []
    for fx in (1 / 3, 2 / 3):
        for fy in (1 / 3, 2 / 3):
            cx, cy = int(w * fx), int(h * fy)
            left = min(max(cx - half, 0), w - _CROP_SIZE)
            top = min(max(cy - half, 0), h - _CROP_SIZE)
            crops.append(img.crop((left, top, left + _CROP_SIZE, top + _CROP_SIZE)))
    return crops


def _block_attr(block, name: str, default=None):
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _response_content(response) -> list:
    content = _block_attr(response, "content", [])
    return content or []


def _first_tool_use(response) -> tuple[dict, str | None]:
    """Extract the report_inspection input from the first tool_use block.

    Blocks may be SDK objects (.type/.input/.id) or plain dicts (fake test
    clients use either shape).
    """
    for block in _response_content(response):
        if _block_attr(block, "type") == "tool_use":
            return _block_attr(block, "input"), _block_attr(block, "id")
    raise InfraError("vision response contained no tool_use block")


def _normalize_content(response) -> list[dict]:
    """Turn a response's content blocks into plain dicts so they can be
    replayed as an assistant turn in a follow-up request."""
    out = []
    for block in _response_content(response):
        if isinstance(block, dict):
            out.append(block)
            continue
        d = {"type": _block_attr(block, "type")}
        for attr in ("id", "name", "input", "text"):
            val = _block_attr(block, attr)
            if val is not None:
                d[attr] = val
        out.append(d)
    return out


def _usage_dict(response) -> dict | None:
    usage = _block_attr(response, "usage")
    if usage is None:
        return None
    it = _block_attr(usage, "input_tokens")
    ot = _block_attr(usage, "output_tokens")
    if it is None and ot is None:
        return None
    return {"input_tokens": it, "output_tokens": ot}


def _append_log(log_path: Path | None, kind: str, model: str, images: list[dict],
                prompt_chars: int, response_payload, usage: dict | None) -> None:
    """Append one JSON line per API call (spec 17.2). Never logs image bytes,
    only path + sha256 references."""
    if log_path is None:
        return
    entry = {"kind": kind, "model": model, "images": images,
             "prompt_chars": prompt_chars, "response": response_payload}
    if usage is not None:
        entry["usage"] = usage
    with log_path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _tool_def(schema: dict) -> dict:
    return {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "input_schema": schema}


def _resolve_view_id(finding, chk: dict, renders_dir: Path) -> str:
    """First cited evidence view, else the first view from the check's view
    patterns that actually has a render on disk (spec 15.5)."""
    if finding.evidence_views:
        return finding.evidence_views[0]
    available = sorted(p.stem for p in renders_dir.glob("*.png"))
    for pattern in chk["views"]:
        for stem in available:
            if Contracts.view_matches(stem, [pattern]):
                return stem
    if chk["views"]:
        return chk["views"][0]
    raise InfraError(f"no view available to recheck check {finding.check_id}")


def _resolve_uncertain(client, finding, report: dict, contracts: Contracts,
                       renders_dir: Path, model: str, tool_def: dict,
                       sleep: Callable[[float], None], log_path: Path | None) -> dict:
    """Run the single-check crop re-query for one uncertain finding and merge
    its verdict back over the original report entry (spec 15.5)."""
    chk = contracts.rubric["checks"][finding.check_id]
    view_id = _resolve_view_id(finding, chk, renders_dir)
    view_path = renders_dir / f"{view_id}.png"

    with Image.open(view_path) as im:
        crops = _center_crops(im.convert("RGB"))
    image_blocks = [_image_block(_png_bytes(c)) for c in crops]

    prior = {"evidence_views": finding.evidence_views, "description": finding.description}
    prompt = build_recheck_prompt(finding.check_id, prior, contracts)
    kwargs = {
        "model": model,
        "max_tokens": 4096,
        "tools": [tool_def],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": image_blocks + [
            {"type": "text", "text": prompt}]}],
    }

    source_sha = hashlib.sha256(view_path.read_bytes()).hexdigest()
    images_meta = [{"path": str(view_path), "sha256": source_sha, "crop": i}
                   for i in range(len(crops))]

    try:
        response = _call_with_retry(client, kwargs, sleep)
    except InfraError as exc:
        _append_log(log_path, "recheck", model, images_meta, len(prompt),
                   {"error": str(exc)}, None)
        raise

    new_report, _ = _first_tool_use(response)
    _append_log(log_path, "recheck", model, images_meta, len(prompt), new_report,
               _usage_dict(response))

    new_entry = next((c for c in (new_report or {}).get("checks", [])
                      if c.get("check_id") == finding.check_id), None)
    if new_entry is None:
        raise InfraError(f"recheck response for {finding.check_id} did not include "
                        "that check in checks[]")
    # The merged report is fed to extract_findings, which assumes a validated
    # report; guard the single entry so a malformed recheck reply becomes an
    # InfraError (spec 15.1), not a KeyError mid-extraction.
    if new_entry.get("verdict") not in ("pass", "fail", "uncertain"):
        raise InfraError(f"recheck response for {finding.check_id} has invalid "
                        f"verdict {new_entry.get('verdict')!r}")
    if new_entry.get("verdict") != "pass" and \
            new_entry.get("defect_type") not in contracts.defects:
        raise InfraError(f"recheck response for {finding.check_id} has invalid "
                        f"defect_type {new_entry.get('defect_type')!r}")

    merged_checks, replaced = [], False
    for entry in report["checks"]:
        if entry.get("check_id") == finding.check_id:
            merged_checks.append(new_entry)
            replaced = True
        else:
            merged_checks.append(entry)
    if not replaced:
        merged_checks.append(new_entry)
    merged = dict(report)
    merged["checks"] = merged_checks
    return merged


def inspect_asset(client, *, request: dict, theme: dict, bbox_range: str,
                  contact_sheets: list[Path], renders_dir: Path,
                  iteration: int, contracts: Contracts, config: dict,
                  log_path: Path | None = None,
                  sleep: Callable[[float], None] = time.sleep,
                  scout_client=None) -> StageResult:
    """Run stage V2 for one iteration and return its StageResult (spec 15).

    See module docstring for the call/retry/recheck structure. `iteration` is
    accepted for interface symmetry with the other Stages methods and future
    logging needs; the report's own `iteration` field is whatever the model
    echoes back and is not itself load-bearing here (validate_report/
    extract_findings never inspect it).

    ``scout_client`` (optional) is a separate, typically LOCAL high-resolution
    vision client used for an advisory detail pre-pass (spec 15 stays the
    authority). Its hints are merged into the judge's prompt as suggestions;
    a scout failure is swallowed and the inspection proceeds unchanged. See
    :mod:`assetpipe.vision.detail_scout` / docs/VISION_BACKENDS.md.
    """
    category = request["category"]
    model = config["vision"]["model"]
    tool_schema = contracts.report_tool_schema(category)
    tool_def = _tool_def(tool_schema)

    # "views" sends every render as its own full-resolution image (labeled
    # by a preceding text block); "contact_sheets" sends the composed grids
    # (fewer tokens, but provider-side downscaling costs each cell most of
    # its pixels -- see _MAX_SEND_EDGE note / docs/VISION_BACKENDS.md).
    image_source = config["vision"].get("image_source", "contact_sheets")
    if image_source == "views":
        image_blocks, images_meta = _view_content_blocks(renders_dir)
        if not image_blocks:  # no bare renders on disk: fall back to sheets
            image_source = "contact_sheets"
    if image_source != "views":
        image_blocks, images_meta = _load_image_blocks(contact_sheets)

    prompt = build_inspection_prompt(request, theme, bbox_range, contracts,
                                     image_delivery=image_source)

    # Detail-scout pre-pass (advisory): a local high-res model flags spots for
    # the judge to look at closely. Failure-isolated inside scout_hints -- an
    # empty string (scout off, or errored) leaves the prompt unchanged.
    if scout_client is not None:
        scout_cfg = config["vision"].get("scout", {})
        hints_block = detail_scout.scout_hints(
            scout_client, model=scout_cfg.get("model", model),
            renders_dir=renders_dir, max_edge=scout_cfg.get("max_edge"),
            log_path=log_path)
        if hints_block:
            prompt = prompt + "\n\n" + hints_block

    # NOTE: no temperature/top_p passed. Current Claude models reject sampling
    # parameters entirely; config's vision.temperature: 0 intent is satisfied
    # by forced tool_choice determinism instead (see config/defaults.yaml).
    base_kwargs = {
        "model": model,
        "max_tokens": 4096,
        "tools": [tool_def],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": image_blocks + [
            {"type": "text", "text": prompt}]}],
    }

    try:
        response = _call_with_retry(client, base_kwargs, sleep)
    except InfraError as exc:
        _append_log(log_path, "inspect", model, images_meta, len(prompt),
                   {"error": str(exc)}, None)
        raise
    report, tool_use_id = _first_tool_use(response)
    _append_log(log_path, "inspect", model, images_meta, len(prompt), report,
               _usage_dict(response))

    errors = validate_report(report, category, contracts)
    if errors:
        error_text = "; ".join(errors)
        retry_kwargs = dict(base_kwargs)
        retry_kwargs["messages"] = base_kwargs["messages"] + [
            {"role": "assistant", "content": _normalize_content(response)},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id or "toolu_missing",
                "content": ("Your previous report_inspection call had semantic "
                           f"errors: {error_text}. Call report_inspection again "
                           "with a fully corrected report."),
                "is_error": True,
            }]},
        ]
        try:
            response = _call_with_retry(client, retry_kwargs, sleep)
        except InfraError as exc:
            _append_log(log_path, "inspect", model, images_meta, len(prompt),
                       {"error": str(exc)}, None)
            raise
        report, tool_use_id = _first_tool_use(response)
        _append_log(log_path, "inspect", model, images_meta, len(prompt), report,
                   _usage_dict(response))
        errors = validate_report(report, category, contracts)
        if errors:
            raise InfraError("vision report failed semantic validation after the "
                            f"single corrective retry: {'; '.join(errors)}")

    max_rounds = config["vision"].get("max_recheck_rounds", 1)
    current_report = report
    rounds_done = 0
    while True:
        findings = extract_findings(current_report, category, contracts, final_round=False)
        agg = aggregate(findings)
        if not agg["uncertain"] or rounds_done >= max_rounds:
            break
        for finding in agg["uncertain"]:
            current_report = _resolve_uncertain(
                client, finding, current_report, contracts, renders_dir, model,
                tool_def, sleep, log_path)
        rounds_done += 1

    final_findings = extract_findings(current_report, category, contracts, final_round=True)
    result = aggregate(final_findings)
    return StageResult(passed=result["passed"], blockers=result["blockers"],
                       warns=result["warns"])
