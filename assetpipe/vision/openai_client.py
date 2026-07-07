"""OpenAI-compatible vision client: run V2 with non-Anthropic models.

Drop-in replacement for the Anthropic SDK client in
:func:`assetpipe.vision.inspector.inspect_asset` (the ``.messages.create``
surface), backed by any endpoint speaking the OpenAI Chat Completions API:
OpenAI itself, Gemini's OpenAI-compat endpoint, OpenRouter, vLLM, Ollama,
LM Studio, ... The inspector, prompts, tool schema, semantic validation,
corrective retry and crop re-query are all unchanged -- this module only
translates the request/response shapes:

- Anthropic ``messages`` content blocks -> Chat Completions content parts
  (base64 image blocks become ``data:`` image_url parts); ``tool_result``
  blocks become ``role: "tool"`` messages (OpenAI's shape for tool replies).
- The forced ``tool_choice`` -> ``{"type": "function", "function": ...}``.
- The response's first tool call -> an Anthropic-shaped ``tool_use`` block
  dict, which is exactly what ``inspector._first_tool_use`` consumes.

Robustness for less capable models (they are well on their way, not perfect):

- ``arguments`` may arrive as a JSON string (the spec shape), an
  already-decoded object, or a double-encoded string -- all accepted.
- A model that ignores the forced tool call and answers with JSON in the
  message content (with or without markdown fences) is still accepted: the
  JSON is parsed and treated as the tool input. Anything unparseable raises,
  which the inspector's semantic-validation/corrective-retry path then
  handles the same way it handles a malformed Anthropic reply.

Transport: stdlib ``urllib`` (no new dependency). Transient failures (429,
5xx, connection errors) are retried here with the same backoff schedule the
inspector uses for Anthropic SDK errors; terminal failures raise
:class:`OpenAIVisionError`, which the inspector classifies as non-retryable
and wraps into ``InfraError`` -- never an asset verdict (spec 22).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

DEFAULT_BASE_URL = "https://api.openai.com/v1"
BASE_URL_ENV = "OPENAI_BASE_URL"
API_KEY_ENV = "OPENAI_API_KEY"

# Mirrors inspector._BACKOFFS_S (spec 22: 3 attempts over <= 5 min).
_BACKOFFS_S = (5, 30)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


class OpenAIVisionError(Exception):
    """Terminal transport/translation failure. Deliberately NOT an anthropic
    error type, so inspector._is_retryable returns False and the call
    surfaces as InfraError without re-running the (already spent) backoff."""


# ---------------------------------------------------------------------------
# Request translation (pure, unit-tested without a network)
# ---------------------------------------------------------------------------

def _content_parts(blocks) -> tuple[list[dict], list[dict]]:
    """Anthropic content blocks -> (chat content parts, tool messages).

    ``tool_result`` blocks cannot live inside an OpenAI user message; they
    are returned separately as ``role: "tool"`` messages for the caller to
    emit after the preceding assistant turn.
    """
    parts: list[dict] = []
    tool_msgs: list[dict] = []
    if isinstance(blocks, str):
        return [{"type": "text", "text": blocks}], []
    for block in blocks:
        btype = block.get("type")
        if btype == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif btype == "image":
            src = block["source"]
            url = f"data:{src.get('media_type', 'image/png')};base64,{src['data']}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif btype == "tool_result":
            content = block.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content)
            tool_msgs.append({"role": "tool",
                              "tool_call_id": block.get("tool_use_id") or "call_0",
                              "content": content})
        # other block types have no chat-completions counterpart; skip
    return parts, tool_msgs


def _assistant_message(blocks) -> dict:
    texts, tool_calls = [], []
    if isinstance(blocks, str):
        return {"role": "assistant", "content": blocks}
    for block in blocks:
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id") or "call_0",
                "type": "function",
                "function": {"name": block.get("name", ""),
                             "arguments": json.dumps(block.get("input") or {})},
            })
    msg: dict = {"role": "assistant", "content": "\n".join(t for t in texts if t) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def to_chat_payload(kwargs: dict) -> dict:
    """Anthropic ``messages.create`` kwargs -> Chat Completions payload."""
    messages: list[dict] = []
    for message in kwargs.get("messages", []):
        role = message.get("role")
        if role == "assistant":
            messages.append(_assistant_message(message.get("content")))
            continue
        parts, tool_msgs = _content_parts(message.get("content"))
        # Tool replies must directly follow the assistant tool_calls turn.
        messages.extend(tool_msgs)
        if parts:
            messages.append({"role": role or "user", "content": parts})

    payload: dict = {"model": kwargs["model"], "messages": messages}
    if kwargs.get("max_tokens") is not None:
        payload["max_tokens"] = kwargs["max_tokens"]
    if kwargs.get("tools"):
        payload["tools"] = [{"type": "function",
                             "function": {"name": t["name"],
                                          "description": t.get("description", ""),
                                          "parameters": t["input_schema"]}}
                            for t in kwargs["tools"]]
    choice = kwargs.get("tool_choice")
    if choice and choice.get("type") == "tool":
        payload["tool_choice"] = {"type": "function",
                                  "function": {"name": choice["name"]}}
    return payload


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------

def _parse_arguments(arguments) -> dict:
    """Tool-call arguments as a dict, tolerating the shapes weaker models
    produce: JSON string (spec), decoded object, double-encoded string."""
    for _ in range(2):
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    if isinstance(arguments, dict):
        return arguments
    raise OpenAIVisionError(f"tool call arguments are not an object: {type(arguments)}")


def _json_from_text(text: str) -> dict | None:
    """Parse a JSON object out of free text, tolerating markdown fences and
    prose before/after the object -- the classic weak-model failure when a
    forced tool call is ignored."""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        fence_end = text.rfind("```")
        if first_nl != -1 and fence_end > first_nl:
            text = text[first_nl + 1:fence_end].strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def from_chat_response(data: dict, tool_name: str) -> dict:
    """Chat Completions response -> Anthropic-shaped response dict with a
    single ``tool_use`` content block (what the inspector consumes)."""
    choices = data.get("choices") or []
    if not choices:
        raise OpenAIVisionError(f"chat response has no choices: {data!r:.500}")
    message = choices[0].get("message") or {}

    tool_input, call_id = None, None
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        tool_input = _parse_arguments(fn.get("arguments"))
        call_id = call.get("id") or "call_0"
        break
    if tool_input is None and isinstance(message.get("content"), str):
        tool_input = _json_from_text(message["content"])
        call_id = "call_0"
    if tool_input is None:
        raise OpenAIVisionError(
            "chat response contained neither a tool call nor parseable JSON "
            f"content (finish_reason={choices[0].get('finish_reason')!r})")

    usage = data.get("usage") or {}
    return {
        "content": [{"type": "tool_use", "id": call_id, "name": tool_name,
                     "input": tool_input}],
        "usage": {"input_tokens": usage.get("prompt_tokens"),
                  "output_tokens": usage.get("completion_tokens")},
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _http_transport(url: str, headers: dict, payload: dict, timeout_s: float) -> dict:
    """POST JSON, return decoded JSON. Raises OpenAIVisionError with the
    status code attached (``.status``) so the retry loop can classify it."""
    req = urllib.request.Request(url, method="POST",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            pass
        err = OpenAIVisionError(f"HTTP {exc.code} from {url}: {body}")
        err.status = exc.code
        raise err from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        err = OpenAIVisionError(f"connection error to {url}: {exc}")
        err.status = None  # connection-level: retryable
        raise err from exc


class _Messages:
    def __init__(self, client: "OpenAIVisionClient"):
        self._client = client

    def create(self, **kwargs):
        return self._client._create(**kwargs)


class OpenAIVisionClient:
    """SDK-shaped client (``.messages.create``) over an OpenAI-compatible
    chat-completions endpoint.

    ``base_url`` falls back to ``$OPENAI_BASE_URL`` then the OpenAI default;
    ``api_key`` falls back to the ``api_key_env`` environment variable
    (default ``$OPENAI_API_KEY``) and may be absent for local servers
    (vLLM/Ollama/LM Studio accept keyless requests).
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, *,
                 api_key_env: str = API_KEY_ENV, timeout_s: float = 300.0,
                 transport: Callable = _http_transport,
                 sleep: Callable[[float], None] = time.sleep):
        self.base_url = (base_url or os.environ.get(BASE_URL_ENV)
                         or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get(api_key_env)
        self.timeout_s = timeout_s
        self._transport = transport
        self._sleep = sleep
        self.messages = _Messages(self)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _post_with_retry(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        attempts = len(_BACKOFFS_S) + 1
        for attempt in range(attempts):
            try:
                return self._transport(url, self._headers(), payload, self.timeout_s)
            except OpenAIVisionError as exc:
                status = getattr(exc, "status", 0)
                retryable = status is None or status in _RETRYABLE_STATUS
                if not retryable or attempt == attempts - 1:
                    raise
                self._sleep(_BACKOFFS_S[attempt])
        raise AssertionError("unreachable")  # pragma: no cover

    def _create(self, **kwargs):
        payload = to_chat_payload(kwargs)
        tool_name = (kwargs.get("tool_choice") or {}).get("name") \
            or ((kwargs.get("tools") or [{}])[0].get("name", ""))
        return from_chat_response(self._post_with_retry(payload), tool_name)

    def complete_text(self, *, model: str, content: list, max_tokens: int = 1024) -> str:
        """Plain (no-tool) completion for Anthropic-shaped ``content`` blocks;
        returns the assistant message text. Used by the detail scout, whose
        replies are free JSON rather than a forced tool call."""
        parts, _ = _content_parts(content)
        payload = {"model": model, "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": parts}]}
        data = self._post_with_retry(payload)
        choices = data.get("choices") or []
        if not choices:
            raise OpenAIVisionError(f"chat response has no choices: {data!r:.500}")
        text = (choices[0].get("message") or {}).get("content")
        if not isinstance(text, str):
            raise OpenAIVisionError("chat response message has no text content")
        return text
