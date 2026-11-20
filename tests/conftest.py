"""Offline tests always use isolated vault, SkillMind home and state."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
import sys
import tempfile

import pytest

_session = tempfile.TemporaryDirectory(prefix="se-distill-tests-")
_root = Path(_session.name)
for name in ("00-系统", "20-知识"):
    (_root / "vault" / name).mkdir(parents=True, exist_ok=True)
os.environ["SE_VAULT"] = str(_root / "vault")
os.environ["SE_STATE_DIR"] = str(_root / "state")
os.environ["SKILLMIND_HOME"] = str(_root / "skillmind")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(filename):
    name = "se_test_" + Path(filename).stem
    spec = spec_from_file_location(name, SCRIPTS / filename)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import _common as common
    vault = tmp_path / "vault"
    for name in ("00-系统", "20-知识"):
        (vault / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SE_VAULT", str(vault))
    common.set_vault_root(str(vault))
    state = tmp_path / "state"
    for name, value in {
        "STATE_DIR": state, "AUDIT_DIR": state / "audit",
        "LEDGER_FILE": state / "injection_ledger.jsonl",
        "APPROVED_FILE": state / "audit/approved.jsonl",
        "REVIEW_FILE": state / "audit/manual-review.jsonl",
        "REJECT_FILE": state / "audit/rejects.jsonl",
        "UNKNOWN_FILE": state / "unknown-queue.md",
    }.items():
        monkeypatch.setattr(common, name, value)
    return common
