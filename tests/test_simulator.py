from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.policies import heuristic_actions
from theatre_business_bench.simulator import SimulationError, VendingSimulator, stable_hash


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenario = json.loads((ROOT / "scenarios" / "vending_v1.json").read_text())

    def test_same_seed_and_actions_replay_exactly(self) -> None:
        left = VendingSimulator(self.scenario, seed=101)
        right = VendingSimulator(self.scenario, seed=101)
        for _ in range(18):
            actions = heuristic_actions(left.public_view(), arm="control")
            left.apply_turn(actions)
            right.apply_turn(copy.deepcopy(actions))
        self.assertEqual(stable_hash(left.state), stable_hash(right.state))
        self.assertEqual(left.state, right.state)

    def test_different_seeds_change_outcomes(self) -> None:
        left = VendingSimulator(self.scenario, seed=101)
        right = VendingSimulator(self.scenario, seed=102)
        for _ in range(12):
            left.apply_turn(heuristic_actions(left.public_view(), arm="control"))
            right.apply_turn(heuristic_actions(right.public_view(), arm="control"))
        self.assertNotEqual(left.state["metrics"]["revenue"], right.state["metrics"]["revenue"])

    def test_order_requires_research_and_cash(self) -> None:
        sim = VendingSimulator(self.scenario, seed=7)
        result = sim.apply_turn([{"type": "place_order", "supplier": "metro", "sku": "water", "units": 12}], advance_days=0)
        self.assertEqual(len(result.rejected), 1)
        sim.apply_turn([{"type": "research_supplier", "supplier": "metro"}], advance_days=0)
        result = sim.apply_turn([{"type": "place_order", "supplier": "metro", "sku": "water", "units": 12}], advance_days=0)
        self.assertEqual(len(result.accepted), 1)
        self.assertLess(sim.state["cash"], self.scenario["starting_cash"])

    def test_compute_cost_reduces_primary_score(self) -> None:
        sim = VendingSimulator(self.scenario, seed=5)
        free = sim.score(output_tokens=0)
        costly = sim.score(output_tokens=1_000_000)
        self.assertEqual(free["primary_score"] - costly["primary_score"], 100.0)

    def test_policy_survives_full_year(self) -> None:
        sim = VendingSimulator(self.scenario, seed=303)
        while not sim.state["terminated"]:
            sim.apply_turn(heuristic_actions(sim.public_view(), arm="theatre"))
        score = sim.score()
        self.assertEqual(score["days_survived"], 365)
        self.assertEqual(score["termination_reason"], "completed")
        self.assertGreater(score["revenue"], 1000)
        self.assertGreater(score["gross_profit"], 0)
        self.assertGreaterEqual(score["ending_inventory_book_value"], 0)


if __name__ == "__main__":
    unittest.main()
