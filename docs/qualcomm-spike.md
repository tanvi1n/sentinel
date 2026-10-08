# Qualcomm semantic integration: spike note

Status: **LIVE is not available yet.** The adapter is built and tested against a
fake model call. The real network call is deliberately not written, because the
Qualcomm Cloud AI Playground details are not known. Nothing in this repository
has been run against Qualcomm.

## 1. Implemented now

| Piece | Where | State |
|---|---|---|
| Generic interface `SemanticReasoner` | `app/semantic/base.py` | Done. Callers use `analyze(context)`, which never raises: any provider exception becomes a non-valid `SemanticResult` (TIMEOUT for `TimeoutError`, UNAVAILABLE otherwise, INVALID for a malformed return). Error text carries only the exception type, never its message. |
| Live provider `QualcommReasoner` | `app/semantic/qualcomm.py` | Done except the network call. |
| Network call `default_call_model` | `app/semantic/qualcomm.py` | **Intentionally not implemented.** It raises `NotImplementedError`, so the reasoner reports UNAVAILABLE. |
| Mock provider | `app/semantic/mock.py` | Done. Seven fixed modes, every text starts with `[MOCK]`. |
| Replay provider | `app/semantic/replay.py` | Done. Looks up recorded LIVE results by key; a miss is UNAVAILABLE and never invents findings. |
| Factory | `app/semantic/factory.py` | Done. Modes `mock`, `replay`, `live`, `auto`. |
| Prompt | `app/semantic/prompts/v1.txt` | Draft. Not frozen. |
| Replay recorder | `scripts/record_replay.py` | Done. Records only VALID results that came from a LIVE call. |

### How the Qualcomm provider behaves today

- **Configuration** is read from the environment once, when the config object is
  built. Variable names: `QUALCOMM_API_KEY`, `QUALCOMM_BASE_URL`,
  `QUALCOMM_MODEL`, `QUALCOMM_TIMEOUT_SECONDS` (default 8),
  `SENTINEL_SEMANTIC_MODE` (default `mock`), `SENTINEL_PROMPT_VERSION`
  (default `v1`), `SENTINEL_REPLAY_PATH`. The key is never logged, returned,
  placed in a prompt, result or error, or shown in a repr. Health reporting says
  only whether a key is configured.
- **Missing settings:** if the key, base URL or model is missing, the result is
  UNAVAILABLE with error `not configured` and no call is attempted.
- **Input:** built only from the generic `SemanticContext`. The prompt has
  separate sections for trusted context, the agent's claims, arguments found in
  external content, and observed external content. Anything that is not ours is
  emitted as JSON strings so it cannot break out of its section.
- **Call policy:** one call plus one retry through `call_model(prompt, timeout)`.
- **Strict validation:** the reply may have text around the JSON object, but the
  object must have exactly `intent_alignment`, `injection_suspected`,
  `ambiguity` and `rationale`; enum values must be valid, `injection_suspected`
  must be a real boolean, and the rationale is at most 400 characters. Extra
  keys (including a `decision` key) are rejected. Anything else is INVALID.
- **Labels:** a valid reply is labelled LIVE with the provider, latency and
  prompt version. Every failure has source NONE.

### Semantic output is advisory

The semantic layer returns only a `SemanticResult`. It cannot produce BLOCK and
cannot lower a decision, and the result shape has no decision field. In the
architecture, `combine()` (in the guard, not yet built) can raise APPROVE to
REVIEW on a semantic finding, but a deterministic BLOCK stands whatever the
model says. The `compromised` mock mode exists to demonstrate exactly that.

### Mock and Replay

- **Mock** is for development and tests. It ignores the context and is always
  labelled MOCK. `auto` mode never falls back to it.
- **Replay** serves previously recorded LIVE results from a JSONL file, keyed by
  a SHA-256 of the canonical context plus the prompt version. The cache file
  `replay/cache.jsonl` does not exist yet, because nothing real has been
  recorded. Mock and unavailable results are never accepted as recordings.

## 2. What remains for the real integration

1. Write the body of `default_call_model` (one function, one file).
2. Run the spike below and record the outcome in this note.
3. Tune prompt v1 on the scenario inputs, then freeze it. Any prompt change after
   that invalidates recorded replays.
4. Build the exact `SemanticContext` inputs through the real guard (registry tool
   descriptions and origin-check flags, supplied by the backend), so replay keys
   match production.
5. Record replay with `scripts/record_replay.py` from real LIVE results.
6. Verify LIVE, REPLAY and the failure paths (no key, timeout, invalid output,
   replay miss, no network) on the demo machine.

## 3. Information needed before `call_model` can be written

None of these are known today, and none are assumed in the code:

- The endpoint style and base URL.
- How requests are authenticated (header name and format).
- The model identifier(s) to use.
- The request and response format, and how to read the text of the reply.
- Whether structured or JSON-only output is supported.
- Rate limits and typical latency.
- How timeouts and errors are signalled.
- Whether the demo network can reach it (the plan calls for one test from a phone
  hotspot).

## Spike checklist (from the work-division plan; none of it done yet)

- [ ] One scripted call succeeds.
- [ ] About 10 calls to measure latency.
- [ ] Six hand-written inputs; pass bar is 5 of 6 valid, with the clearly aligned
      and clearly misaligned cases judged correctly.
- [ ] One injection string.
- [ ] One test from a phone hotspot.

Outcome and chosen path (live, partial, or manual replay): **not recorded yet.**
If access never arrives, the demo runs on Mock with a visible banner and the
wording "integration built and tested against a stand-in; live access pending".
Nothing may be labelled LIVE or REPLAY unless it truly was.
