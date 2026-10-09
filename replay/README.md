# Replay recordings

Replay serves real, previously recorded Qualcomm (LIVE) answers when the model
is not reachable. A replay hit is labelled REPLAY with its recording time; a miss
is UNAVAILABLE, and for a tool that requires a semantic check the guard then
moves to REVIEW. Replay never invents an answer, and mock results are never
accepted as recordings.

## Files

| File | Tracked in Git | What it is |
|---|---|---|
| `cache.jsonl` | No (ignored) | Default output of `scripts/record_replay.py` and default `SENTINEL_REPLAY_PATH`. Local, machine-specific. |
| any other `*.jsonl` | No (ignored) | Ad hoc local recordings. |
| `demo.jsonl` | Yes, once it exists | A reviewed copy of LIVE answers to the four scenario inputs, so the demo is reproducible from a clean checkout. It does not exist yet: nothing has been recorded from a real Qualcomm call. |

Each line is one record: `key`, `recorded_at`, `provider`, `prompt_version`,
`source` (always `LIVE`) and `findings`. The key is a SHA-256 of the canonical
model input plus the prompt version, so a changed prompt or input is a miss.
Records hold no API key, and the prompt never contains one.

## Producing demo.jsonl (after Qualcomm access works)

1. Freeze the prompt version first; changing it later invalidates every record.
2. `python scripts/record_replay.py --from-scenarios --out replay/demo.jsonl`
   (runs the scenarios through the real harness and guard in live mode; exits 1
   and writes nothing for any answer that is not a real LIVE one).
3. Review the file: only the scenario inputs (synthetic demo data), no personal
   data, no key or token anywhere.
4. Check it replays: `SENTINEL_REPLAY_PATH=replay/demo.jsonl` (PowerShell:
   `$env:SENTINEL_REPLAY_PATH="replay/demo.jsonl"`), then
   `python scripts/run_scenario.py --semantic-mode replay`.
5. Commit `replay/demo.jsonl` only.
