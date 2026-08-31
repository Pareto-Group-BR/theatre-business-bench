from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.v2 import (
    audit_preregistration,
    extract_actions,
    validate_role_output,
    validate_theatre_handoff,
)


class V2ProtocolTests(unittest.TestCase):
    def test_repository_preregistration_is_frozen_and_hash_complete(self) -> None:
        report = audit_preregistration()
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(len(report["paired_seeds"]), 5)
        self.assertEqual(len(report["observed_hashes"]), 8)

    def test_tampered_artifact_fails_without_writing(self) -> None:
        source = json.loads((ROOT / "preregistration" / "v2.json").read_text(encoding="utf-8"))
        source["artifacts"]["shared_corpus"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            report = audit_preregistration(path)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("shared_corpus SHA-256 mismatch" in error for error in report["errors"]))

    def test_functional_asymmetry_fails(self) -> None:
        source = json.loads((ROOT / "preregistration" / "v2.json").read_text(encoding="utf-8"))
        source["functional_parity"]["control_responsibilities"].remove("strategic_challenge")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            report = audit_preregistration(path)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("same four" in error for error in report["errors"]))

    def test_critical_verdict_requires_executable_verification(self) -> None:
        report = validate_role_output("critic", {
            "verdict": "critical",
            "correction": {"required": False, "required_action_types": [], "verification": []},
        })
        self.assertEqual(report["status"], "failed")
        self.assertEqual(len(report["errors"]), 3)

    def test_actor_actions_are_plan_bound_and_capacity_checked(self) -> None:
        payload = {
            "plan_adherence": "followed",
            "action_capacity": {"limit": 14, "used": 1, "unused_reason": "other actions lack evidence"},
            "execution_queue": [{
                "plan_item_id": "P1",
                "action": {"type": "collect_cash"},
                "expected_effect": "move machine cash to bank",
            }],
        }
        self.assertEqual(validate_role_output("actor", payload)["status"], "passed")
        self.assertEqual(extract_actions("actor", payload), [{"type": "collect_cash"}])

        payload["action_capacity"]["used"] = 0
        self.assertEqual(validate_role_output("actor", payload)["status"], "failed")

    def test_control_must_carry_strategic_parity(self) -> None:
        report = validate_role_output("control", {
            "audit": {"verdict": "on_track"},
            "strategic_challenge": {"alternative_hypotheses": ["only one"]},
            "plan": {"action_queue": []},
            "action_capacity": {"limit": 14, "used": 0},
            "execution_queue": [],
        })
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("three alternative" in error for error in report["errors"]))

    def test_theatre_handoff_confronts_correction_plan_and_execution(self) -> None:
        critic = {
            "verdict": "critical",
            "correction": {
                "required": True,
                "id": "C1",
                "required_action_types": ["restock"],
                "verification": ["machine stock increases"],
            },
        }
        planner = {
            "capital_budget": {},
            "action_queue": [{"id": "P1", "action_type": "restock"}],
            "correction_binding": {"correction_id": "C1", "queue_item_ids": ["P1"]},
        }
        actor = {
            "plan_adherence": "followed",
            "action_capacity": {"limit": 14, "used": 1},
            "execution_queue": [{
                "plan_item_id": "P1",
                "action": {"type": "restock", "sku": "water", "units": 12},
            }],
        }
        self.assertEqual(validate_theatre_handoff(critic, planner, actor)["status"], "passed")
        actor["execution_queue"][0]["plan_item_id"] = "P9"
        report = validate_theatre_handoff(critic, planner, actor)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("unknown plan item" in error for error in report["errors"]))
        self.assertTrue(any("did not execute" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
