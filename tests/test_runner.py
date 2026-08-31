from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.runner import (
    TokenBudget,
    _role_message,
    create_pair,
    create_run,
    read_json,
    step_pair,
)
from theatre_business_bench.transport import (
    ModelResult,
    ModelTransportError,
    OpenClawCodexTransport,
    parse_json_object,
)
from theatre_business_bench.cli import pair_batch
from theatre_business_bench.v2 import activate_v2_pair
from theatre_business_bench.verify import verify_pair


class RunnerTests(unittest.TestCase):
    def test_create_run_freezes_scenario_and_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = create_run("control", seed=77, days=35, run_root=Path(directory), agent_id="business-bench")
            manifest = read_json(run / "manifest.json")
            self.assertEqual(manifest["arm"], "control")
            self.assertEqual(manifest["seed"], 77)
            self.assertEqual(read_json(run / "scenario.json")["days"], 35)
            self.assertEqual(set(manifest["prompt_hashes"]), {"control", "critic", "planner", "actor"})
            self.assertTrue((run / "flow.json").exists())

    def test_token_budget_reads_provider_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "usage.jsonl"
            today = datetime.now(timezone.utc).date().isoformat()
            ledger.write_text(
                json.dumps({"timestamp": today + "T00:00:00+00:00", "usage": {"total": 1200}}) + "\n" +
                json.dumps({"timestamp": "2020-01-01T00:00:00+00:00", "usage": {"total": 9999}}) + "\n"
            )
            budget = TokenBudget(ledger, daily_limit=30_000, reserve_per_call=25_000)
            self.assertEqual(budget.used_today(), 1200)
            budget.assert_call_allowed()

    def test_token_budget_is_disabled_when_no_local_limit_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "usage.jsonl"
            today = datetime.now(timezone.utc).date().isoformat()
            ledger.write_text(
                json.dumps({"timestamp": today + "T00:00:00+00:00", "usage": {"total": 9_999_999}}) + "\n"
            )
            TokenBudget(ledger).assert_call_allowed()

    def test_parse_json_object_handles_fence(self) -> None:
        self.assertEqual(parse_json_object('```json\n{"ok": true}\n```'), {"ok": True})
        with self.assertRaises(ModelTransportError):
            parse_json_object("not json")

    def test_create_pair_freezes_identical_worlds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir = create_pair(seed=91, days=28, run_root=Path(directory))
            pair = read_json(pair_dir / "pair.json")
            control = Path(pair["control_run"])
            theatre = Path(pair["theatre_run"])
            self.assertEqual(read_json(control / "scenario.json"), read_json(theatre / "scenario.json"))
            self.assertEqual(read_json(control / "manifest.json")["seed"], 91)
            self.assertEqual(read_json(theatre / "manifest.json")["seed"], 91)
            self.assertEqual(pair["next_arm"], "control")

    def test_role_message_uses_frozen_run_prompt_not_mutable_repository_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = create_run("control", seed=92, days=3, run_root=Path(directory))
            frozen = run / "prompt-control.md"
            frozen.write_text("FROZEN PROMPT\n", encoding="utf-8")
            message = _role_message(
                run,
                read_json(run / "manifest.json"),
                "control",
                {"day": 1},
                {"pending": {}},
                {},
            )
            self.assertTrue(message.startswith("FROZEN PROMPT\n"))

    def test_public_view_exposes_real_action_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = create_run("control", seed=93, days=3, run_root=Path(directory))
            scenario = read_json(run / "scenario.json")
            from theatre_business_bench.simulator import VendingSimulator

            view = VendingSimulator(scenario, 93).public_view()
            self.assertEqual(view["max_actions_per_turn"], scenario["max_actions_per_turn"])

    def test_pair_batch_refuses_to_resume_failed_integrity(self) -> None:
        report = {"status": "failed", "errors": ["replay hash mismatch"]}
        with (
            patch("theatre_business_bench.cli.verify_pair", return_value=report),
            patch("theatre_business_bench.cli.step_pair") as step,
            redirect_stdout(StringIO()),
        ):
            with self.assertRaises(SystemExit):
                pair_batch(Namespace(pair="ignored", max_role_calls=1, daily_token_budget=500_000))
        step.assert_not_called()

    def test_v2_pair_is_frozen_offline_until_activated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = create_pair(seed=2202, run_root=root, protocol="v2")
            pair = read_json(pair_dir / "pair.json")
            self.assertEqual(pair["first_arm"], "theatre")
            self.assertFalse(pair["inference_enabled"])
            self.assertEqual(verify_pair(pair_dir)["status"], "passed")
            with patch.object(OpenClawCodexTransport, "invoke") as invoke:
                result = step_pair(pair_dir)
            self.assertEqual(result["status"], "blocked_preregistration")
            invoke.assert_not_called()

    def test_v2_activation_executes_one_paired_cycle_and_replays(self) -> None:
        critic = {
            "verdict": "on_track",
            "correction": {"required": False, "id": "none", "required_action_types": [], "verification": []},
        }
        consciousness = {
            "alternative_hypotheses": ["h1", "h2", "h3"],
            "reversible_experiment": {},
            "rules": {},
        }
        planner = {
            "capital_budget": {},
            "action_queue": [{"id": "P1", "action_type": "collect_cash"}],
            "correction_binding": {"correction_id": "none", "queue_item_ids": []},
        }
        actor = {
            "plan_adherence": "followed",
            "action_capacity": {"limit": 14, "used": 0},
            "execution_queue": [],
        }
        control = {
            "audit": {"verdict": "on_track"},
            "strategic_challenge": {"alternative_hypotheses": ["h1", "h2", "h3"]},
            "plan": {
                "action_queue": [{"id": "P1", "action_type": "collect_cash"}],
                "correction_binding": {"correction_id": "none", "queue_item_ids": []},
            },
            "action_capacity": {"limit": 14, "used": 0},
            "execution_queue": [],
        }
        by_role = {
            "critic": critic,
            "consciousness": consciousness,
            "planner": planner,
            "actor": actor,
            "control": control,
        }

        def fake_invoke(_transport: OpenClawCodexTransport, session_key: str, _message: str) -> ModelResult:
            role = session_key.rsplit("-", 1)[-1]
            content = by_role[role]
            return ModelResult(
                content=content,
                text=json.dumps(content),
                run_id=f"gateway-{role}",
                session_id=session_key,
                provider="openai",
                model="gpt-5.6-sol",
                duration_ms=1,
                usage={"input": 10, "cache_read": 0, "cache_write": 0, "output": 5, "total": 15},
            )

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = create_pair(seed=2201, run_root=Path(directory), protocol="v2")
            with patch("theatre_business_bench.v2._published_source_identity"):
                activate_v2_pair(pair_dir, "a" * 40)
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                results = [step_pair(pair_dir) for _ in range(5)]
            self.assertTrue(all(result["pair_status"] == "running" for result in results))
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["control"]["day"], 3)
            self.assertEqual(report["runs"]["theatre"]["day"], 3)

    def test_v2_frozen_preregistration_tamper_fails_pair_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir = create_pair(seed=2201, run_root=Path(directory), protocol="v2")
            (pair_dir / "preregistration.json").write_text("{}\n", encoding="utf-8")
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("preregistration hash mismatch" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
