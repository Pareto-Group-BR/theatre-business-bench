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

from theatre_business_bench.runner import TokenBudget, create_pair, create_run, read_json
from theatre_business_bench.transport import ModelTransportError, parse_json_object
from theatre_business_bench.cli import pair_batch


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


if __name__ == "__main__":
    unittest.main()
