"""Detail scout: an optional local high-resolution vision pre-pass whose
findings are appended to the judge's inspection prompt as ADVISORY hints
(spec 15 stays the authority -- the judge verifies every hint against the
same renders and owns the verdict).

Motivation (docs/VISION_BACKENDS.md): a cloud judge (Opus) sees renders
after provider-side downscaling, so thin seams / per-plank artifacts can
vanish before it ever looks. A local VLM (Qwen2.5-VL via Ollama, ...) sees
native resolution but judges less reliably. The scout splits those roles:
the local model reports *where to look closely*, the judge decides. Because
hints are advisory, a hallucinating scout costs the judge a second look at a
clean spot -- never a false asset failure.

Hard invariants:

- **Never blocks.** Any scout error (transport, timeout, unparseable reply)
  is swallowed and logged; :func:`scout_hints` returns ``""`` and the
  inspection runs exactly as if the scout were disabled.
- **Advisory only.** Output is free text merged into the prompt, never a
  verdict, defect, or tool call. It cannot pass or fail a check by itself.
- **Full resolution.** The scout receives per-view images capped only by the
  local backend's own limits (default: no cap), which is the whole point.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Callable

from PIL import Image

# One compact instruction; the scout is a spotter, not a judge. It must not
# emit verdicts (the judge owns those) -- only coordinates of interest.
_SCOUT_PROMPT = """\
You are a high-resolution detail spotter for a 3D game-asset QA pipeline. You
are NOT the judge -- do not pass or fail anything. Look ONLY for small visual
anomalies a downscaled view would hide: thin UV/texture seams (hard lines
where a surface is continuous), stretched or smeared texels, tiling/repeat
artifacts, z-fighting stripes on coplanar faces, single stray bright/black
texels, and per-plank/per-tile detail that looks mushy or missing.

For each render named below, report at most the 3 most notable spots as terse
location phrases. Respond with ONLY a JSON object mapping view_id -> list of
short strings, e.g. {"turn_045": ["thin seam along the roof ridge"]}. Omit a
view entirely if nothing stands out. No prose outside the JSON."""

_MAX_HINT_VIEWS = 12          # scouting every view is slow; cap the sweep
_MAX_HINTS_PER_VIEW = 3


def _image_block(data: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(data).decode("ascii")}}


def _read_capped(path: Path, max_edge: int | None) -> bytes:
    data = path.read_bytes()
    if not max_edge:
        return data
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        if max(w, h) <= max_edge:
            return data
        scale = max_edge / max(w, h)
        resized = im.convert("RGB").resize(
            (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="PNG")
    return buf.getvalue()


def _scout_views(renders_dir: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Content views worth scouting: skip contact sheets, silhouettes and
    the normals/uv debug passes (their 'anomalies' are by design)."""
    skip = ("contact_sheet", "silhouette_", "normals_", "uvcheck")
    out = []
    for p in sorted(renders_dir.glob("*.png")):
        if any(p.stem.startswith(s) or p.stem == s.rstrip("_") for s in skip):
            continue
        out.append(p)
    return out[:_MAX_HINT_VIEWS]


def _parse_hints(text: str) -> dict[str, list[str]]:
    """Extract the view->hints object from a possibly-fenced free-text reply.
    Tolerant: anything unparseable yields {} (the scout is best-effort)."""
    text = text.strip()
    if text.startswith("```"):
        nl, fence = text.find("\n"), text.rfind("```")
        if nl != -1 and fence > nl:
            text = text[nl + 1:fence].strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        return {}
    hints: dict[str, list[str]] = {}
    for view, items in obj.items():
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            continue
        clean = [str(s).strip() for s in items if str(s).strip()][:_MAX_HINTS_PER_VIEW]
        if clean:
            hints[str(view)] = clean
    return hints


def format_hints_block(hints: dict[str, list[str]]) -> str:
    """Render hints as the advisory prompt section merged into the judge's
    inspection prompt. Empty hints -> empty string (no section added)."""
    if not hints:
        return ""
    lines = ["DETAIL-SCOUT HINTS (advisory, from a high-resolution local "
             "pre-pass -- these are POSSIBLE spots to look at closely, NOT "
             "verdicts; confirm each against the renders yourself and ignore "
             "any that do not hold up):"]
    for view in sorted(hints):
        for note in hints[view]:
            lines.append(f"- {view}: {note}")
    return "\n".join(lines)


def scout_hints(client, *, model: str, renders_dir: Path,
                max_edge: int | None = None,
                log_path: Path | None = None,
                view_patterns: tuple[str, ...] = ()) -> str:
    """Run the scout and return the formatted advisory block, or ``""`` on any
    failure or if disabled inputs are missing. NEVER raises: the inspection
    must proceed unchanged whether or not the scout works.

    ``client`` needs a ``complete_text(model=..., content=..., max_tokens=...)``
    method (assetpipe.vision.openai_client.OpenAIVisionClient provides it).
    """
    try:
        views = _scout_views(renders_dir, view_patterns)
        if not views:
            return ""
        content: list[dict] = []
        names = []
        for p in views:
            content.append({"type": "text", "text": f"view_id: {p.stem}"})
            content.append(_image_block(_read_capped(p, max_edge)))
            names.append(p.stem)
        content.append({"type": "text",
                        "text": _SCOUT_PROMPT + "\n\nViews provided: " + ", ".join(names)})
        text = client.complete_text(model=model, content=content, max_tokens=1024)
        hints = _parse_hints(text)
        if log_path is not None:
            _log(log_path, model, names, text, hints, None)
        return format_hints_block(hints)
    except Exception as exc:  # noqa: BLE001 -- scout must never break inspection
        if log_path is not None:
            _log(log_path, model, [], None, {}, str(exc))
        return ""


def _log(log_path: Path, model: str, views: list[str], raw: str | None,
         hints: dict, error: str | None) -> None:
    entry = {"kind": "detail_scout", "model": model, "views": views, "hints": hints}
    if raw is not None:
        entry["raw_chars"] = len(raw)
    if error is not None:
        entry["error"] = error
    try:
        with Path(log_path).open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass
