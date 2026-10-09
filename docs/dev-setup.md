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
and python-dotenv. The code and tests need pydantic, pytest, FastAPI and PyYAML.
Uvicorn serves the API, python-dotenv is used optionally by
`scripts/record_replay.py`, and httpx is declared for the future Qualcomm call.

## 3. Check your setup

```powershell
python scripts/preflight.py            # quick checks
python scripts/preflight.py --tests    # also runs the test suite
```

It prints PASS, WARN or FAIL per check and exits non-zero only for a required
failure. WARNs are expected right now (see section 7).

## 4. Run the tests

```powershell
python -m pytest              # whole suite
python -m pytest tests/semantic -q
```

No network access or API key is needed. `pytest.ini` puts the repository root on
the import path.

## 5. Run the scenarios

```powershell
python scripts/run_scenario.py                                  # all scenario files
python scripts/run_scenario.py scenarios/<file>.json            # one file
python scripts/run_scenario.py --semantic-mode auto             # see the fail-closed path
```

This runs each scenario file through the trusted harness, the semantic layer and
the **real** guard, tools and mock world, in one process. State is reset before
each run, and a "simulated human" plays the reviewer where the file says so. In
the default mock mode the file picks the mock semantic variant per step. In
`replay`, `live` or `auto` there is no provider yet, so required semantic checks
come back UNAVAILABLE and the guard moves to REVIEW; the runner reports any step
that then differs from the file.

Serve the API with `python -m uvicorn app.api.main:app --workers 1 --env-file .env`
(one worker: all state is in memory). Use `--explain` with the runner to print
each decision's explanation.

## 6. Semantic configuration (high level)

- Provider selection is by mode: `mock` (default), `replay`, `live`, `auto`.
  `auto` tries LIVE, then REPLAY, then reports UNAVAILABLE; it never uses Mock.
- Settings are environment variables starting with `QUALCOMM_` or `SENTINEL_`,
  listed with empty placeholders in `.env.example`. Copy it to `.env` (git-ignored)
  for local values. The scripts and `uvicorn --env-file .env` read it; values
  already set in the shell win. Preflight fails if `.env.example` ever holds a key.
- Without configuration, everything runs in mock mode.
- The test suite ignores your shell and `.env`: every test starts in mock mode
  with no key (`tests/conftest.py`).

## 7. What is not available yet

- **Qualcomm LIVE:** `call_model` is not implemented and the API details are
  unknown, so LIVE reports UNAVAILABLE. See `docs/qualcomm-spike.md`.
- **Replay recordings:** none exist until a real LIVE result is recorded.
  `replay/cache.jsonl` is local and git-ignored; `replay/demo.jsonl` is the
  reviewed file meant to be committed (see `replay/README.md`).
- **Dashboard:** `static/` is not part of this repository yet.

Known limits are listed in
`docs/semantic-integration.md`.
