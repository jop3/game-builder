# Running the vision loop with any model

The V2 inspection tier is model-agnostic by construction: the prompt text
(`assetpipe/vision/prompts.py`), the forced `report_inspection` tool schema
(`Contracts.report_tool_schema`), the semantic validation + single
corrective retry, the server-side two-view rule, and the uncertain crop
re-query (`assetpipe/vision/inspector.py`) are identical whichever model
answers. Only the *client* — the thing exposing `.messages.create(...)` —
changes. Three are built in; pick with `--vision-client` and set the model
with `--vision-model` (config: `vision.client` / `vision.model`).

## 1. `api` — Anthropic SDK (default)

    export ANTHROPIC_API_KEY=...
    python -m assetpipe generate --request req.json --out runs/ \
        --blender-bin blender --vision-client api --vision-model claude-fable-5

Any Claude vision model works (`--vision-model claude-sonnet-5`, ...).
`ANTHROPIC_BASE_URL` is honored by the SDK for gateways/proxies that speak
the Anthropic API.

## 2. `openai` — any OpenAI-compatible endpoint

`assetpipe/vision/openai_client.py` adapts the same calls to the Chat
Completions API, so anything speaking that protocol can run the loop:
OpenAI, Gemini's OpenAI-compat endpoint, OpenRouter, and local servers
(vLLM, Ollama, LM Studio — keyless requests are fine).

    # OpenAI
    export OPENAI_API_KEY=...
    python -m assetpipe generate ... --vision-client openai --vision-model gpt-5.2

    # OpenRouter (any hosted vision model)
    export OPENAI_API_KEY=$OPENROUTER_API_KEY
    python -m assetpipe generate ... --vision-client openai \
        --vision-base-url https://openrouter.ai/api/v1 \
        --vision-model qwen/qwen3.5-vl-plus

    # Gemini (OpenAI-compat endpoint)
    export OPENAI_API_KEY=$GEMINI_API_KEY
    python -m assetpipe generate ... --vision-client openai \
        --vision-base-url https://generativelanguage.googleapis.com/v1beta/openai \
        --vision-model gemini-3-flash

    # Local vLLM / Ollama
    python -m assetpipe generate ... --vision-client openai \
        --vision-base-url http://localhost:11434/v1 --vision-model llama4-vision

Config equivalents: `vision.base_url`, `vision.api_key_env` (name of the
env var holding the key; default `OPENAI_API_KEY`), `vision.request_timeout_s`.
Requests carry base64 `data:` image URLs and a forced function call;
transient failures (429/5xx/connection) retry with the same backoff the
Anthropic path uses.

## 3. `agent` — file exchange (a driving agent's own vision)

    python -m assetpipe generate ... --vision-client agent --vision-exchange exch/

Each vision call blocks while `exch/call_NNNN/` is populated with
`prompt.txt`, `images/*.png`, and `request.json` (which embeds the exact
tool `input_schema`); whoever is driving — an interactive agent session, a
different harness, or a patient human — looks at the images and writes the
tool input to `report.json`. Protocol details:
`assetpipe/vision/agent_client.py`. This is also the cheapest way to trial
a model that has a chat UI but no API yet.

## Image resolution: what the model actually sees

Providers downscale large images before the model ever sees them (Anthropic:
long edge capped around 1568 px, then a ~1.15-megapixel cap; other providers
have similar limits). The composed 2x3 contact sheets are 2048x3072 at the
default 1024 render resolution, so model-side they land at roughly 875x1313
— every 1024x1024 view inspected at ~437 px, under a fifth of its rendered
pixels. That is enough to judge silhouettes and palettes and MISS thin
seams, per-plank detail, and small artifacts ("the model seems to be
downscaling and missing details" — it is the transport, not the model).

`vision.image_source` (defaults.yaml) controls delivery:

- `views` (the default): every render goes out as its OWN image at full
  resolution, preceded by a text line naming its view_id (bare renders have
  no burned-in labels; the sheets do). A 1024 view is under every provider
  cap, so nothing is resampled. Costs roughly 10-15x the image tokens of
  sheets (~18k vs ~1.5k per inspection at 1024/16 views) — worth it: the
  pipeline's whole value is catching visual defects.
- `contact_sheets`: the composed grids, for token-constrained runs.

Anything still above 1568 px on an edge (e.g. sheets, or a bumped render
resolution) is resized down by the inspector itself with LANCZOS before
sending, so the resampling is at least predictable and logged. The
`uncertain` crop re-query always sends full-resolution 512-px crops
regardless of this setting.

## Expectations for imperfect models

The harness already absorbs the common weak-model failure modes — the
loop's guarantees come from its own validation, not from the model being
flawless:

- **Malformed reports.** Forced tool use fixes the shape; semantic errors
  (missing/duplicate checks, fail without evidence, unknown defect types)
  get ONE corrective retry with the errors quoted back, then the call is an
  infrastructure error — never a fake asset verdict (spec 15.1/15.4).
- **Ignored tool call.** The openai client accepts a JSON report in the
  message content (markdown fences and surrounding prose tolerated), and
  arguments that arrive decoded or double-encoded.
- **Overeager single-view failures.** The two-view rule downgrades a fail
  cited from one view to `uncertain` (spec 15.3), which triggers the crop
  re-query round rather than a wasted repair iteration.
- **Hedging.** After `vision.max_recheck_rounds`, remaining `uncertain`
  becomes fail-with-0.5-confidence (fail-safe, spec 15.5).

What a weaker model still costs you: blocker false-positives burn repair
iterations (`iteration.max_iterations` caps the damage; assets land
`best_effort` instead of `validated`), and missed defects ship. The
scripted S/A-checks catch the objective failures regardless of the vision
model; the rubric judgement quality is exactly what varies. When comparing
models, run the same request/seed and diff `vision_report.json` +
`history.jsonl` across runs — and see NEXT_STEPS item 2 for the planned
labeled fixture corpus (spec 21.1), which is the principled way to score a
candidate model (≥90% catch rate, 0 blocker false-positives) before
trusting it with a batch.
