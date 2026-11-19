"""se 对 skillmind 2.4 verdict 的消费。不调用模型，不需要 vault。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audit_decision import attach_yaml_roles, interpret_report  # noqa: E402


def _summary(**overrides) -> dict:
    data = {
        "coverage": 96.0,
        "verdict": "PASS",
        "needs_human_review": False,
        "missing": [],
        "completeness": {"status": "PASS"},
        "executability": {"status": "PASS", "faithfulness": "PASS"},
        "hallucination": {"checked": True, "count": 0},
        "quality_current": True,
    }
    data.update(overrides)
    return data


class InterpretTests(unittest.TestCase):
    def test_pass_only_when_verdict_pass(self) -> None:
        decision = interpret_report(_summary())
        self.assertEqual(decision["verdict"], "PASS")
        self.assertEqual(decision["route"], "approved")
        self.assertFalse(decision["needs_human_review"])
        self.assertEqual(decision["hallucination_rate"], 0.0)
        self.assertTrue(decision["hallucination_checked"])

    def test_high_coverage_fail_does_not_approve(self) -> None:
        decision = interpret_report(_summary(
            coverage=95.0,
            verdict="FAIL",
            needs_human_review=True,
            completeness={"status": "FAIL"},
            executability={"status": "PASS", "faithfulness": "PASS"},
        ))
        self.assertEqual(decision["route"], "manual-review")
        self.assertTrue(decision["needs_human_review"])

    def test_unknown_is_not_pass_and_has_no_fake_hallucination_zero(self) -> None:
        decision = interpret_report({
            "coverage": None,
            "verdict": "UNKNOWN",
            "completeness": {"status": "UNKNOWN"},
            "executability": {"status": "UNKNOWN", "faithfulness": "UNKNOWN"},
        })
        self.assertEqual(decision["verdict"], "UNKNOWN")
        self.assertEqual(decision["route"], "manual-review")
        self.assertFalse(decision["hallucination_checked"])
        self.assertIsNone(decision["hallucination_rate"])
        self.assertIsNone(decision["hallucination_count"])
        self.assertTrue(any("not 0" in note for note in decision["notes"]))

    def test_yaml_role_survives_skip_extract(self) -> None:
        cached = [{
            "source_hash": "abc",
            "source_path": "/work/sources/security-and-hardening/SKILL.md",
        }]
        attach_yaml_roles(cached, [{
            "kind": "local_dir",
            "path": "/work/sources/security-and-hardening",
            "target_role": "架构师",
        }])
        self.assertEqual(cached[0]["target_role"], "架构师")


class StoredAuditReplayTests(unittest.TestCase):
    def test_previous_five_reports_do_not_auto_approve(self) -> None:
        root = Path("/tmp/sm-verify/se-distill-2.4.0-20261118/out/audit-24")
        if not root.exists():
            self.skipTest("previous verification pack is not on this machine")
        routes = {}
        for path in sorted(root.glob("*.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            decision = interpret_report(report)
            routes[path.stem] = decision["route"]
            self.assertNotEqual(decision["verdict"], "PASS")
            self.assertIsNone(decision["hallucination_rate"])
            self.assertEqual(decision["route"], "manual-review")
        self.assertEqual(len(routes), 5)


if __name__ == "__main__":
    unittest.main()
