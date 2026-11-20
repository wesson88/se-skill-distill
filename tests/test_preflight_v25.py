from pathlib import Path
import os
import subprocess
import sys
from conftest import load_script, SCRIPTS


def test_preflight_checks_semantic_capability(monkeypatch):
    preflight = load_script("preflight.py")
    from skillmind import distillation
    monkeypatch.setattr(distillation, "SCHEMA_VERSION", "incompatible")
    status, message = preflight._check_skillmind()
    assert status == "fail"
    assert "semantic_distillation_v1" in message


def test_explicit_vault_overrides_missing_environment(isolated, monkeypatch):
    monkeypatch.delenv("SE_VAULT", raising=False)
    preflight = load_script("preflight.py")
    assert preflight._check_vault(str(isolated.VAULT_ROOT))[0] == "ok"


def test_pipeline_help_does_not_require_vault(tmp_path):
    env = {k:v for k,v in os.environ.items() if k != "SE_VAULT"}
    result = subprocess.run([sys.executable, str(SCRIPTS/"pipeline.py"), "--help"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--vault" in result.stdout


def test_distill_help_does_not_require_vault(tmp_path):
    env = {k:v for k,v in os.environ.items() if k != "SE_VAULT"}
    result = subprocess.run([sys.executable, str(SCRIPTS/"distill_url.py"), "--help"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
