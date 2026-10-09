# Sentinel

Sentinel is a generic guardrail that sits between an AI agent and its tools and
answers **APPROVE**, **REVIEW** or **BLOCK** for every action the agent proposes
(hackathon problem ET-03, Agentic AI Guardrail Validator).

> **Status: integrated, with one stub.** The deterministic guard, API, mock world
> and tools, the semantic layer and the scripted agent run together in one
> process, and the four demo scenarios pass against the real guard. **Qualcomm
> LIVE does not work yet**: its network call is not implemented, so LIVE reports
> UNAVAILABLE. See [Current status](#current-status).

## How it works

The agent proposes; the trusted registry defines; deterministic rules enforce;
the semantic layer may escalate but can never relax a deterministic decision.

```
scripted agent (untrusted) --request--> trusted harness
                                           |  builds the proposal, records observed
                                           |  content, works out argument origin
                                           v
                  semantic layer (advisory) ---> GuardClient.evaluate(proposal, semantic result)
                                                     |
                                    intake -> rules -> risk -> combine()
                                                     |
                              APPROVE / REVIEW / BLOCK -> human review -> gateway
                              (integrity check, revalidation, run, undo) -> mock world
```

- **The agent** submits only a tool and arguments. The harness supplies the agent
  id, session, user task and the content observed during the run.
- **Deterministic checks** (capabilities, limits, risk, session flow, argument
  origin) decide what can be stated as a rule. A deterministic BLOCK is final.
- **The semantic layer** judges meaning only: does the action fit what the user
  asked, does external content try to steer the agent. Its result is advisory.
  `combine()` can raise APPROVE to REVIEW on it; it can never produce BLOCK or
  lower a decision. If the check is required and the provider fails, the guard
  moves to REVIEW. The check always runs on the server: `POST /evaluate` refuses a
  caller-supplied `semantic_result` (422).

| Decision | Meaning |
|---|---|
| APPROVE | The action may run. |
| REVIEW | Not approved. It cannot run until a human reviewer approves it. |
| BLOCK | Prohibited. It cannot run, and neither a reviewer nor the semantic layer can override it. |

Full design: `reference/` (the blueprint and the work-division plan, kept locally).
How the semantic layer connects to the backend: [docs/semantic-integration.md](docs/semantic-integration.md).

## Semantic providers

All providers sit behind one interface, `SemanticReasoner.analyze(context)`, which
never raises. `app/semantic/bridge.py` turns a provider's result into the guard's
`SemanticResult` (SAFE / SUSPICIOUS / UNSAFE, or INVALID / TIMEOUT / UNAVAILABLE).

| Provider | Purpose | State |
|---|---|---|
| Mock | Deterministic stand-in (aligned, misaligned, injection, compromised, timeout, invalid, unavailable). Always labelled MOCK. | Working |
| Replay | Serves recorded real results by key. A miss is UNAVAILABLE. | Working; no recordings exist yet |
| Qualcomm | Live model behind the same interface. | Adapter built and tested with a fake call; **the network call is not implemented**, so LIVE is unavailable |

Modes are `mock` (default), `replay`, `live` and `auto` (LIVE, then REPLAY, then
UNAVAILABLE; never Mock). Switch with `POST /api/admin/semantic-mode`; `GET
/api/health` reports the mode and whether a key is configured (never the key).
Details in [docs/qualcomm-spike.md](docs/qualcomm-spike.md).

## Demo scenarios

The four files in `scenarios/` describe the demo. `python scripts/run_scenario.py`
runs them through the harness, the semantic layer and the real guard.

| # | Scenario | What happens | Outcome |
|---|---|---|---|
| 1 | `safe-action` | A calendar read the user asked for | APPROVE, runs |
| 2 | `intent-mismatch` | Asked to summarize the inbox, the agent emails it out; the semantic check flags it | REVIEW; the demo reviewer rejects |
| 3 | `bulk-delete` | A reversible file delete; a human approves, it runs, then undo restores the world | REVIEW, execute, undo |
| 4 | `prompt-injection` | A fetched page tells the agent to pay an account; the harness flags that argument as external | BLOCK, even with a compromised semantic checker |

The `/api/demo/*` routes load the same files, so there is one source of truth.
Provenance is never scripted: it is worked out from the content observed in the run.

## Tech stack

Python 3.11+, FastAPI (one Uvicorn worker, in-memory state), Pydantic v2, PyYAML
for the tool and policy config, pytest. No database, Docker, microservices, MCP or
LangGraph.

## Repository structure

```
app/
  contracts/    shared data shapes (the backend's contracts are authoritative)
  guard/        intake, registry, rules, risk, combine, store, gateway, client
  api/          FastAPI routes (evaluate, review, execute, audit, admin, demo)
  world/        mock world state and tools with undo
  audit/        audit log
  semantic/     reasoner interface, Mock / Replay / Qualcomm, factory, bridge, prompts
  reporting/    explanation.py (template text for a decision)
config/         tools.yaml and policies.yaml
agent/          scripted agent, trusted harness, scenario loader
scenarios/      the four scenario files (data)
replay/         replay recordings (local cache ignored; see replay/README.md)
scripts/        preflight, scenario runner, replay recorder
docs/           spike note, integration notes, developer setup, contributing
tests/          unit, security, lifecycle, scenarios, integration, semantic, agent, tooling
```

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env        # optional; the defaults run in mock mode
python scripts/preflight.py
python -m uvicorn app.api.main:app --workers 1 --env-file .env   # serve the API
```

More in [docs/dev-setup.md](docs/dev-setup.md).

## Run the tests

```powershell
python -m pytest
```

## Run the scenarios

```powershell
python scripts/run_scenario.py                         # all four, mock semantic mode
python scripts/run_scenario.py scenarios/<file>.json   # one
python scripts/run_scenario.py --semantic-mode auto    # no provider yet: fails closed to REVIEW
python scripts/run_scenario.py --explain               # print each decision's explanation
```

## Current status

Working and tested:
- The guard, API, mock world with undo, and audit log (backend).
- The semantic layer wired to the backend: `POST /evaluate`, the demo routes and
  the scenario runner run it server-side for every tool that requires a check,
  and the mode switch drives it.
- Security invariants checked through the real engine: a semantic result never
  creates a BLOCK, never lowers a decision, and cannot relax a rule BLOCK; a
  required check that fails becomes REVIEW.
- Scripted agent, trusted harness, scenario files, explanation text, preflight.

Not done yet:
- **Qualcomm LIVE:** `default_call_model` is intentionally unimplemented until the
  API details are known. No result is ever labelled LIVE or REPLAY unless it truly was.
- Recorded replay: `replay/demo.jsonl` can only be produced once LIVE works
  ([replay/README.md](replay/README.md)). The evaluation cases are not written yet.
- The dashboard (`static/`) is not part of this repository yet.
- Known limits are listed in [docs/semantic-integration.md](docs/semantic-integration.md).

Contributor rules: [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
