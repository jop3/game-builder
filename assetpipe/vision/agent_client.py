"""File-exchange vision client: a driving agent's own vision instead of the API.

Drop-in replacement for the Anthropic SDK client in
:func:`assetpipe.vision.inspector.inspect_asset`. Instead of calling the
Messages API, ``messages.create(**kwargs)`` serializes the request to a
``call_NNNN/`` directory under an exchange root and blocks until a
``report.json`` appears there, then replays it as the forced tool_use
response. This lets an interactive agent (e.g. a Claude Code session, which
can view images directly) *be* the vision model: it watches the exchange
directory, looks at the dumped renders with its own vision, and writes the
``report_inspection`` tool input as ``report.json``.

Everything downstream is unchanged: the same prompt builder, the same
generated tool schema (written verbatim to ``request.json``), the same
semantic validation / two-view rule / uncertain re-query in ``inspect_asset``.
The corrective-retry turn (spec 15.4) also round-trips through here -- its
error feedback text is included in ``prompt.txt`` -- so the agent gets the
same second chance the API model would.

Exchange protocol, per call directory:

- ``request.json``  -- model, kind marker, tool name + input_schema, image list
- ``prompt.txt``    -- every text block of the conversation, role-labeled
- ``images/NN.png`` -- the image blocks, decoded, in conversation order
- ``report.json``   -- **written by the agent**: the tool input object only
  (the thing that would be ``tool_use.input``), matching request.json's
  ``input_schema``. Partial/unparsable JSON is ignored (treated as a write in
  progress) and polling continues, so a non-atomic writer is safe.

A poll timeout raises :class:`AgentVisionTimeout`, which the inspector's
retry policy classifies as non-retryable and wraps into ``InfraError`` --
never an asset verdict, same as any other vision-transport failure (spec 22).
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Callable

REQUEST_FILE = "request.json"
PROMPT_FILE = "prompt.txt"
REPORT_FILE = "report.json"
IMAGES_DIR = "images"


class AgentVisionTimeout(Exception):
    """No report.json appeared within timeout_s. Deliberately NOT an
    anthropic error type, so inspector._is_retryable returns False and the
    call surfaces as InfraError without burning the backoff schedule."""


def _block_get(block, name: str, default=None):
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


class _AgentMessages:
    def __init__(self, client: "AgentVisionClient"):
        self._client = client

    def create(self, **kwargs):
        return self._client._create(**kwargs)


class AgentVisionClient:
    """SDK-shaped client (``.messages.create``) backed by a file exchange.

    One instance may serve many calls (the loop makes one inspect call per
    iteration plus recheck calls); each gets a fresh ``call_NNNN`` directory.
    Directory creation is atomic (``mkdir`` with retry on collision), so
    several clients -- e.g. parallel per-asset loops in a batch -- can share
    one exchange root.
    """

    def __init__(self, exchange_dir: Path | str, *, poll_s: float = 2.0,
                 timeout_s: float = 1800.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.exchange_dir = Path(exchange_dir)
        self.exchange_dir.mkdir(parents=True, exist_ok=True)
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self._sleep = sleep
        self._clock = clock
        self.messages = _AgentMessages(self)

    # ---------- request serialization ----------

    def _new_call_dir(self) -> Path:
        n = 0
        for existing in self.exchange_dir.glob("call_*"):
            try:
                n = max(n, int(existing.name.split("_")[1]) + 1)
            except (IndexError, ValueError):
                continue
        while True:
            call_dir = self.exchange_dir / f"call_{n:04d}"
            try:
                call_dir.mkdir()
                return call_dir
            except FileExistsError:  # concurrent client won the name; step past
                n += 1

    @staticmethod
    def _walk_content(messages: list) -> tuple[list[str], list[bytes]]:
        """Flatten a Messages-API conversation into role-labeled text pieces
        and image byte strings, in order. Handles the shapes inspect_asset
        actually sends: text blocks, base64 image blocks, tool_use blocks
        (replayed assistant turn), and tool_result blocks (retry feedback)."""
        texts: list[str] = []
        images: list[bytes] = []
        for message in messages:
            role = _block_get(message, "role", "user")
            content = _block_get(message, "content", [])
            if isinstance(content, str):
                texts.append(f"[{role}]\n{content}")
                continue
            for block in content:
                btype = _block_get(block, "type")
                if btype == "text":
                    texts.append(f"[{role}]\n{_block_get(block, 'text', '')}")
                elif btype == "image":
                    source = _block_get(block, "source", {})
                    if _block_get(source, "type") == "base64":
                        images.append(base64.b64decode(_block_get(source, "data", "")))
                elif btype == "tool_use":
                    texts.append(f"[{role} tool_use {_block_get(block, 'name')}]\n"
                                 + json.dumps(_block_get(block, "input"), indent=2,
                                              default=str))
                elif btype == "tool_result":
                    texts.append(f"[{role} tool_result]\n"
                                 f"{_block_get(block, 'content', '')}")
        return texts, images

    def _create(self, **kwargs):
        call_dir = self._new_call_dir()
        texts, images = self._walk_content(kwargs.get("messages", []))

        images_dir = call_dir / IMAGES_DIR
        images_dir.mkdir()
        image_files = []
        for i, data in enumerate(images):
            path = images_dir / f"{i:02d}.png"
            path.write_bytes(data)
            image_files.append(str(path))

        tools = kwargs.get("tools", [])
        tool = tools[0] if tools else {}
        # A retry turn (assistant tool_use + is_error tool_result present)
        # is flagged so the agent knows this is the corrective second chance.
        is_retry = any(_block_get(b, "type") == "tool_result"
                       for m in kwargs.get("messages", [])
                       for b in (_block_get(m, "content", [])
                                 if not isinstance(_block_get(m, "content"), str) else []))
        (call_dir / REQUEST_FILE).write_text(json.dumps({
            "model": kwargs.get("model"),
            "kind": "retry" if is_retry else "call",
            "tool_name": _block_get(tool, "name"),
            "input_schema": _block_get(tool, "input_schema"),
            "image_files": image_files,
            "instructions": (
                "Inspect the images under images/ with your own vision, following "
                "prompt.txt. Then write the tool input object (only the object that "
                "would be tool_use.input, matching input_schema) to report.json in "
                "this directory."),
        }, indent=2, default=str))
        (call_dir / PROMPT_FILE).write_text("\n\n".join(texts))

        report = self._poll_for_report(call_dir)
        return {"content": [{
            "type": "tool_use",
            "id": f"toolu_agent_{call_dir.name}",
            "name": _block_get(tool, "name", "report_inspection"),
            "input": report,
        }]}

    # ---------- response polling ----------

    def _poll_for_report(self, call_dir: Path) -> dict:
        report_path = call_dir / REPORT_FILE
        deadline = self._clock() + self.timeout_s
        while True:
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text())
                except (json.JSONDecodeError, OSError):
                    report = None  # write in progress; keep polling
                if isinstance(report, dict):
                    return report
            if self._clock() >= deadline:
                raise AgentVisionTimeout(
                    f"no report.json appeared in {call_dir} within "
                    f"{self.timeout_s:.0f}s")
            self._sleep(self.poll_s)
