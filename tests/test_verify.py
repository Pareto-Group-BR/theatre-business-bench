from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.runner import atomic_json, create_pair, read_json
from theatre_business_bench.simulator import VendingSimulator, stable_hash
from theatre_business_bench.verify import verify_pair


class PairVerificationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        pair_dir = create_pair(seed=91, days=28, run_root=root)
        pair = read_json(pair_dir / "pair.json")
        ledger = root / "usage-ledger.jsonl"
        ledger_rows = []
        for arm in ("control", "theatre"):
            run = Path(pair[f"{arm}_run"])
            manifest = read_json(run / "manifest.json")
            scenario = read_json(run / "scenario.json")
            simulator = VendingSimulator(scenario, seed=91)
            roles = ("control",) if arm == "control" else ("critic", "planner", "actor")
            decisions = []
            usages = []
            for role in roles:
                content = (
                    {"actions": [{"type": "research_supplier", "supplier": "metro"}]}
                    if role in ("control", "actor") else {"summary": role}
                )
                decisions.append({
                    "timestamp": "2026-08-29T00:00:00+00:00",
                    "turn_index": 0,
                    "role": role,
                    "content": content,
                    "response_hash": stable_hash(content),
                })
                usages.append({
                    "timestamp": "2026-08-29T00:00:00+00:00",
                    "run_id": manifest["run_id"],
                    "arm": arm,
                    "seed": 91,
                    "role": role,
                    "gateway_run_id": f"gateway-{arm}-{role}",
                    "session_id": f"session-{arm}-{role}",
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "duration_ms": 10,
                    "usage": {"input": 100, "cache_read": 20, "cache_write": 0, "output": 10, "total": 130},
                    "response_hash": stable_hash(content),
                })
            business_content = decisions[-1]["content"]
            applied = simulator.apply_turn(business_content["actions"])
            turn = {
                "timestamp": "2026-08-29T00:00:00+00:00",
                "turn_index": 0,
                "day_before": 0,
                "day_after": 3,
                "role": role,
                "accepted": applied.accepted,
                "rejected": applied.rejected,
                "state_hash": applied.state_hash,
            }
            (run / "model-decisions.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in decisions), encoding="utf-8"
            )
            (run / "usage.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in usages), encoding="utf-8"
            )
            (run / "turns.jsonl").write_text(json.dumps(turn) + "\n", encoding="utf-8")
            atomic_json(run / "state.json", simulator.state)
            flow = read_json(run / "flow.json")
            flow["turn_index"] = 1
            atomic_json(run / "flow.json", flow)
            ledger_rows.extend(usages)
        ledger.write_text("".join(json.dumps(row) + "\n" for row in ledger_rows), encoding="utf-8")
        return pair_dir, ledger

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


if __name__ == "__main__":
    unittest.main()
