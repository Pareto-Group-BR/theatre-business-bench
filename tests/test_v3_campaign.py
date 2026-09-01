from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.report import ReportGateError
from theatre_business_bench.simulator import stable_hash
from theatre_business_bench.v3_campaign import (
    build_v3_terminal_campaign,
    write_v3_campaign_bundle,
)


SEEDS = [2301, 2302, 2303, 2304, 2305]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def make_campaign(root: Path, completed: set[int] | None = None) -> Path:
    completed = completed or set()
    run_root = root / "v3-official"
    (run_root / "usage-ledger.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (run_root / "usage-ledger.jsonl").write_text("", encoding="utf-8")
    for seed in SEEDS:
        pair_id = f"pair-s{seed}"
        pair_dir = run_root / "pairs" / pair_id
        status = "completed" if seed in completed else "failed_contract"
        failing_arm = "control" if seed % 2 else "theatre"
        pair = {
            "pair_id": pair_id,
            "seed": seed,
            "official": True,
            "protocol_version": "v3",
            "status": status,
        }
        for arm in ("control", "theatre"):
            run_id = f"{arm}-s{seed}"
            run_dir = run_root / run_id
            pair[f"{arm}_run"] = str(run_dir)
            write_json(run_dir / "manifest.json", {
                "run_id": run_id,
                "seed": seed,
                "arm": arm,
                "official": True,
                "protocol_version": "v3",
            })
            flow = {"status": "completed" if status == "completed" else "ready"}
            if status == "failed_contract" and arm == failing_arm:
                flow = {
                    "status": "failed_contract",
                    "contract_failure": {"phase": arm, "message": f"terminal {arm} failure"},
                }
            write_json(run_dir / "flow.json", flow)
            if status == "completed":
                write_json(run_dir / "result.json", {
                    "score": {"primary_score": float(seed + (10 if arm == "theatre" else 0))}
                })
        if status == "completed":
            difference = float(seed - 2303)
            winner = "theatre" if difference > 0 else "control" if difference < 0 else "tie"
            write_json(pair_dir / "result.json", {
                "paired_difference_theatre_minus_control": difference,
                "winner": winner,
                "control": {"score": {"primary_score": float(seed)}},
                "theatre": {"score": {"primary_score": float(seed) + difference}},
            })
        write_json(pair_dir / "pair.json", pair)
    return run_root


def verification_for(pair_dir: Path, _ledger: Path) -> dict[str, object]:
    seed = int(pair_dir.name.rsplit("s", 1)[1])
    pair = json.loads((pair_dir / "pair.json").read_text())
    return {
        "status": "passed",
        "pair_id": pair_dir.name,
        "pair_status": pair["status"],
        "errors": [],
        "runs": {
            arm: {
                "day": 365 if pair["status"] == "completed" else seed - 2200,
                "turns": 10,
                "model_calls": 10 if arm == "control" else 20,
                "model_failures": 0,
                "provider_total_tokens": seed * (1 if arm == "control" else 2),
                "output_tokens": 100,
                "first_pass_contract_failures": 1,
                "successful_repairs": 1,
                "terminal_repair_failures": 0,
                "repair_calls": 1,
                "repair_tokens": 50,
                "replay_state_hash": f"{arm}-{seed}",
            }
            for arm in ("control", "theatre")
        },
    }


def tree_digest(root: Path) -> str:
    evidence = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        evidence.append({"path": str(path.relative_to(root)), "bytes": path.read_bytes().hex()})
    return stable_hash(evidence)


class V3TerminalCampaignTests(unittest.TestCase):
    def build(self, run_root: Path) -> dict[str, object]:
        audit = {"status": "passed", "paired_seeds": SEEDS, "errors": []}
        with (
            patch("theatre_business_bench.v3_campaign.audit_preregistration", return_value=audit),
            patch("theatre_business_bench.v3_campaign.verify_pair", side_effect=verification_for),
        ):
            return build_v3_terminal_campaign(run_root)

    def test_failed_campaign_is_deterministic_and_never_aggregates_partial_scores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = make_campaign(Path(directory))
            before = tree_digest(run_root)
            first = self.build(run_root)
            second = self.build(run_root)
            self.assertEqual(first, second)
            self.assertEqual(first["campaign"]["failed_contract_pairs"], 5)
            self.assertEqual(first["economic_outcome"]["estimand_status"], "not_observed")
            self.assertIsNone(first["economic_outcome"]["paired_differences"])
            self.assertIsNone(first["economic_outcome"]["winner"])
            self.assertEqual(before, tree_digest(run_root))
            digest = first["integrity"].pop("report_digest")
            self.assertEqual(digest, stable_hash(first))

    def test_one_completed_pair_remains_non_aggregable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.build(make_campaign(Path(directory), {2301}))
            self.assertEqual(report["campaign"]["completed_pairs"], 1)
            self.assertEqual(report["economic_outcome"]["estimand_status"], "not_identified")
            self.assertIsNone(report["economic_outcome"]["paired_differences"])
            self.assertEqual(report["pairs"][0]["economic_result"]["winner"], "control")

    def test_five_completed_pairs_enable_exact_paired_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.build(make_campaign(Path(directory), set(SEEDS)))
            outcome = report["economic_outcome"]
            self.assertEqual(outcome["paired_differences"], [-2.0, -1.0, 0.0, 1.0, 2.0])
            self.assertEqual(outcome["mean_paired_difference"], 0.0)
            self.assertEqual(outcome["median_paired_difference"], 0.0)
            self.assertEqual(outcome["bootstrap_interval_95"], [-1.2, 1.2])
            self.assertEqual(outcome["estimand_status"], "inconclusive")
            self.assertIsNone(outcome["winner"])

    def test_nonterminal_or_broken_replay_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = make_campaign(Path(directory))
            pair_path = run_root / "pairs" / "pair-s2303" / "pair.json"
            pair = json.loads(pair_path.read_text())
            pair["status"] = "running"
            write_json(pair_path, pair)
            with self.assertRaisesRegex(ReportGateError, "not terminal"):
                self.build(run_root)

    def test_bundle_refuses_output_inside_immutable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = make_campaign(root)
            report = self.build(run_root)
            published = root / "published"
            write_v3_campaign_bundle(
                run_root,
                report,
                json_out=published / "campaign.json",
                markdown_out=published / "campaign.md",
                html_out=published / "campaign.html",
            )
            self.assertEqual(json.loads((published / "campaign.json").read_text()), report)
            self.assertIn("campanha oficial terminal", (published / "campaign.md").read_text())
            self.assertIn("não identifica um vencedor", (published / "campaign.html").read_text())
            with self.assertRaisesRegex(ReportGateError, "outside the immutable run root"):
                write_v3_campaign_bundle(run_root, report, json_out=run_root / "false-result.json")


if __name__ == "__main__":
    unittest.main()
