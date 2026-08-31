from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.runner import atomic_json, create_pair, create_run, read_json, step_pair, step_run
from theatre_business_bench.simulator import VendingSimulator
from theatre_business_bench.transport import ModelResult
from theatre_business_bench.v2 import (
    V2ContractError,
    activate_v2_pair,
    audit_v2_bundle,
    verify_v2_preregistration,
)
from theatre_business_bench.verify import verify_pair


def valid_bundle() -> dict:
    return {
        "critical_review": {
            "verdict": "critical",
            "binding_constraint": "no suppliers are known",
            "facts": ["supplier offers are empty"],
            "opportunity_cost": "inventory cannot be purchased",
            "required_corrections": [{
                "id": "corr-source",
                "problem": "no supplier offer",
                "required_outcome": "discover Metro",
                "verification": "Metro appears in discovered suppliers",
            }],
        },
        "operating_plan": {
            "objective": "open a reliable supply path",
            "forecast": {"cash": "stable", "demand": "unserved", "inventory": "zero"},
            "execution_queue": [{
                "id": "q-research",
                "source_correction_ids": ["corr-source"],
                "priority": 1,
                "required": True,
                "action": {"type": "research_supplier", "supplier": "metro"},
                "verification": "offers become observable",
            }],
            "risks": ["three days of demand remain unserved"],
        },
        "strategic_review": {
            "challenged_assumptions": ["waiting preserves cash but destroys option value"],
            "reviewed_correction_ids": ["corr-source"],
            "required_queue_ids": ["q-research"],
            "experiment": "compare reliable supplier economics after discovery",
        },
        "execution": {
            "summary": "discover the reliable supplier",
            "executed_queue_ids": ["q-research"],
            "blocked_queue_ids": [],
            "additional_actions": [],
        },
    }


def result(content: dict, role: str) -> ModelResult:
    return ModelResult(
        content=content,
        text=json.dumps(content),
        run_id=f"run-{role}",
        session_id=f"session-{role}",
        provider="openai",
        model="gpt-5.6-sol",
        duration_ms=1,
        usage={"input": 10, "output": 10, "cache_read": 0, "cache_write": 0, "total": 20},
    )


class V2ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        scenario = json.loads((ROOT / "scenarios" / "vending_v2.json").read_text(encoding="utf-8"))
        self.simulator = VendingSimulator(scenario, seed=2101)
        self.view = self.simulator.public_view()

    def test_preregistration_hashes_and_five_new_seeds_pass(self) -> None:
        report = verify_v2_preregistration()
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual([row["seed"] for row in report["seeds"]], [2101, 2203, 2309, 2411, 2521])

    def test_v2_exposes_real_action_budget_without_changing_v1_view(self) -> None:
        self.assertEqual(self.view["action_budget"]["max_actions_per_turn"], 14)
        v1 = json.loads((ROOT / "scenarios" / "vending_v1.json").read_text(encoding="utf-8"))
        self.assertNotIn("action_budget", VendingSimulator(v1, seed=1201).public_view())

    def test_feedback_contract_compiles_actions_and_rejects_lost_correction(self) -> None:
        audit = audit_v2_bundle(valid_bundle(), self.view)
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(audit["actions"], [{"type": "research_supplier", "supplier": "metro"}])

        broken = valid_bundle()
        broken["operating_plan"]["execution_queue"][0]["source_correction_ids"] = []
        with self.assertRaisesRegex(V2ContractError, "missing from execution queue"):
            audit_v2_bundle(broken, self.view)

        fake_block = valid_bundle()
        fake_block["execution"]["executed_queue_ids"] = []
        fake_block["execution"]["blocked_queue_ids"] = [{
            "queue_id": "q-research",
            "state_path": "cash",
            "observed_value": 500.0,
            "reason": "pretend the supplier cannot be researched",
            "simulator_rejection": "pretend rejection",
        }]
        with self.assertRaisesRegex(V2ContractError, "is executable"):
            audit_v2_bundle(fake_block, self.view, self.simulator)

    def test_offline_pair_is_pre_registered_verified_and_cannot_call_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = create_pair(seed=2101, run_root=root, protocol="v2")
            pair = read_json(pair_dir / "pair.json")
            self.assertEqual(pair["next_arm"], "control")
            self.assertFalse(pair["inference_enabled"])
            report = verify_pair(pair_dir, root / "usage-ledger.jsonl")
            self.assertEqual(report["status"], "passed", report["errors"])
            with patch("theatre_business_bench.runner.OpenClawCodexTransport.invoke") as invoke:
                blocked = step_run(Path(pair["control_run"]))
            self.assertEqual(blocked["status"], "blocked_preregistration")
            invoke.assert_not_called()
            pair_blocked = step_pair(pair_dir)
            self.assertEqual(pair_blocked["status"], "blocked_preregistration")
            self.assertEqual(read_json(pair_dir / "pair.json")["next_arm"], "control")

    def test_activation_binds_clean_source_commit_and_preregistration_before_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = create_pair(seed=2203, run_root=root, protocol="v2")
            source_commit = "a" * 40
            with patch(
                "theatre_business_bench.v2.subprocess.run",
                side_effect=[SimpleNamespace(stdout=source_commit + "\n"), SimpleNamespace(stdout="")],
            ):
                receipt = activate_v2_pair(pair_dir, source_commit)
            self.assertEqual(receipt["source_commit"], source_commit)
            pair = read_json(pair_dir / "pair.json")
            self.assertTrue(pair["inference_enabled"])
            self.assertEqual(pair["next_arm"], "theatre")
            report = verify_pair(pair_dir, root / "usage-ledger.jsonl")
            self.assertEqual(report["status"], "passed", report["errors"])

    def test_activation_refuses_a_tampered_run_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = create_pair(seed=2101, run_root=root, protocol="v2")
            pair = read_json(pair_dir / "pair.json")
            prompt = Path(pair["control_run"]) / "prompt-control.md"
            prompt.write_text(prompt.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
            source_commit = "a" * 40
            with (
                patch(
                    "theatre_business_bench.v2.subprocess.run",
                    side_effect=[SimpleNamespace(stdout=source_commit + "\n"), SimpleNamespace(stdout="")],
                ),
                self.assertRaisesRegex(V2ContractError, "integrity failed before activation"),
            ):
                activate_v2_pair(pair_dir, source_commit)

    def test_same_shared_evidence_flows_through_four_roles_and_audited_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            theatre = create_run(
                "theatre", seed=2101, days=6, run_root=temp,
                protocol="v2",
            )
            manifest = read_json(theatre / "manifest.json")
            manifest["inference_enabled"] = True
            atomic_json(theatre / "manifest.json", manifest)
            bundle = valid_bundle()
            outputs = [
                result({"critical_review": bundle["critical_review"]}, "critic"),
                result({"operating_plan": bundle["operating_plan"]}, "planner"),
                result({"strategic_review": bundle["strategic_review"]}, "consciousness"),
                result({"execution": bundle["execution"]}, "actor"),
            ]
            with (
                patch("theatre_business_bench.runner.ROOT", temp),
                patch("theatre_business_bench.runner._v2_activation_allows", return_value=True),
                patch("theatre_business_bench.runner.OpenClawCodexTransport.invoke", side_effect=outputs) as invoke,
            ):
                calls = [step_run(theatre) for _ in range(4)]
            self.assertEqual([item["completed_role"] for item in calls], ["critic", "planner", "consciousness", "actor"])
            self.assertTrue(all("SHARED EVIDENCE" in call.args[1] for call in invoke.call_args_list))
            turn = json.loads((theatre / "turns.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(turn["decision_audit"]["status"], "passed")
            self.assertEqual(turn["decision_audit"]["correction_ids"], ["corr-source"])
            self.assertEqual(read_json(theatre / "state.json")["day"], 3)
            self.assertTrue((temp / "usage-ledger.jsonl").is_file())

    def test_activated_pair_completes_one_balanced_cycle_and_replays_from_custom_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = create_pair(seed=2101, run_root=root, protocol="v2")
            source_commit = "a" * 40
            with patch(
                "theatre_business_bench.v2.subprocess.run",
                side_effect=[SimpleNamespace(stdout=source_commit + "\n"), SimpleNamespace(stdout="")],
            ):
                activate_v2_pair(pair_dir, source_commit)
            bundle = valid_bundle()
            outputs = [
                result(bundle, "control"),
                result({"critical_review": bundle["critical_review"]}, "critic"),
                result({"operating_plan": bundle["operating_plan"]}, "planner"),
                result({"strategic_review": bundle["strategic_review"]}, "consciousness"),
                result({"execution": bundle["execution"]}, "actor"),
            ]
            with patch(
                "theatre_business_bench.runner.OpenClawCodexTransport.invoke",
                side_effect=outputs,
            ):
                progress = [step_pair(pair_dir) for _ in range(5)]
            self.assertEqual([item["arm"] for item in progress], ["control"] + ["theatre"] * 4)
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["control"]["day"], 3)
            self.assertEqual(report["runs"]["theatre"]["day"], 3)
            self.assertEqual(report["runs"]["control"]["model_calls"], 1)
            self.assertEqual(report["runs"]["theatre"]["model_calls"], 4)
            self.assertTrue((root / "usage-ledger.jsonl").is_file())

    def test_control_performs_the_same_four_functions_in_one_audited_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            control = create_run(
                "control", seed=2203, days=6, run_root=temp,
                protocol="v2",
            )
            manifest = read_json(control / "manifest.json")
            manifest["inference_enabled"] = True
            atomic_json(control / "manifest.json", manifest)
            with (
                patch("theatre_business_bench.runner.ROOT", temp),
                patch("theatre_business_bench.runner._v2_activation_allows", return_value=True),
                patch(
                    "theatre_business_bench.runner.OpenClawCodexTransport.invoke",
                    return_value=result(valid_bundle(), "control"),
                ) as invoke,
            ):
                completed = step_run(control)
            self.assertEqual(completed["completed_role"], "control")
            self.assertIn("critical_review", invoke.call_args.args[1])
            turn = json.loads((control / "turns.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(turn["decision_audit"]["required_queue_ids"], ["q-research"])


if __name__ == "__main__":
    unittest.main()
