from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pair_fixture import make_pair_fixture
from theatre_business_bench.report import (
    ReportGateError,
    build_executive_report,
    build_live_cockpit,
    write_live_cockpit,
    write_report_bundle,
)
from theatre_business_bench.simulator import stable_hash


def tree_digest(root: Path) -> str:
    evidence = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        evidence.append({"path": str(path.relative_to(root)), "bytes": path.read_bytes().hex()})
    return stable_hash(evidence)


class ExecutiveReportTests(unittest.TestCase):
    def test_incomplete_verified_pair_renders_honest_live_cockpit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir, ledger = make_pair_fixture(Path(directory))
            cockpit = build_live_cockpit(pair_dir, ledger)
            self.assertEqual(cockpit["claim"], "checkpoint parcial; não permite declarar vencedor")
            self.assertFalse(cockpit["pair"]["complete"])
            self.assertIsNone(cockpit["comparison"]["final_winner"])
            self.assertEqual(cockpit["integrity"]["status"], "passed")
            self.assertIn("gross_margin_pct", cockpit["arms"]["control"])
            self.assertIn("stockout_rate_pct", cockpit["arms"]["theatre"])
            self.assertIn("strategic_diagnostic", cockpit)
            self.assertIn("estimated_revenue_gap_volume_effect", cockpit["strategic_diagnostic"])
            self.assertIn("total_actions", cockpit["arms"]["control"])
            self.assertEqual(cockpit["schema_version"], 2)
            for arm in ("control", "theatre"):
                timeline = cockpit["arms"][arm]["timeline"]
                self.assertEqual([point["day"] for point in timeline], [1, 2, 3])
                self.assertEqual(timeline[-1]["liquid_cash"], cockpit["arms"][arm]["liquid_cash"])
                self.assertEqual(
                    sum(point["daily_provider_total_tokens"] for point in timeline),
                    cockpit["arms"][arm]["provider_total_tokens"],
                )
                self.assertEqual(timeline[1]["daily_provider_total_tokens"], 0)
                self.assertEqual(timeline[2]["daily_provider_total_tokens"], 0)

    def test_completed_verified_pair_renders_deterministically_without_mutating_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir, ledger = make_pair_fixture(root, days=3, complete=True)
            before = tree_digest(pair_dir)

            first = build_executive_report(pair_dir, ledger)
            second = build_executive_report(pair_dir, ledger)

            self.assertEqual(first, second)
            self.assertEqual(first["report_type"], "pilot_non_official")
            self.assertEqual(first["integrity"]["status"], "passed")
            self.assertEqual(first["pair"]["seed"], 91)
            self.assertEqual(before, tree_digest(pair_dir))
            digest = first["integrity"].pop("report_digest")
            self.assertEqual(digest, stable_hash(first))

    def test_bundle_writes_machine_markdown_and_standalone_html_outside_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir, ledger = make_pair_fixture(root, days=3, complete=True)
            report = build_executive_report(pair_dir, ledger)
            output = root / "published"
            write_report_bundle(
                pair_dir,
                report,
                json_out=output / "pilot.json",
                markdown_out=output / "pilot.md",
                html_out=output / "pilot.html",
            )
            self.assertEqual(json.loads((output / "pilot.json").read_text()), report)
            self.assertIn("Estado da alegação", (output / "pilot.md").read_text())
            self.assertIn("Piloto anual", (output / "pilot.html").read_text())

    def test_incomplete_pair_is_refused_even_when_checkpoint_integrity_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir, ledger = make_pair_fixture(Path(directory))
            with self.assertRaisesRegex(ReportGateError, "missing result.json"):
                build_executive_report(pair_dir, ledger)

    def test_live_cockpit_is_written_outside_immutable_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir, ledger = make_pair_fixture(root)
            cockpit = build_live_cockpit(pair_dir, ledger)
            output = root / "published" / "live-cockpit.json"

            write_live_cockpit(pair_dir, cockpit, output)

            self.assertEqual(json.loads(output.read_text()), cockpit)

    def test_live_cockpit_cannot_modify_immutable_pair_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir, ledger = make_pair_fixture(root)
            cockpit = build_live_cockpit(pair_dir, ledger)
            with self.assertRaisesRegex(ReportGateError, "outside the immutable pair"):
                write_live_cockpit(pair_dir, cockpit, pair_dir / "cockpit.json")

    def test_output_cannot_modify_immutable_pair_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir, ledger = make_pair_fixture(root, days=3, complete=True)
            report = build_executive_report(pair_dir, ledger)
            with self.assertRaisesRegex(ReportGateError, "outside the immutable pair"):
                write_report_bundle(pair_dir, report, json_out=pair_dir / "executive-report.json")

    def test_bundle_refuses_colliding_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir, ledger = make_pair_fixture(root, days=3, complete=True)
            report = build_executive_report(pair_dir, ledger)
            same_path = root / "report"
            with self.assertRaisesRegex(ReportGateError, "distinct paths"):
                write_report_bundle(
                    pair_dir,
                    report,
                    json_out=same_path,
                    markdown_out=same_path,
                )


if __name__ == "__main__":
    unittest.main()
