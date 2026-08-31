from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from theatre_business_bench.v3 import (
    PREREGISTRATION,
    audit_preregistration,
    validate_repair_envelope,
    validate_role_output,
    validate_theatre_handoff,
)


def critic() -> dict:
    return {
        "verdict": "critical",
        "correction": {
            "required": True,
            "id": "C1",
            "required_action_types": ["place_order", "restock"],
            "verification": ["inventory is delivered", "machine stock increases"],
        },
    }


def planner() -> dict:
    return {
        "capital_budget": {},
        "action_queue": [
            {"id": "P1", "action_type": "place_order", "timing": "now", "precondition": "already_satisfied"},
            {"id": "P2", "action_type": "restock", "timing": "conditional_future", "precondition": "order P1 has arrived in storage"},
        ],
        "correction_binding": {
            "correction_id": "C1",
            "immediate_queue_item_ids": ["P1"],
            "conditional_queue_item_ids": ["P2"],
        },
    }


def actor() -> dict:
    return {
        "plan_adherence": "followed",
        "action_capacity": {"limit": 14, "used": 1},
        "execution_queue": [
            {"plan_item_id": "P1", "action": {"type": "place_order"}}
        ],
        "future_queue_acknowledgement": ["P2"],
    }


class V3PreregistrationTests(unittest.TestCase):
    def test_official_preregistration_passes(self) -> None:
        report = audit_preregistration()
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(report["paired_seeds"], [2301, 2302, 2303, 2304, 2305])
        self.assertEqual(len(report["observed_hashes"]), 9)

    def test_v2_seed_reuse_is_rejected(self) -> None:
        value = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
        value["design"]["paired_seeds"][0] = 2201
        value["design"]["arm_order"]["2201"] = value["design"]["arm_order"].pop("2301")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v3.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            report = audit_preregistration(path)
        self.assertIn("v3 seeds must not reuse immutable v2 seeds", report["errors"])


class V3TimingGateTests(unittest.TestCase):
    def test_future_correction_is_acknowledged_not_executed(self) -> None:
        report = validate_theatre_handoff(critic(), planner(), actor())
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(report["executed_plan_items"], ["P1"])
        self.assertEqual(report["conditional_plan_items"], ["P2"])

    def test_conditional_item_cannot_execute_early(self) -> None:
        value = actor()
        value["action_capacity"]["used"] = 2
        value["execution_queue"].append({"plan_item_id": "P2", "action": {"type": "restock"}})
        report = validate_theatre_handoff(critic(), planner(), value)
        self.assertTrue(any("cannot execute now" in item for item in report["errors"]))

    def test_immediate_correction_cannot_be_deferred(self) -> None:
        value = actor()
        value["action_capacity"]["used"] = 0
        value["execution_queue"] = []
        report = validate_theatre_handoff(critic(), planner(), value)
        self.assertTrue(any("every immediate correction item" in item for item in report["errors"]))

    def test_control_has_the_same_timing_gate(self) -> None:
        plan = planner()
        value = {
            "audit": critic(),
            "strategic_challenge": {"alternative_hypotheses": ["a", "b", "c"]},
            "plan": plan,
            **actor(),
        }
        report = validate_role_output("control", value)
        self.assertEqual(report["status"], "passed", report["errors"])


class V3RepairGateTests(unittest.TestCase):
    def test_one_same_identity_repair_can_pass(self) -> None:
        envelope = {
            "attempt": 1,
            "role": "actor",
            "turn_index": 9,
            "state_hash": "abc",
            "original_validation_errors": ["actor.action_capacity used mismatch"],
            "original_response_sha256": "a" * 64,
            "replacement": actor(),
        }
        report = validate_repair_envelope(envelope, role="actor", turn_index=9, state_hash="abc")
        self.assertEqual(report["status"], "passed", report["errors"])

    def test_second_or_cross_state_repair_fails(self) -> None:
        envelope = {
            "attempt": 2,
            "role": "actor",
            "turn_index": 9,
            "state_hash": "changed",
            "original_validation_errors": ["x"],
            "original_response_sha256": "a" * 64,
            "replacement": actor(),
        }
        report = validate_repair_envelope(envelope, role="actor", turn_index=9, state_hash="abc")
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("attempt must equal 1" in item for item in report["errors"]))
        self.assertTrue(any("preserve role, turn, and state" in item for item in report["errors"]))


if __name__ == "__main__":
    unittest.main()
