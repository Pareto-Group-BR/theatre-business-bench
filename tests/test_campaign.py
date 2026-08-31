from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from theatre_business_bench.campaign import (
    build_v2_terminal_campaign,
    write_v2_campaign_bundle,
)
from theatre_business_bench.report import ReportGateError
from theatre_business_bench.simulator import stable_hash


SEEDS = [2201, 2202, 2203, 2204, 2205]
FAILURES = {
    2201: ("control", "control", "invalid model JSON preserved"),
    2202: ("theatre", "actor", "actor did not execute every bound correction item"),
    2203: ("theatre", "actor", "actor did not execute every bound correction item"),
    2204: ("theatre", "actor", "actor did not execute every bound correction item"),
    2205: ("control", "control", "control plan omits a required action type"),
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def make_campaign(root: Path) -> Path:
    run_root = root / "v2-official"
    (run_root / "usage-ledger.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (run_root / "usage-ledger.jsonl").write_text("", encoding="utf-8")
    for seed in SEEDS:
        pair_id = f"pair-s{seed}"
        pair_dir = run_root / "pairs" / pair_id
        failing_arm, phase, message = FAILURES[seed]
        pair = {
            "pair_id": pair_id,
            "seed": seed,
            "official": True,
            "protocol_version": "v2",
            "status": "failed_contract",
        }
        for arm in ("control", "theatre"):
            run_id = f"{arm}-s{seed}"
            run_dir = run_root / run_id
            pair[f"{arm}_run"] = str(run_dir)
            write_json(
                run_dir / "manifest.json",
                {
                    "run_id": run_id,
                    "seed": seed,
                    "arm": arm,
                    "official": True,
                    "protocol_version": "v2",
                },
            )
            flow = {"status": "ready"}
            if arm == failing_arm:
                flow = {
                    "status": "failed_contract",
                    "contract_failure": {"phase": phase, "message": message},
                }
            write_json(run_dir / "flow.json", flow)
        write_json(pair_dir / "pair.json", pair)
    return run_root


def verification_for(pair_dir: Path, _ledger: Path) -> dict[str, object]:
    seed = int(pair_dir.name.rsplit("s", 1)[1])
    return {
        "status": "passed",
        "pair_id": pair_dir.name,
        "pair_status": "failed_contract",
        "errors": [],
        "runs": {
            "control": {
                "day": seed - 2190,
                "turns": 2,
                "model_calls": 3,
                "model_failures": 1 if seed == 2201 else 0,
                "provider_total_tokens": 1000 + seed,
                "output_tokens": 100,
                "replay_state_hash": f"control-{seed}",
            },
            "theatre": {
                "day": seed - 2191,
                "turns": 2,
                "model_calls": 4,
                "model_failures": 0,
                "provider_total_tokens": 2000 + seed,
                "output_tokens": 200,
                "replay_state_hash": f"theatre-{seed}",
            },
        },
    }


def tree_digest(root: Path) -> str:
    evidence = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        evidence.append({"path": str(path.relative_to(root)), "bytes": path.read_bytes().hex()})
    return stable_hash(evidence)


class V2TerminalCampaignTests(unittest.TestCase):
    def build(self, run_root: Path) -> dict[str, object]:
        audit = {"status": "passed", "paired_seeds": SEEDS, "errors": []}
        with (
            patch("theatre_business_bench.campaign.audit_preregistration", return_value=audit),
            patch("theatre_business_bench.campaign.verify_pair", side_effect=verification_for),
        ):
            return build_v2_terminal_campaign(run_root)

    def test_terminal_campaign_is_deterministic_and_never_invents_economic_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = make_campaign(Path(directory))
            before = tree_digest(run_root)

            first = self.build(run_root)
            second = self.build(run_root)

            self.assertEqual(first, second)
            self.assertEqual(first["campaign"]["failed_contract_pairs"], 5)
            self.assertEqual(first["campaign"]["pairs_with_economic_result"], 0)
            self.assertEqual(first["economic_outcome"]["estimand_status"], "not_observed")
            self.assertIsNone(first["economic_outcome"]["winner"])
            self.assertIsNone(first["economic_outcome"]["mean_paired_difference"])
            self.assertEqual(first["reliability"]["failed_pairs_by_arm"], {"control": 2, "theatre": 3})
            self.assertEqual(before, tree_digest(run_root))
            digest = first["integrity"].pop("report_digest")
            self.assertEqual(digest, stable_hash(first))

    def test_bundle_writes_outside_evidence_and_refuses_inside_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = make_campaign(root)
            report = self.build(run_root)
            published = root / "published"
            write_v2_campaign_bundle(
                run_root,
                report,
                json_out=published / "campaign.json",
                markdown_out=published / "campaign.md",
                html_out=published / "campaign.html",
            )
            self.assertEqual(json.loads((published / "campaign.json").read_text()), report)
            self.assertIn("Resultado econômico:** não observado", (published / "campaign.md").read_text())
            self.assertIn("Não houve resultado econômico v2", (published / "campaign.html").read_text())
            with self.assertRaisesRegex(ReportGateError, "outside the immutable run root"):
                write_v2_campaign_bundle(run_root, report, json_out=run_root / "false-result.json")

    def test_result_json_or_nonterminal_seed_fails_loud(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = make_campaign(root)
            write_json(run_root / "pairs" / "pair-s2201" / "result.json", {"winner": "control"})
            with self.assertRaisesRegex(ReportGateError, "unexpectedly has result.json"):
                self.build(run_root)

            (run_root / "pairs" / "pair-s2201" / "result.json").unlink()
            pair_path = run_root / "pairs" / "pair-s2202" / "pair.json"
            pair = json.loads(pair_path.read_text())
            pair["status"] = "running"
            write_json(pair_path, pair)
            with self.assertRaisesRegex(ReportGateError, "not terminal failed_contract"):
                self.build(run_root)

    def test_seed_set_and_replay_integrity_are_all_or_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = make_campaign(Path(directory))
            pair_dir = run_root / "pairs" / "pair-s2205"
            for child in pair_dir.iterdir():
                child.unlink()
            pair_dir.rmdir()
            with self.assertRaisesRegex(ReportGateError, "seed set mismatch"):
                self.build(run_root)

            make_campaign(Path(directory))
            audit = {"status": "passed", "paired_seeds": SEEDS, "errors": []}

            def broken_verification(pair: Path, ledger: Path) -> dict[str, object]:
                result = verification_for(pair, ledger)
                if pair.name.endswith("2203"):
                    result["status"] = "failed"
                    result["errors"] = ["tampered replay"]
                return result

            with (
                patch("theatre_business_bench.campaign.audit_preregistration", return_value=audit),
                patch("theatre_business_bench.campaign.verify_pair", side_effect=broken_verification),
            ):
                with self.assertRaisesRegex(ReportGateError, "tampered replay"):
                    build_v2_terminal_campaign(run_root)


if __name__ == "__main__":
    unittest.main()
