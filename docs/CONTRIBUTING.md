# Contributing

Sentinel's architecture is frozen. The sources of truth are the two PDFs in
`reference/`: the blueprint and the work-division plan. Read them before changing
anything, and do not redesign what they decide.

## Ownership

Each folder has one owner, and you edit only your own folders. A change in someone
else's folder goes through them.

| Owner | Folders |
|---|---|
| Person 1 (Risha) | `app/semantic/`, `app/contracts/semantic.py`, `agent/`, `scenarios/`, `seed_content/`, `replay/`, `app/reporting/explanation.py`, `scripts/`, `docs/`, root files |
| Person 2 | `app/guard/`, `app/api/`, `app/world/`, `config/`, `app/reporting/audit.py`, and the other contract files |
| Person 3 | `static/` |

Contract files in `app/contracts/` can change only with the contract owner's
approval.

## Rules that protect the architecture

- **The semantic layer is advisory.** It may raise APPROVE to REVIEW. It cannot
  produce BLOCK, cannot lower a decision, and can never relax a deterministic
  BLOCK. Do not add anything that lets a model output decide.
- **Keep providers behind `SemanticReasoner`.** Callers use `analyze()` and get a
  `SemanticResult`. A provider implements `_analyze()`; it does not raise to
  callers, and it never labels a result LIVE or REPLAY unless it truly was.
- **No production guard logic outside the guard.** `agent/`, the fake client and
  the scenario code simulate and exercise Sentinel; they do not decide
  permissions, risk, reversibility or approval. `agent/fake_client.py` is a
  stand-in and must never be treated as a real `GuardClient`.
- **Scenario files are data and test cases.** They hold a proposal and the outcome
  we expect. Do not add scenario-specific or tool-specific branches to the loader,
  harness, runner or any engine code. Behaviour for a particular domain belongs in
  configuration, scenarios, seed content or an adapter.
- **Trusted fields come from the harness.** The agent proposes a tool, arguments
  and a justification and nothing else.
- **Keep it small.** The architecture lists what is not used (database, Docker,
  microservices, MCP, LangGraph, a frontend framework, a rule language). Do not
  add dependencies or frameworks without a reason the plan supports.

## Before you commit

```powershell
python -m pytest
python scripts/preflight.py
```

`main` should always install and pass the tests that exist. Never commit `.env`,
real keys, local logs or audit files, or virtual environments.

## Git conventions (from the work-division plan)

- Short-lived branches named `risha/<topic>`, `p2/<topic>` or `p3/<topic>`, one
  topic each.
- Commit messages look like `type(area): short message`, with type `feat`, `fix`,
  `test`, `docs` or `chore`. Example: `feat(semantic): add replay reasoner`.
- Pull `main` and run the tests before merging.
