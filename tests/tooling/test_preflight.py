import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "preflight.py"


@pytest.fixture(scope="module")
def pf():
    spec = importlib.util.spec_from_file_location("preflight", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_current_repository_has_no_failures(pf):
    results = pf.run_checks(ROOT)
    assert [r for r in results if r.level == "FAIL"] == []
    assert pf.exit_code(results) == 0
    assert any(r.level == "PASS" for r in results)


def test_suite_is_not_run_by_default(pf):
    suite = next(r for r in pf.run_checks(ROOT) if r.name == "test suite")
    assert suite.level == "WARN" and "--tests" in suite.detail


def test_missing_project_files_fail(pf, tmp_path):
    results = pf.run_checks(tmp_path)
    failed = {r.name for r in results if r.level == "FAIL"}
    assert {"dir app/contracts", "dir agent", "file requirements.txt", "scenario files"} <= failed
    assert pf.exit_code(results) == 1


def test_warnings_alone_do_not_fail(pf):
    results = [pf.Result("PASS", "a"), pf.Result("WARN", "b"), pf.Result("WARN", "c")]
    assert pf.exit_code(results) == 0
    assert pf.exit_code(results + [pf.Result("FAIL", "d")]) == 1


def test_backend_dirs_warn_when_empty_and_pass_when_populated(pf, tmp_path):
    (tmp_path / "app" / "guard").mkdir(parents=True)
    (tmp_path / "app" / "guard" / "__init__.py").write_text("")
    by_name = {r.name: r for r in pf.check_backend(tmp_path)}
    assert by_name["backend app/guard"].level == "WARN"
    (tmp_path / "app" / "guard" / "engine.py").write_text("")
    by_name = {r.name: r for r in pf.check_backend(tmp_path)}
    assert by_name["backend app/guard"].level == "PASS"
    assert by_name["backend app/api"].level == "WARN"  # absent directory


def test_cli_exit_code_and_output_shape():
    proc = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout.startswith("Sentinel preflight")
    assert "0 FAIL" in proc.stdout


def test_cli_returns_one_on_a_broken_root(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--root", str(tmp_path)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 1 and "FAIL" in proc.stdout


def test_output_never_contains_configuration_values():
    secret = "sk-preflight-test-9f3a1c"
    env = {**os.environ, "QUALCOMM_API_KEY": secret, "QUALCOMM_MODEL": "model-xyz",
           "QUALCOMM_BASE_URL": "https://host.invalid"}
    proc = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True,
                          text=True, cwd=ROOT, env=env)
    assert secret not in proc.stdout + proc.stderr
    assert "host.invalid" not in proc.stdout + proc.stderr


def test_preflight_is_generic():
    src = SCRIPT.read_text(encoding="utf-8")
    for token in ("safe-action", "intent-mismatch", "bulk-delete", "prompt-injection",
                  "calendar.read", "email.send", "file.delete", "payment.transfer", "web.fetch"):
        assert token not in src
