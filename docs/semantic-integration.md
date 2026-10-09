# Semantic layer: how it connects to the backend

The backend's contracts are authoritative. The semantic layer adapts to them in
one module, `app/semantic/bridge.py`. Nothing else in `app/semantic/` imports the
guard. The guard itself never calls a model: it receives a semantic result from
a trusted caller and decides with `combine()`.

## Who calls the semantic layer

Every path that evaluates a proposal gets its semantic result from the server
side, through `advise(proposal, harness_provenance)`:

| Entry point | Semantic result comes from |
|---|---|
| `POST /evaluate`, `POST /api/evaluate` | `advise()` in the route. A `semantic_result` field in the request body is refused with 422, the same convention as `arg_provenance`. |
| `POST /api/demo/{run_id}/step` | `advise()` in the route, with the scenario's mock variant (used only in mock mode). |
| `agent/harness.py` (scenario runner, recorder) | `advise()` as the harness's default advisor. |
| `GuardClient.evaluate(...)` in Python | The caller. This is the trusted in-process interface; the three paths above are its only users in the application. |

`advise()` returns `None` for a tool whose registry entry has
`requires_semantic: false` (no model is called). For a tool that requires a
check it always returns a result: the provider's answer, or INVALID / TIMEOUT /
UNAVAILABLE if anything fails, including an exception in the provider or factory.
One call per evaluation; execution-time revalidation never calls it.

## Contract mapping

| Provider result | Guard `SemanticOutcome` |
|---|---|
| injection suspected, or intent MISALIGNED | `UNSAFE` |
| intent SUSPICIOUS, or ambiguity HIGH | `SUSPICIOUS` |
| aligned, low ambiguity, no injection | `SAFE` |
| INVALID / TIMEOUT / UNAVAILABLE | the same name |
| SKIPPED for a tool that requires a check | `UNAVAILABLE` (no check happened) |

`provider` is `MOCK`, `REPLAY` or `QUALCOMM` (a real LIVE answer), or `NONE` when
nothing answered. The guard's outcome enum has no BLOCK, so the semantic layer
cannot express one.

## What the guard does with it (backend behaviour, unchanged)

- A rule BLOCK stays BLOCK for every semantic result.
- `SUSPICIOUS` and `UNSAFE` can raise APPROVE to REVIEW; `SAFE` changes nothing.
- A required check that comes back INVALID, TIMEOUT or UNAVAILABLE fires
  `SEMANTIC_REQUIRED_UNAVAILABLE`: REVIEW.
- REVIEW needs a human approval; a BLOCK cannot be approved; execution takes a
  decision id only, checks integrity, revalidates deterministically and runs the
  stored canonical action (extra fields sent to `/execute` are ignored).

## Which tools require a semantic check

From `config/tools.yaml`, matching the blueprint (tools marked "always": email
send, file delete, payment transfer; the three reads are "never"):

| Tool | `requires_semantic` |
|---|---|
| `calendar_read`, `email_read`, `web_fetch` | false |
| `email_send`, `file_delete`, `payment_transfer` | true |

Reads stay protected without a model: a fetched page is recorded as untrusted
observed content, and a later action that reuses a value from it is flagged by
the origin check (`agent/provenance.py`), which fires the deterministic
provenance rules. After an `email_read`, an `email_send` in the same session
triggers the session-flow rule.

## Scenarios

`scenarios/*.json` is the single source of truth. The scenario runner, the tests
and the `/api/demo/*` routes all load these files. Provenance is not scripted:
it is worked out at run time from the content observed in that run.

Scenario 4 end to end (real guard, mock mode): `web_fetch` of the injected page
is APPROVE and runs; the page text is recorded as external content; the transfer
to the account named on the page is BLOCK (`LARGE_PAYMENT`,
`PAYMENT_EXTERNAL_PROVENANCE`, `EXTERNAL_CONTENT_MUTATION`), and stays BLOCK when
the semantic checker is compromised (says SAFE) or unavailable.

## Tests that protect this

- `tests/integration/test_http_evaluate_semantic.py`: the HTTP route refuses a
  supplied result, runs the check itself, fails closed on every provider failure,
  keeps every rule BLOCK, calls the model once and never at execution, runs the
  stored action, and never leaks a key.
- `tests/integration/test_semantic_integration.py`: 14 tool/argument cases ×
  11 provider behaviours through the real engine; semantic output never creates
  a BLOCK and never weakens a decision.
- `tests/integration/test_demo_wiring.py`, `test_replay_reproducibility.py`.

## Known limits

- **`context` over HTTP is caller-supplied.** `POST /evaluate` accepts
  `user_task` and `observed_external_content` in `context`, as the backend
  contract defines. They only feed the advisory semantic check: they cannot lower
  a decision or relax a rule, and provenance (`arg_provenance`) cannot be sent over
  HTTP at all.
- **Risk score and semantic.** `UNSAFE` adds 0.15 to the risk score, which feeds
  the 0.85 BLOCK threshold. With the current configuration the largest score
  reachable without a rule BLOCK is below that, so a semantic result cannot cause
  a BLOCK; `test_semantic_never_creates_a_block_and_never_weakens_a_decision`
  includes the highest-risk case and fails if a configuration change breaks this.
- **Delete is one path per action.** The backend's delete tool takes a single
  path, so scenario 3 is a single reversible delete, not the blueprint's 25-path
  bulk delete.
- **Qualcomm LIVE** is not implemented; see `docs/qualcomm-spike.md`.
