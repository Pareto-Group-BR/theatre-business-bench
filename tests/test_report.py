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
    write_report_bundle,
)
from theatre_business_bench.simulator import stable_hash


def tree_digest(root: Path) -> str:
    evidence = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        evidence.append({"path": str(path.relative_to(root)), "bytes": path.read_bytes().hex()})
    return stable_hash(evidence)


class ExecutiveReportTests(unittest.TestCase):
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
