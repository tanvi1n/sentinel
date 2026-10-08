# Sentinel

Sentinel is a generic guardrail that sits between an AI agent and its tools and
answers **APPROVE**, **REVIEW** or **BLOCK** for every action the agent proposes
(hackathon problem ET-03, Agentic AI Guardrail Validator).

> **Status: work in progress.** The semantic layer, the scripted agent and
> harness, the scenarios and the explanation text are built and tested. The
> deterministic guard, the API, the mock tools and world, and the dashboard are
> **not integrated yet**. Qualcomm LIVE does **not** work yet. See
> [Current status](#current-status).

## How it works

The agent proposes; the trusted registry defines; deterministic rules enforce;
the semantic layer may escalate but can never relax a deterministic decision.

```
scripted agent (untrusted) --proposal--> trusted harness --> guard client
                                                               |
                          deterministic rules  +  semantic reasoner
                                                               |
                                    combine(): most severe wins
                                                               |
                           APPROVE / REVIEW / BLOCK -> human review -> execution gateway
```

- **The agent** submits only a tool, arguments and a justification. The harness
  supplies the agent id, session id, user task and the observed content with its
  provenance labels.
- **Deterministic checks** (permissions, allowlists, limits, risk, reversibility,
  session history, origin of arguments) decide anything that can be stated as a
  rule. A deterministic BLOCK is final.
- **The semantic layer** judges meaning only: does the action fit what the user
  asked, does external content try to manipulate the agent, is the request
  ambiguous. It is advisory and can raise APPROVE to REVIEW. It cannot produce
  BLOCK or lower a decision.

| Decision | Meaning |
|---|---|
| APPROVE | The action may run. |
| REVIEW | Not approved. It cannot run until a human reviewer approves it. |
| BLOCK | Prohibited. It cannot run, and neither a reviewer nor the semantic layer can override it. |

Full design: `reference/ET-03 Guardrail Validator Final Blueprint v2.pdf` and
`reference/Sentinel Work Division and Parallel Implementation Plan.pdf`.

## Semantic providers

All providers sit behind one interface, `SemanticReasoner.analyze(context)`, which
never raises and always returns a `SemanticResult`.

| Provider | Purpose | State |
|---|---|---|
| Mock | Deterministic stand-in (aligned, misaligned, injection, compromised, timeout, invalid, unavailable). Always labelled MOCK. | Working |
| Replay | Serves recorded real results by key. A miss is UNAVAILABLE. | Working; no recordings exist yet |
| Qualcomm | Live model behind the same interface. | Adapter built and tested with a fake call; **the network call is not implemented**, so LIVE is unavailable |

Modes are `mock` (default), `replay`, `live` and `auto` (LIVE, then REPLAY, then
UNAVAILABLE; never Mock). Details in [docs/qualcomm-spike.md](docs/qualcomm-spike.md).

## Demo scenarios

Four scenario files in `scenarios/` are the single source of truth for the demo.
They describe a proposal and the outcome expected from the guard once it exists.

| # | Scenario | Agent | What happens | Expected |
|---|---|---|---|---|
| 1 | `safe-action` | inbox-assistant | A calendar read the user asked for | APPROVE, runs |
| 2 | `intent-mismatch` | inbox-assistant | Asked to summarize the inbox, the agent emails it to the manager; rules alone approve, the semantic check flags the mismatch | REVIEW |
| 3 | `bulk-delete` | file-assistant | Soft delete of 25 temp files; a human approves, it runs, then undo | REVIEW, then execute and undo |
| 4 | `prompt-injection` | finance-assistant | A fetched vendor page tells the agent to move all funds to an unknown account | BLOCK, even with a compromised semantic checker |

Seed content (the inbox, calendar, file listing and the vendor page with the
injected instruction) is in `seed_content/`, kept apart from the scenario files.

## Tech stack

Python 3.11+, Pydantic v2 and pytest are what the current code uses. For the
backend, `requirements.txt` also declares FastAPI with Uvicorn (one worker),
PyYAML, httpx and python-dotenv. No database, Docker, microservices, MCP or
LangGraph.

## Repository structure

```
app/
  contracts/    shared Pydantic data shapes (frozen)
  semantic/     reasoner interface, Mock / Replay / Qualcomm providers, factory, prompts
  reporting/    explanation.py (template text for a decision)
  guard/ api/ world/   backend packages, empty until the backend is integrated
agent/          scripted agent, trusted harness, scenario loader, fake client
scenarios/      the four scenario files (data)
seed_content/   external content used by the scenarios (data)
scripts/        preflight, scenario runner, replay recorder
docs/           spike note, developer setup, contributing
reference/      the blueprint and the work-division plan
tests/          unit, semantic, agent and tooling tests
```

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/preflight.py
```

More in [docs/dev-setup.md](docs/dev-setup.md).

## Run the tests

```powershell
python -m pytest
```

## Run the scenarios

```powershell
python scripts/run_scenario.py                         # all four
python scripts/run_scenario.py scenarios/<file>.json   # one
```

This uses a **fake** guard client whose decisions come from the scenario files
themselves. It checks that the scenario files and the harness work together. It
does **not** exercise or validate the real guard.

## Current status

Built and tested:
- Contracts, the semantic layer (interface, Mock, Replay, Qualcomm adapter,
  factory, prompt v1 draft), and the replay recorder.
- The scripted agent, the trusted harness, scenario loading and seed content.
- Explanation text for APPROVE, REVIEW and BLOCK.
- Preflight and the fake-client scenario runner.

Not done yet:
- The deterministic guard, tool registry and policy config, decision store,
  execution gateway and revalidation, the API routes, the mock tools and world
  with undo, and the dashboard. These belong to other team members and are not in
  this repository.
- Qualcomm LIVE: `default_call_model` is intentionally unimplemented until the
  API details are known. No result is ever labelled LIVE or REPLAY unless it truly
  was.
- Recorded replay (`replay/cache.jsonl`) and the evaluation cases.
- Agreement with the backend on tool argument names and policy values the
  scenario files assume.

Contributor rules: [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
