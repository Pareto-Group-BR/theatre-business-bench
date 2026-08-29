from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pair_fixture import make_pair_fixture
from theatre_business_bench.runner import atomic_json, read_json
from theatre_business_bench.verify import verify_pair


class PairVerificationTests(unittest.TestCase):
    def _fixture(self, root: Path, *, days: int = 28, complete: bool = False) -> tuple[Path, Path]:
        return make_pair_fixture(root, days=days, complete=complete)

    def test_live_shape_replays_and_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair, ledger = self._fixture(Path(directory))
            report = verify_pair(pair, ledger)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["control"]["turns"], 1)
            self.assertEqual(report["runs"]["theatre"]["model_calls"], 3)

    def test_tampered_state_fails_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir, ledger = self._fixture(Path(directory))
            pair = read_json(pair_dir / "pair.json")
            state_path = Path(pair["control_run"]) / "state.json"
            state = read_json(state_path)
            state["cash"] += 1
            atomic_json(state_path, state)
            report = verify_pair(pair_dir, ledger)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("persisted state differs" in error for error in report["errors"]))

    def test_missing_global_usage_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair, ledger = self._fixture(Path(directory))
            rows = ledger.read_text(encoding="utf-8").splitlines()
            ledger.write_text(rows[0] + "\n", encoding="utf-8")
            report = verify_pair(pair, ledger)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("global usage ledger" in error for error in report["errors"]))

    def test_provider_usage_total_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir, ledger = self._fixture(Path(directory))
            pair = read_json(pair_dir / "pair.json")
            usage_path = Path(pair["control_run"]) / "usage.jsonl"
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            usage["usage"]["total"] += 1
            usage_path.write_text(json.dumps(usage) + "\n", encoding="utf-8")
            ledger_rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            ledger_rows[0] = usage
            ledger.write_text("".join(json.dumps(row) + "\n" for row in ledger_rows), encoding="utf-8")
            report = verify_pair(pair_dir, ledger)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("provider usage total mismatch" in error for error in report["errors"]))

    def test_embedded_pair_result_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir, ledger = self._fixture(Path(directory), days=3, complete=True)
            result_path = pair_dir / "result.json"
            result = read_json(result_path)
            result["control"]["score"]["primary_score"] += 1
            atomic_json(result_path, result)
            report = verify_pair(pair_dir, ledger)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("embedded control result mismatch" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
