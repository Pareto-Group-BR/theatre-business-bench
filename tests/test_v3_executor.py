from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theatre_business_bench.cli import pair_batch
from theatre_business_bench.evidence import reconcile_openclaw_v3_gateway_restart
from theatre_business_bench.runner import _v3_repair_message, atomic_json, create_pair, read_json, step_pair
from theatre_business_bench.transport import ModelResult, ModelTransportError, OpenClawCodexTransport
from theatre_business_bench.v3 import V3ContractError, activate_v3_pair
from theatre_business_bench.verify import verify_pair


def critic() -> dict:
    return {
        "verdict": "on_track",
        "correction": {
            "required": False,
            "id": "none",
            "required_action_types": [],
            "verification": [],
        },
    }


def consciousness() -> dict:
    return {
        "alternative_hypotheses": ["h1", "h2", "h3"],
        "reversible_experiment": {},
        "rules": {},
    }


def planner() -> dict:
    return {
        "capital_budget": {},
        "action_queue": [{
            "id": "P1",
            "action_type": "collect_cash",
            "timing": "now",
            "precondition": "already_satisfied",
        }],
        "correction_binding": {
            "correction_id": "none",
            "immediate_queue_item_ids": [],
            "conditional_queue_item_ids": [],
        },
    }


def actor(*, valid: bool = True) -> dict:
    return {
        "plan_adherence": "followed",
        "action_capacity": {"limit": 14, "used": 0 if valid else 1},
        "execution_queue": [],
        "future_queue_acknowledgement": [],
    }


def control() -> dict:
    return {
        "audit": critic(),
        "strategic_challenge": {"alternative_hypotheses": ["h1", "h2", "h3"]},
        "plan": planner(),
        "action_capacity": {"limit": 14, "used": 0},
        "execution_queue": [],
        "future_queue_acknowledgement": [],
    }


def result(session_key: str, content: dict, ordinal: int) -> ModelResult:
    return ModelResult(
        content=content,
        text=json.dumps(content),
        run_id=f"gateway-{ordinal}",
        session_id=session_key,
        provider="openai",
        model="gpt-5.6-sol",
        duration_ms=1,
        usage={"input": 10, "cache_read": 2, "cache_write": 0, "output": 5, "total": 17},
    )


class V3ExecutorTests(unittest.TestCase):
    def _activated(self, root: Path, seed: int = 2301) -> Path:
        pair = create_pair(seed=seed, run_root=root, protocol="v3")
        with patch("theatre_business_bench.v3._published_source_identity"):
            activate_v3_pair(pair, "a" * 40)
        return pair

    def test_offline_pair_freezes_exact_v3_and_blocks_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir = create_pair(seed=2302, run_root=Path(directory), protocol="v3")
            pair = read_json(pair_dir / "pair.json")
            self.assertEqual(pair["first_arm"], "control")
            self.assertFalse(pair["inference_enabled"])
            self.assertEqual(verify_pair(pair_dir)["status"], "passed")
            with patch.object(OpenClawCodexTransport, "invoke") as invoke:
                stopped = step_pair(pair_dir)
            self.assertEqual(stopped["status"], "blocked_preregistration")
            invoke.assert_not_called()
            for arm in ("control", "theatre"):
                run = Path(pair[f"{arm}_run"])
                manifest = read_json(run / "manifest.json")
                self.assertEqual(manifest["protocol_version"], "v3")
                self.assertEqual(set(manifest["prompt_hashes"]), {
                    "control", "critic", "consciousness", "planner", "actor", "repair"
                })

    def test_one_paid_actor_repair_executes_and_replays(self) -> None:
        calls: list[tuple[str, bool, str]] = []

        def fake_invoke(_transport: OpenClawCodexTransport, session_key: str, message: str) -> ModelResult:
            role = session_key.rsplit("-", 1)[-1]
            is_repair = "CURRENT V3 REPAIR INPUT" in message
            calls.append((role, is_repair, message))
            by_role = {
                "critic": critic(),
                "consciousness": consciousness(),
                "planner": planner(),
                "actor": actor(valid=is_repair),
                "control": control(),
            }
            return result(session_key, by_role[role], len(calls))

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                outcomes = [step_pair(pair_dir) for _ in range(5)]
            self.assertTrue(all(item["pair_status"] == "running" for item in outcomes))
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["theatre"]["day"], 3)
            self.assertEqual(report["runs"]["control"]["day"], 3)
            self.assertEqual(report["runs"]["theatre"]["first_pass_contract_failures"], 1)
            self.assertEqual(report["runs"]["theatre"]["successful_repairs"], 1)
            self.assertEqual(report["runs"]["theatre"]["repair_calls"], 1)
            self.assertEqual(report["runs"]["theatre"]["repair_tokens"], 17)
            self.assertEqual(sum(is_repair for _, is_repair, _ in calls), 1)
            repair_message = next(message for _, is_repair, message in calls if is_repair)
            self.assertIn("original_response", repair_message)
            self.assertIn("deterministic_validation_errors", repair_message)
            self.assertNotIn("opponent", repair_message.lower())

    def test_local_budget_pause_between_attempts_resumes_only_the_repair(self) -> None:
        actor_primary_calls = 0
        repair_calls = 0

        def fake_invoke(_transport: OpenClawCodexTransport, session_key: str, message: str) -> ModelResult:
            nonlocal actor_primary_calls, repair_calls
            role = session_key.rsplit("-", 1)[-1]
            is_repair = "CURRENT V3 REPAIR INPUT" in message
            if role == "actor" and not is_repair:
                actor_primary_calls += 1
                return result(session_key, actor(valid=False), 10)
            if role == "actor" and is_repair:
                repair_calls += 1
                return result(session_key, actor(valid=True), 11)
            by_role = {
                "critic": critic(), "consciousness": consciousness(),
                "planner": planner(), "control": control(),
            }
            return result(session_key, by_role[role], actor_primary_calls + repair_calls + 1)

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                for _ in range(4):
                    self.assertEqual(step_pair(pair_dir)["pair_status"], "running")
                paused = step_pair(pair_dir, daily_token_budget=25_084)
                self.assertEqual(paused["pair_status"], "paused_quota")
                resumed = step_pair(pair_dir)
            self.assertEqual(resumed["pair_status"], "running")
            self.assertEqual(actor_primary_calls, 1)
            self.assertEqual(repair_calls, 1)
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["theatre"]["repair_calls"], 1)

    def test_provider_quota_failure_is_terminal_and_not_repairable(self) -> None:
        def fake_invoke(
            _transport: OpenClawCodexTransport, _session_key: str, _message: str
        ) -> ModelResult:
            raise ModelTransportError("429 quota exceeded")

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                outcome = step_pair(pair_dir)
            self.assertEqual(outcome["pair_status"], "failed_contract")
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])

    def test_second_structural_failure_is_terminal_and_immutable(self) -> None:
        calls = 0

        def fake_invoke(_transport: OpenClawCodexTransport, session_key: str, message: str) -> ModelResult:
            nonlocal calls
            calls += 1
            role = session_key.rsplit("-", 1)[-1]
            by_role = {
                "critic": critic(), "consciousness": consciousness(), "planner": planner(),
                "actor": actor(valid=False), "control": control(),
            }
            return result(session_key, by_role[role], calls)

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                outcomes = [step_pair(pair_dir) for _ in range(5)]
                repeated = step_pair(pair_dir)
            self.assertEqual(outcomes[-1]["pair_status"], "failed_contract")
            self.assertEqual(repeated["status"], "failed_contract")
            self.assertEqual(calls, 6)  # Theatre roles + repair, plus balanced control call
            pair = read_json(pair_dir / "pair.json")
            theatre = Path(pair["theatre_run"])
            self.assertEqual(read_json(theatre / "state.json")["day"], 0)
            self.assertFalse((theatre / "turns.jsonl").exists())
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["theatre"]["terminal_repair_failures"], 1)

    def test_activation_refuses_wrong_seed_and_tampered_preregistration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(V3ContractError):
                create_pair(seed=2201, run_root=root, protocol="v3")
            pair = create_pair(seed=2303, run_root=root, protocol="v3")
            (pair / "preregistration.json").write_text("{}\n", encoding="utf-8")
            with patch("theatre_business_bench.v3._published_source_identity"):
                with self.assertRaises(V3ContractError):
                    activate_v3_pair(pair, "a" * 40)

    def test_tampered_repair_accounting_fails_verification(self) -> None:
        def fake_invoke(_transport: OpenClawCodexTransport, session_key: str, message: str) -> ModelResult:
            role = session_key.rsplit("-", 1)[-1]
            is_repair = "CURRENT V3 REPAIR INPUT" in message
            by_role = {
                "critic": critic(), "consciousness": consciousness(), "planner": planner(),
                "actor": actor(valid=is_repair), "control": control(),
            }
            return result(session_key, by_role[role], 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = self._activated(root)
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                for _ in range(4):
                    step_pair(pair_dir)
            pair = read_json(pair_dir / "pair.json")
            theatre = Path(pair["theatre_run"])
            usage = [json.loads(line) for line in (theatre / "usage.jsonl").read_text().splitlines()]
            usage[-1]["usage"]["total"] += 1
            (theatre / "usage.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in usage), encoding="utf-8"
            )
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("usage" in item for item in report["errors"]))

    def test_model_drift_is_charged_and_terminal_with_verifiable_evidence(self) -> None:
        def fake_invoke(
            _transport: OpenClawCodexTransport, session_key: str, _message: str
        ) -> ModelResult:
            value = result(session_key, critic(), 1)
            return ModelResult(**{**value.__dict__, "provider": "other", "model": "wrong-model"})

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                outcome = step_pair(pair_dir)
            self.assertEqual(outcome["pair_status"], "failed_contract")
            pair = read_json(pair_dir / "pair.json")
            theatre = Path(pair["theatre_run"])
            usage = [json.loads(line) for line in (theatre / "usage.jsonl").read_text().splitlines()]
            failures = [
                json.loads(line) for line in (theatre / "model-failures.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(usage), 1)
            self.assertEqual(failures[0]["failure_kind"], "model_drift")
            self.assertEqual(usage[0]["usage"]["total"], 17)
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])

    def test_incomplete_call_journal_fails_loud_verification(self) -> None:
        def fake_invoke(
            _transport: OpenClawCodexTransport, session_key: str, _message: str
        ) -> ModelResult:
            return result(session_key, critic(), 1)

        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            with patch.object(OpenClawCodexTransport, "invoke", new=fake_invoke):
                step_pair(pair_dir)
            pair = read_json(pair_dir / "pair.json")
            theatre = Path(pair["theatre_run"])
            rows = (theatre / "call-journal.jsonl").read_text().splitlines()
            (theatre / "call-journal.jsonl").write_text(rows[0] + "\n", encoding="utf-8")
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("incomplete or reordered" in item for item in report["errors"]))

    def test_gateway_restart_reconciliation_charges_continuation_and_stops_terminal(self) -> None:
        repair_messages: list[str] = []

        def interrupted_invoke(
            _transport: OpenClawCodexTransport, session_key: str, message: str
        ) -> ModelResult:
            role = session_key.rsplit("-", 1)[-1]
            is_repair = "CURRENT V3 REPAIR INPUT" in message
            if role == "actor" and is_repair:
                repair_messages.append(message)
                raise KeyboardInterrupt("gateway restart")
            by_role = {
                "critic": critic(), "consciousness": consciousness(),
                "planner": planner(), "actor": actor(valid=False), "control": control(),
            }
            return result(session_key, by_role[role], 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = self._activated(root)
            with patch.object(OpenClawCodexTransport, "invoke", new=interrupted_invoke):
                with self.assertRaises(KeyboardInterrupt):
                    for _ in range(10):
                        step_pair(pair_dir)
            self.assertEqual(len(repair_messages), 1)
            pair = read_json(pair_dir / "pair.json")
            theatre = Path(pair["theatre_run"])
            flow = read_json(theatre / "flow.json")
            pending = flow["pending_invocation"]
            pending["original_message"] += "\n" + ("long-context-evidence " * 700)
            atomic_json(theatre / "flow.json", flow)
            repair_messages[0] = _v3_repair_message(
                theatre,
                pending["role"],
                pending["original"],
                pending["original_validation_errors"],
                pending["original_message"],
            )
            session_key = f"agent:business-bench:bench-{theatre.name.lower()}-actor"
            trace_id = "trace-restart-1"
            interrupted_id = "gateway-interrupted"
            completed_id = "gateway-auto-continuation"
            self.assertGreater(len(repair_messages[0]), 20_000)
            truncated_repair = repair_messages[0][:20_000] + "…"
            replacement = actor(valid=True)
            raw_text = json.dumps(replacement)
            common = {
                "traceSchema": "openclaw-trajectory", "schemaVersion": 1,
                "traceId": trace_id, "sessionId": trace_id,
                "sessionKey": session_key, "provider": "openai", "modelId": "gpt-5.6-sol",
            }
            trajectory_rows = [
                {**common, "type": "session.started", "runId": interrupted_id, "ts": "2099-09-01T00:00:00Z"},
                {**common, "type": "context.compiled", "runId": interrupted_id, "ts": "2099-09-01T00:00:01Z", "data": {"prompt": repair_messages[0]}},
                {**common, "type": "prompt.submitted", "runId": interrupted_id, "ts": "2099-09-01T00:00:02Z", "data": {"prompt": truncated_repair}},
                {**common, "type": "session.started", "runId": completed_id, "ts": "2099-09-01T00:01:00Z"},
                {**common, "type": "context.compiled", "runId": completed_id, "ts": "2099-09-01T00:01:01Z", "data": {"prompt": "restart continuation"}},
                {**common, "type": "prompt.submitted", "runId": completed_id, "ts": "2099-09-01T00:01:02Z", "data": {"prompt": "restart continuation"}},
                {**common, "type": "model.completed", "runId": completed_id, "ts": "2099-09-01T00:02:00Z", "data": {
                    "timedOut": False, "aborted": False, "promptError": None,
                    "assistantTexts": [raw_text],
                    "usage": {"input": 20, "cacheRead": 2, "cacheWrite": 0, "output": 5, "total": 27},
                }},
                {**common, "type": "session.ended", "runId": completed_id, "ts": "2099-09-01T00:02:01Z", "data": {"status": "completed"}},
            ]
            trajectory = root / "source.trajectory.jsonl"
            trajectory.write_text(
                "".join(json.dumps(row) + "\n" for row in trajectory_rows), encoding="utf-8"
            )
            session_rows = [
                {"type": "session", "id": trace_id},
                {"type": "message", "message": {"role": "user", "content": repair_messages[0]}},
                {"type": "message", "message": {"role": "user", "content": "[System] Your previous turn was interrupted by a gateway restart while OpenClaw was waiting."}},
                {"type": "message", "message": {"role": "assistant", "content": raw_text}},
            ]
            session_log = root / "source.session.jsonl"
            session_log.write_text(
                "".join(json.dumps(row) + "\n" for row in session_rows), encoding="utf-8"
            )

            first = reconcile_openclaw_v3_gateway_restart(
                pair_dir, "theatre", trajectory, session_log, interrupted_id, completed_id
            )
            second = reconcile_openclaw_v3_gateway_restart(
                pair_dir, "theatre", trajectory, session_log, interrupted_id, completed_id
            )
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "reconciled_failed_contract")
            self.assertEqual(first["provider_usage"]["total"], 27)
            self.assertEqual(read_json(pair_dir / "pair.json")["status"], "failed_contract")
            self.assertEqual(read_json(theatre / "flow.json")["status"], "failed_contract")
            self.assertFalse((theatre / "turns.jsonl").exists())
            failures = [json.loads(line) for line in (theatre / "model-failures.jsonl").read_text().splitlines()]
            self.assertEqual(failures[-1]["failure_kind"], "gateway_restart_recovery")
            self.assertEqual(failures[-1]["original_response_hash"], pending["original_response_hash"])
            report = verify_pair(pair_dir)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["runs"]["theatre"]["first_pass_contract_failures"], 1)
            self.assertEqual(report["runs"]["theatre"]["terminal_repair_failures"], 1)
            receipt_path = theatre / "gateway-restart-reconciliation.json"
            receipt = read_json(receipt_path)
            receipt["provider_usage"]["total"] += 1
            atomic_json(receipt_path, receipt)
            tampered = verify_pair(pair_dir)
            self.assertEqual(tampered["status"], "failed")
            self.assertIn(
                "theatre: gateway-restart reconciliation receipt is inconsistent",
                tampered["errors"],
            )

    def test_gateway_restart_reconciliation_refuses_session_without_exact_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_dir = self._activated(root)
            pair = read_json(pair_dir / "pair.json")
            theatre = Path(pair["theatre_run"])
            flow = read_json(theatre / "flow.json")
            flow["pending_invocation"] = {
                "role": "actor", "turn_index": 0, "state_hash": "a" * 64,
                "original": actor(valid=False), "original_response_hash": "b" * 64,
                "original_validation_errors": ["actor error"], "original_message": "original",
            }
            flow["phase"] = "actor"
            flow["current_step"] = "model_roles"
            flow["provider_attempt_serial"] = 1
            atomic_json(theatre / "flow.json", flow)
            (theatre / "call-journal.jsonl").write_text(json.dumps({
                "event": "started", "attempt_id": f"{theatre.name}:0:actor:repair:1",
                "attempt_kind": "repair", "turn_index": 0, "state_hash": "a" * 64,
                "original_response_hash": "b" * 64, "role": "actor",
            }) + "\n", encoding="utf-8")
            trajectory = root / "trace.jsonl"
            session_log = root / "session.jsonl"
            trajectory.write_text("", encoding="utf-8")
            session_log.write_text("", encoding="utf-8")
            with self.assertRaises(V3ContractError):
                reconcile_openclaw_v3_gateway_restart(
                    pair_dir, "theatre", trajectory, session_log, "interrupted", "completed"
                )
            self.assertEqual(len((theatre / "call-journal.jsonl").read_text().splitlines()), 1)

    def test_official_pair_batch_requires_canonical_lock_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            args = SimpleNamespace(
                pair=str(pair_dir), max_role_calls=1, daily_token_budget=None
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("theatre_business_bench.cli.step_pair") as step,
                patch("theatre_business_bench.cli.emit"),
            ):
                with self.assertRaises(SystemExit):
                    pair_batch(args)
            step.assert_not_called()

    def test_partial_activation_state_cannot_infer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pair_dir = self._activated(Path(directory))
            pair = read_json(pair_dir / "pair.json")
            pair["official"] = False
            pair["inference_enabled"] = False
            atomic_json(pair_dir / "pair.json", pair)
            with patch.object(OpenClawCodexTransport, "invoke") as invoke:
                stopped = step_pair(pair_dir)
            self.assertEqual(stopped["status"], "blocked_preregistration")
            invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
