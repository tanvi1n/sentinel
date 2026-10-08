# Developer setup

Commands are for Windows PowerShell. The project targets Python 3.11 or newer.

## 1. Python environment

```powershell
python --version                  # 3.11 or newer
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # cmd.exe: .venv\Scripts\activate.bat
```

If PowerShell blocks the activation script, skip activation and call the
environment's Python directly in every command below:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 2. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

`requirements.txt` lists pydantic (v2), pytest, FastAPI, Uvicorn, PyYAML, httpx
and python-dotenv. The code in the repository today needs only pydantic and
pytest (python-dotenv is used optionally by `scripts/record_replay.py`). The
rest are declared for the backend that is not integrated yet.

## 3. Check your setup

```powershell
python scripts/preflight.py            # quick checks
python scripts/preflight.py --tests    # also runs the test suite
```

It prints PASS, WARN or FAIL per check and exits non-zero only for a required
failure. WARNs are expected right now (see section 6).

## 4. Run the tests

```powershell
python -m pytest              # whole suite
python -m pytest tests/semantic -q
```

No network access or API key is needed. `pytest.ini` puts the repository root on
the import path.

## 5. Run the scenarios (fake client)

```powershell
python scripts/run_scenario.py                                  # all scenario files
python scripts/run_scenario.py scenarios/<file>.json            # one file
```

This runs each scenario file through the real harness with a **fake** guard
client. The decisions come from the scenario file's own expectations. No rules
and no model run, so it checks that the scenario files and the harness fit
together and nothing more. It does not validate the real guard.

## 6. Semantic configuration (high level)

- Provider selection is by mode: `mock` (default), `replay`, `live`, `auto`.
  `auto` tries LIVE, then REPLAY, then reports UNAVAILABLE; it never uses Mock.
- Settings are read from environment variables whose names start with
  `QUALCOMM_` or `SENTINEL_`. The full list is in `docs/qualcomm-spike.md`. A
  `.env.example` template is meant to list them; preflight warns if it is
  missing from your checkout. Real values stay in a local, git-ignored `.env` and
  are never committed.
- Without configuration, everything runs in mock mode.

## 7. What is not available yet

- **Qualcomm LIVE:** `call_model` is not implemented and the API details are
  unknown, so LIVE reports UNAVAILABLE. See `docs/qualcomm-spike.md`.
- **Replay cache:** `replay/cache.jsonl` does not exist until a real LIVE result
  is recorded.
- **Backend:** `app/guard/`, `app/api/` and `app/world/` have no modules yet. There
  is no real `GuardClient`, no web service, and no mock tools or world state.
- **Dashboard:** `static/` is not part of this repository yet.

## 8. Needed once Person 2's backend is integrated

- `config/tools.yaml` and `config/policies.yaml`, loaded with PyYAML.
- The real `GuardClient` replacing `agent/fake_client.py` in the harness.
- Agreement on the tool argument names and policy values the scenario files use.
- The FastAPI service (one Uvicorn worker, per the architecture) and its demo
  routes calling the harness.
- Building `SemanticContext` through `build_semantic_context` with the registry's
  tool descriptions and origin-check flags, so replay keys match production.
- Recording replay from real LIVE results once access exists.
