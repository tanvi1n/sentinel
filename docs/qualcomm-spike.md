# Qualcomm semantic integration: spike note

Status: **LIVE is not available yet.** The adapter is built and tested against a
fake model call, and it is wired into the backend through `app/semantic/bridge.py`
(see `docs/semantic-integration.md`). The real network call is deliberately not
written, because the Qualcomm Cloud AI Playground details are not known. Nothing in
this repository has been run against Qualcomm. With a key, URL and model configured
but the call unimplemented, the result is UNAVAILABLE, and the guard moves to REVIEW
for any tool that requires a semantic check.

## 1. Implemented now

| Piece | Where | State |
|---|---|---|
| Generic interface `SemanticReasoner` | `app/semantic/base.py` | Done. Callers use `analyze(context)`, which never raises: any provider exception becomes a non-valid `SemanticResult` (TIMEOUT for `TimeoutError`, UNAVAILABLE otherwise, INVALID for a malformed return). Error text carries only the exception type, never its message. |
| Live provider `QualcommReasoner` | `app/semantic/qualcomm.py` | Done except the network call. |
| Adapter to the backend contract | `app/semantic/bridge.py` | Done. Maps findings to SAFE / SUSPICIOUS / UNSAFE and failures to INVALID / TIMEOUT / UNAVAILABLE. |
| Network call `default_call_model` | `app/semantic/qualcomm.py` | **Intentionally not implemented.** It raises `LiveCallNotImplemented` (not retried), so the reasoner reports UNAVAILABLE with error `live call not implemented`. |
| Mock provider | `app/semantic/mock.py` | Done. Seven fixed modes, every text starts with `[MOCK]`. |
| Replay provider | `app/semantic/replay.py` | Done. Looks up recorded LIVE results by key; a miss is UNAVAILABLE and never invents findings. |
| Factory | `app/semantic/factory.py` | Done. Modes `mock`, `replay`, `live`, `auto`. |
| Prompt | `app/semantic/prompts/v1.txt` | Draft. Not frozen. |
| Replay recorder | `scripts/record_replay.py` | Done. `--from-scenarios` records through the real harness and guard; `--contexts` takes explicit inputs. Records only VALID results that came from a LIVE call. |

### How the Qualcomm provider behaves today

- **Configuration** is read from the environment once, when the config object is
  built. Variable names: `QUALCOMM_API_KEY`, `QUALCOMM_BASE_URL`,
  `QUALCOMM_MODEL`, `QUALCOMM_TIMEOUT_SECONDS` (default 8),
  `SENTINEL_SEMANTIC_MODE` (default `mock`), `SENTINEL_PROMPT_VERSION`
  (default `v1`), `SENTINEL_REPLAY_PATH`. The names, with empty placeholders, are
  in `.env.example`. The key is never logged, returned, placed in a prompt,
  result, error, audit entry or replay record, or shown in a repr; tests check the
  HTTP responses, audit log and health route for it. Health reporting says only
  whether a key is configured.
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
architecture, `combine()` (in the guard) can raise APPROVE to REVIEW on a semantic
finding, but a deterministic BLOCK stands whatever the model says. The `compromised`
mock mode exists to demonstrate exactly that, and `tests/integration/` checks it
through the real engine.

### Mock and Replay

- **Mock** is for development and tests. It ignores the context and is always
  labelled MOCK. `auto` mode never falls back to it.
- **Replay** serves previously recorded LIVE results from a JSONL file, keyed by
  a SHA-256 of the canonical context plus the prompt version. The cache file
  `replay/cache.jsonl` (local, git-ignored) does not exist yet, because nothing
  real has been recorded; `replay/demo.jsonl` is the reviewed, committed copy once
  it exists (see `replay/README.md`). Mock and unavailable results are never
  accepted as recordings.

## 2. Enabling LIVE once the key and API details arrive

1. Put the values in a local `.env` (copy `.env.example`; `.env` is git-ignored):
   `QUALCOMM_API_KEY`, `QUALCOMM_BASE_URL`, `QUALCOMM_MODEL`, and
   `QUALCOMM_TIMEOUT_SECONDS` if 8 s is wrong. Run `python scripts/preflight.py`:
   `.env.example` must still show no key.
2. Write the body of `default_call_model(prompt, timeout) -> str` in
   `app/semantic/qualcomm.py`, from the provider's documentation (section 3). It
   must send `prompt`, honour `timeout` (raise `TimeoutError` when it expires),
   raise on HTTP or auth errors, return only the reply text, and never put the key
   in a log or exception message. The adapter already handles retry, JSON
   extraction, validation, labelling and failure mapping. If an HTTP client is
   needed, `httpx` is already in `requirements.txt`.
3. Run the unit tests (`python -m pytest tests/semantic`), then one real call:
   `python scripts/run_scenario.py --semantic-mode live`. Steps that require a
   check must show `semantic .../QUALCOMM`; any `UNAVAILABLE/NONE` means the call
   failed, and `GET /api/health` shows whether a key is configured.
4. Run the spike checklist below and record the outcome here.
5. Tune `prompts/v1.txt` on the scenario inputs, then freeze it. Any prompt change
   afterwards invalidates recorded replays.
6. Record: `python scripts/record_replay.py --from-scenarios --out replay/demo.jsonl`,
   review it, check it with `--semantic-mode replay`, and commit only that file
   (steps in `replay/README.md`).
7. On the demo machine: verify LIVE, REPLAY with the network off, and the failure
   paths (no key, timeout, invalid output, replay miss). Each must show REVIEW, not
   APPROVE, for a tool that requires a check.

Do not label anything LIVE until step 3 has succeeded against the real service.

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
