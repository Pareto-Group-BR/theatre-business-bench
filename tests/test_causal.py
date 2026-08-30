from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pair_fixture import make_pair_fixture
from theatre_business_bench.causal import (
    CausalGateError,
    NON_SCORING_CLASSIFICATION,
    create_causal_fork,
    create_v2_preregistration,
    verify_causal_fork,
)
from theatre_business_bench.runner import PROMPT_FILES, finalize_run, read_json, step_run
from theatre_business_bench.simulator import stable_hash
from theatre_business_bench.verify import verify_pair


class CausalLaneTests(unittest.TestCase):
    def _fork(self, root: Path, *, days: int = 28) -> tuple[Path, Path, Path]:
        pair, ledger = make_pair_fixture(root / "runs", days=days)
        fork = create_causal_fork(
            pair,
            human_will="Use the full published action allowance; do not assume a three-action cap.",
            hypothesis="The false cap suppresses inventory throughput in the Theatre arm.",
            output_root=root / "forks",
            ledger_path=ledger,
        )
        return pair, ledger, fork

    def test_fork_is_replay_verified_isolated_and_non_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair, ledger, fork = self._fork(root)
            pair_before = (pair / "pair.json").read_bytes()
            report = verify_causal_fork(fork, ledger)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertFalse(report["scoring_eligible"])
            self.assertEqual((pair / "pair.json").read_bytes(), pair_before)
            active = Path(report["active_run"])
            manifest = read_json(active / "manifest.json")
            self.assertEqual(manifest["classification"], NON_SCORING_CLASSIFICATION)
            self.assertEqual(manifest["session_namespace"], read_json(fork / "fork.json")["fork_id"])
            intervention = read_json(active / "consciousness.json")
            self.assertEqual(stable_hash(intervention), manifest["consciousness_intervention_hash"])

    def test_tampered_intervention_fails_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, ledger, fork = self._fork(root)
            active = Path(read_json(fork / "fork.json")["active_run"])
            active = fork / active
            intervention = read_json(active / "consciousness.json")
            intervention["human_will"] = "silently changed"
            (active / "consciousness.json").write_text(json.dumps(intervention), encoding="utf-8")
            report = verify_causal_fork(fork, ledger)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("intervention binding" in error for error in report["errors"]))

    def test_generic_runner_refuses_non_scoring_fork(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, fork = self._fork(root)
            active = fork / read_json(fork / "fork.json")["active_run"]
            with self.assertRaisesRegex(RuntimeError, "verified causal runner"):
                step_run(active)

    def test_pair_gate_rejects_non_scoring_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair, ledger = make_pair_fixture(root / "runs")
            pair_manifest = read_json(pair / "pair.json")
            theatre_manifest_path = Path(pair_manifest["theatre_run"]) / "manifest.json"
            theatre_manifest = read_json(theatre_manifest_path)
            theatre_manifest["classification"] = NON_SCORING_CLASSIFICATION
            theatre_manifest["scoring_eligible"] = False
            theatre_manifest_path.write_text(json.dumps(theatre_manifest), encoding="utf-8")
            report = verify_pair(pair, ledger)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("non-scoring run" in error for error in report["errors"]))

    def test_preregistration_waits_for_completed_fork_and_new_design(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, ledger, fork = self._fork(root, days=3)
            scenario = root / "v2-scenario.json"
            scenario_value = json.loads((ROOT / "scenarios" / "vending_v1.json").read_text(encoding="utf-8"))
            scenario_value["version"] = 2
            scenario.write_text(json.dumps(scenario_value), encoding="utf-8")
            prompt_dir = root / "v2-prompts"
            prompt_dir.mkdir()
            for role, source in PROMPT_FILES.items():
                shutil.copy2(source, prompt_dir / f"{role}.md")
            protocol = root / "protocol-v2.md"
            protocol.write_text("# Protocol v2\nFrozen after causal exploration.\n", encoding="utf-8")
            output = root / "preregistration-v2.json"
            with self.assertRaisesRegex(CausalGateError, "completed exploratory fork"):
                create_v2_preregistration(
                    fork,
                    seeds=[1301, 1302, 1303, 1304, 1305],
                    scenario_path=scenario,
                    prompt_dir=prompt_dir,
                    protocol_path=protocol,
                    output_path=output,
                    runs_root=root / "runs",
                    ledger_path=ledger,
                )
            active = fork / read_json(fork / "fork.json")["active_run"]
            finalize_run(active)
            created = create_v2_preregistration(
                fork,
                seeds=[1301, 1302, 1303, 1304, 1305],
                scenario_path=scenario,
                prompt_dir=prompt_dir,
                protocol_path=protocol,
                output_path=output,
                runs_root=root / "runs",
                ledger_path=ledger,
            )
            registration = read_json(created)
            self.assertEqual(registration["status"], "preregistered_not_started")
            self.assertEqual([item["seed"] for item in registration["seeds"]], [1301, 1302, 1303, 1304, 1305])
            self.assertIn("seed 91", registration["exclusions"][0])


if __name__ == "__main__":
    unittest.main()
