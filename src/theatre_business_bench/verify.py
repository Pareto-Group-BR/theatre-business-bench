from __future__ import annotations

import json
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .runner import PROMPT_FILES, read_json
from .simulator import VendingSimulator, stable_hash
from .transport import ModelTransportError, parse_json_object
from .v2 import (
    PREREGISTRATION as V2_PREREGISTRATION,
    audit_preregistration as audit_v2_preregistration,
    extract_actions as extract_v2_actions,
    seed_plan as v2_seed_plan,
    validate_role_output as validate_v2_role_output,
    validate_theatre_handoff as validate_v2_theatre_handoff,
)
from .v3 import (
    PREREGISTRATION as V3_PREREGISTRATION,
    audit_preregistration as audit_v3_preregistration,
    seed_plan as v3_seed_plan,
    validate_repair_envelope,
    validate_role_output as validate_v3_role_output,
    validate_theatre_handoff as validate_v3_theatre_handoff,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _file_record(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sha256": None, "bytes": 0, "rows": 0}
    raw = path.read_bytes()
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "rows": len(raw.splitlines()),
    }


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows
    ).encode()


def _jsonl_sha256(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_jsonl_bytes(rows)).hexdigest()


def _jsonl_record(rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw = _jsonl_bytes(rows)
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "rows": len(rows),
    }


def _gateway_restart_attempt_kind(receipt: dict[str, Any]) -> str:
    return str(receipt.get("attempt_kind", "repair"))


def _gateway_restart_reason(receipt: dict[str, Any]) -> str | None:
    attempt_kind = _gateway_restart_attempt_kind(receipt)
    if attempt_kind == "repair":
        return (
            "gateway restarted during the frozen repair; auto-continuation "
            "preserved and charged but not applied"
        )
    if attempt_kind == "original":
        return (
            "gateway restarted during the frozen original invocation; auto-continuation "
            "preserved and charged but not applied"
        )
    return None


def _restart_protected_records(
    pair_dir: Path, run_dir: Path, other_run_dir: Path
) -> dict[str, dict[str, Any]]:
    paths = {
        "run/manifest.json": run_dir / "manifest.json",
        "run/scenario.json": run_dir / "scenario.json",
        "run/state.json": run_dir / "state.json",
        "run/role-memory.json": run_dir / "role-memory.json",
        "run/model-decisions.jsonl": run_dir / "model-decisions.jsonl",
        "run/role-invocations.jsonl": run_dir / "role-invocations.jsonl",
        "run/turns.jsonl": run_dir / "turns.jsonl",
        "run/result.json": run_dir / "result.json",
        "other/manifest.json": other_run_dir / "manifest.json",
        "other/scenario.json": other_run_dir / "scenario.json",
        "other/state.json": other_run_dir / "state.json",
        "other/flow.json": other_run_dir / "flow.json",
        "other/role-memory.json": other_run_dir / "role-memory.json",
        "other/usage.jsonl": other_run_dir / "usage.jsonl",
        "other/model-decisions.jsonl": other_run_dir / "model-decisions.jsonl",
        "other/model-failures.jsonl": other_run_dir / "model-failures.jsonl",
        "other/role-invocations.jsonl": other_run_dir / "role-invocations.jsonl",
        "other/call-journal.jsonl": other_run_dir / "call-journal.jsonl",
        "other/turns.jsonl": other_run_dir / "turns.jsonl",
        "other/result.json": other_run_dir / "result.json",
        "pair/activation.json": pair_dir / "activation.json",
        "pair/preregistration.json": pair_dir / "preregistration.json",
        "pair/result.json": pair_dir / "result.json",
    }
    return {name: _file_record(path) for name, path in paths.items()}


def _verify_gateway_restart_transaction(
    run_dir: Path, manifest: dict[str, Any], receipt: dict[str, Any]
) -> list[str]:
    arm = str(manifest.get("arm"))
    errors: list[str] = []
    transaction = receipt.get("transaction")
    final = receipt.get("final")
    if not isinstance(transaction, dict) or not isinstance(final, dict):
        return [f"{arm}: gateway-restart receipt lacks its completed transaction"]
    pair_dir = Path(manifest["pair_dir"]).resolve()
    pair = read_json(pair_dir / "pair.json")
    other_arm = "theatre" if arm == "control" else "control"
    other_run_dir = Path(pair[f"{other_arm}_run"]).resolve()
    ledger_path = Path(transaction.get("ledger_path", "")).resolve()
    expected_ledger_path = Path(manifest["usage_ledger_path"]).resolve()
    if ledger_path != expected_ledger_path:
        return [f"{arm}: gateway-restart transaction targets the wrong usage ledger"]
    paths = {
        "usage": run_dir / "usage.jsonl",
        "failures": run_dir / "model-failures.jsonl",
        "ledger": ledger_path,
        "journal": run_dir / "call-journal.jsonl",
    }
    source = transaction.get("source", {})
    target = transaction.get("target", {})
    rows = transaction.get("rows", {})
    if set(rows) != set(paths) or set(source.get("files", {})) != set(paths):
        return [f"{arm}: gateway-restart transaction file set mismatch"]
    if set(target.get("files", {})) != set(paths):
        return [f"{arm}: gateway-restart transaction target set mismatch"]
    expected_transaction_id = stable_hash({
        "pair_id": receipt.get("pair_id"),
        "run_id": receipt.get("run_id"),
        "attempt_id": receipt.get("attempt_id"),
        "interrupted_gateway_run_id": receipt.get("interrupted_gateway_run_id"),
        "completed_gateway_run_id": receipt.get("completed_gateway_run_id"),
        "trajectory_sha256": receipt.get("source", {}).get("trajectory_sha256"),
        "session_log_sha256": receipt.get("source", {}).get("session_log_sha256"),
    })
    if transaction.get("transaction_id") != expected_transaction_id:
        errors.append(f"{arm}: gateway-restart transaction id mismatch")
    usage_row = rows.get("usage", [{}])[-1] if rows.get("usage") else {}
    failure_row = rows.get("failures", [{}])[-1] if rows.get("failures") else {}
    ledger_row = rows.get("ledger", [{}])[-1] if rows.get("ledger") else {}
    journal_row = rows.get("journal", [{}])[-1] if rows.get("journal") else {}
    identity = {
        "attempt_id": receipt.get("attempt_id"),
        "role": receipt.get("role"),
        "turn_index": receipt.get("turn_index"),
        "attempt_kind": _gateway_restart_attempt_kind(receipt),
    }
    if "state_hash" in receipt:
        identity["state_hash"] = receipt.get("state_hash")
    if (
        any(usage_row.get(key) != value for key, value in identity.items())
        or any(failure_row.get(key) != value for key, value in identity.items())
        or any(journal_row.get(key) != value for key, value in identity.items())
        or usage_row.get("usage") != receipt.get("provider_usage")
        or usage_row.get("response_hash") != receipt.get("response_hash")
        or failure_row.get("response_hash") != receipt.get("response_hash")
        or failure_row.get("failure_kind") != "gateway_restart_recovery"
        or ledger_row != usage_row
        or journal_row.get("event") != "completed"
        or journal_row.get("outcome") != "gateway_restart_recovery_terminal"
    ):
        errors.append(f"{arm}: gateway-restart transaction rows exceed the receipt")
    observed_files: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        target_rows = rows.get(name)
        source_record = source["files"][name]
        target_record = _jsonl_record(target_rows) if isinstance(target_rows, list) else {}
        if name == "ledger" and isinstance(target_rows, list):
            current_raw = path.read_bytes() if path.exists() else b""
            current_record = target_record if current_raw.startswith(_jsonl_bytes(target_rows)) else _file_record(path)
        else:
            current_record = _file_record(path)
        observed_files[name] = current_record
        if (
            not isinstance(target_rows, list)
            or _jsonl_sha256(target_rows) != target["files"][name]
            or current_record.get("sha256") != target["files"][name]
            or len(target_rows) != int(source_record.get("rows", -1)) + 1
        ):
            errors.append(f"{arm}: gateway-restart {name} transaction hash mismatch")
            continue
        if source_record.get("sha256") is None:
            if target_rows[:-1]:
                errors.append(f"{arm}: gateway-restart {name} source absence mismatch")
        elif _jsonl_sha256(target_rows[:-1]) != source_record.get("sha256"):
            errors.append(f"{arm}: gateway-restart {name} source rows were not preserved")

    flow = read_json(run_dir / "flow.json")
    pair_path = pair_dir / "pair.json"
    if (
        _file_record(run_dir / "flow.json").get("sha256") != target.get("flow")
        or _file_record(pair_path).get("sha256") != target.get("pair")
    ):
        errors.append(f"{arm}: gateway-restart terminal state hash mismatch")
    source_flow = dict(flow)
    for key in ("status", "updated_at", "contract_failure"):
        source_flow.pop(key, None)
    source_flow.update(source.get("flow_transition_fields", {}))
    source_pair = dict(pair)
    for key in ("status", "last_arm", "last_result", "updated_at"):
        source_pair.pop(key, None)
    source_pair.update(source.get("pair_transition_fields", {}))
    def json_sha(value: Any) -> str:
        return hashlib.sha256(
            (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
        ).hexdigest()

    if json_sha(source_flow) != source.get("flow", {}).get("sha256"):
        errors.append(f"{arm}: gateway-restart source flow cannot be reconstructed")
    if json_sha(source_pair) != source.get("pair", {}).get("sha256"):
        errors.append(f"{arm}: gateway-restart source pair cannot be reconstructed")
    reason = _gateway_restart_reason(receipt)
    if reason is None:
        errors.append(f"{arm}: gateway-restart receipt attempt kind is invalid")
    expected_flow_transition = {
        "status": "failed_contract",
        "updated_at": receipt.get("reconciled_at"),
        "contract_failure": {"phase": receipt.get("role"), "message": reason},
    }
    expected_pair_transition = {
        "status": "failed_contract",
        "last_arm": arm,
        "last_result": {
            "status": "failed_contract",
            "reason": reason,
            "run_dir": str(run_dir),
            "provider_usage_charged": receipt.get("provider_usage"),
            "simulator_turns_added": 0,
        },
        "updated_at": receipt.get("reconciled_at"),
    }
    if (
        transaction.get("flow_transition") != expected_flow_transition
        or transaction.get("pair_transition") != expected_pair_transition
    ):
        errors.append(f"{arm}: gateway-restart transaction state transition mismatch")
    expected_flow = dict(source_flow)
    expected_flow.update(expected_flow_transition)
    expected_pair = dict(source_pair)
    expected_pair.update(expected_pair_transition)
    if json_sha(expected_flow) != target.get("flow") or json_sha(expected_pair) != target.get("pair"):
        errors.append(f"{arm}: gateway-restart transaction target state mismatch")
    protected = _restart_protected_records(pair_dir, run_dir, other_run_dir)
    if protected != source.get("protected_artifacts"):
        errors.append(f"{arm}: gateway-restart reconciliation changed protected evidence")
    observed_final = {
        "files": observed_files,
        "flow": _file_record(run_dir / "flow.json"),
        "pair": _file_record(pair_path),
        "protected_artifacts": protected,
    }
    if observed_final != final:
        errors.append(f"{arm}: gateway-restart final transaction receipt mismatch")
    return errors


_UNDISPATCHED_REASON = (
    "gateway restart left a write-ahead attempt with no OpenClaw dispatch observed; "
    "terminalized without retry"
)


def _undispatched_protected_records(
    pair_dir: Path, run_dir: Path, other_run_dir: Path
) -> dict[str, dict[str, Any]]:
    protected = _restart_protected_records(pair_dir, run_dir, other_run_dir)
    protected.update({
        "run/usage.jsonl": _file_record(run_dir / "usage.jsonl"),
        "run/model-failures.jsonl": _file_record(run_dir / "model-failures.jsonl"),
    })
    return protected


def _undispatched_ledger_prefix_matches(
    path: Path, prefix: dict[str, Any], run_id: str
) -> bool:
    if not path.is_file():
        return prefix.get("sha256") is None and prefix.get("bytes") == 0
    raw = path.read_bytes()
    try:
        size = int(prefix.get("bytes", -1))
    except (TypeError, ValueError):
        return False
    if size < 0 or len(raw) < size:
        return False
    original = raw[:size]
    if prefix.get("sha256") is None:
        if original:
            return False
    elif hashlib.sha256(original).hexdigest() != prefix.get("sha256"):
        return False
    suffix = raw[size:]
    if suffix and original and not original.endswith(b"\n"):
        return False
    for line in suffix.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(row, dict) or row.get("run_id") == run_id:
            return False
    return True


def _verify_undispatched_transaction(
    run_dir: Path, manifest: dict[str, Any], receipt: dict[str, Any]
) -> list[str]:
    arm = str(manifest.get("arm"))
    errors: list[str] = []
    transaction = receipt.get("transaction")
    final = receipt.get("final")
    if not isinstance(transaction, dict) or not isinstance(final, dict):
        return [f"{arm}: undispatched-attempt receipt lacks its completed transaction"]
    pair_dir = Path(manifest["pair_dir"]).resolve()
    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    other_arm = "theatre" if arm == "control" else "control"
    other_run_dir = Path(pair[f"{other_arm}_run"]).resolve()
    source = transaction.get("source", {})
    target = transaction.get("target", {})
    rows = transaction.get("rows", {})
    if set(rows) != {"journal"} or set(source.get("files", {})) != {"journal"}:
        return [f"{arm}: undispatched-attempt transaction file set mismatch"]
    if set(target.get("files", {})) != {"journal"}:
        return [f"{arm}: undispatched-attempt transaction target set mismatch"]
    expected_transaction_id = stable_hash({
        "pair_id": receipt.get("pair_id"),
        "run_id": receipt.get("run_id"),
        "attempt_id": receipt.get("attempt_id"),
        "trajectory_sha256": receipt.get("source", {}).get("trajectory_sha256"),
        "session_log_sha256": receipt.get("source", {}).get("session_log_sha256"),
    })
    if transaction.get("transaction_id") != expected_transaction_id:
        errors.append(f"{arm}: undispatched-attempt transaction id mismatch")

    journal_rows = rows.get("journal")
    source_record = source["files"]["journal"]
    terminal = journal_rows[-1] if isinstance(journal_rows, list) and journal_rows else {}
    expected_terminal = {
        "attempt_id": receipt.get("attempt_id"),
        "attempt_kind": "original",
        "turn_index": receipt.get("turn_index"),
        "state_hash": receipt.get("state_hash"),
        "role": receipt.get("role"),
        "event": "transport_failed",
        "timestamp": receipt.get("reconciled_at"),
        "error": _UNDISPATCHED_REASON,
        "evidence_source": "openclaw_undispatched_attempt_reconciliation",
        "trajectory_sha256": receipt.get("source", {}).get("trajectory_sha256"),
        "session_log_sha256": receipt.get("source", {}).get("session_log_sha256"),
    }
    if terminal != expected_terminal:
        errors.append(f"{arm}: undispatched-attempt terminal row exceeds the receipt")
    current_journal = _file_record(run_dir / "call-journal.jsonl")
    if (
        not isinstance(journal_rows, list)
        or _jsonl_sha256(journal_rows) != target["files"]["journal"]
        or current_journal.get("sha256") != target["files"]["journal"]
        or len(journal_rows) != int(source_record.get("rows", -1)) + 1
    ):
        errors.append(f"{arm}: undispatched-attempt journal transaction hash mismatch")
    elif source_record.get("sha256") is None:
        if journal_rows[:-1]:
            errors.append(f"{arm}: undispatched-attempt journal source absence mismatch")
    elif _jsonl_sha256(journal_rows[:-1]) != source_record.get("sha256"):
        errors.append(f"{arm}: undispatched-attempt prior journal rows changed")

    ledger_path = Path(transaction.get("ledger_path", "")).resolve()
    expected_ledger = Path(manifest["usage_ledger_path"]).resolve()
    ledger_prefix = source.get("ledger_prefix")
    if ledger_path != expected_ledger:
        errors.append(f"{arm}: undispatched-attempt transaction targets the wrong usage ledger")
    elif not isinstance(ledger_prefix, dict) or not _undispatched_ledger_prefix_matches(
        ledger_path, ledger_prefix, str(receipt.get("run_id"))
    ):
        errors.append(f"{arm}: undispatched-attempt no-usage ledger boundary mismatch")

    flow = read_json(run_dir / "flow.json")
    source_flow = dict(flow)
    for key in ("status", "updated_at", "contract_failure"):
        source_flow.pop(key, None)
    source_flow.update(source.get("flow_transition_fields", {}))
    source_pair = dict(pair)
    for key in ("next_arm", "status", "last_arm", "last_result", "updated_at"):
        source_pair.pop(key, None)
    source_pair.update(source.get("pair_transition_fields", {}))

    def json_sha(value: Any) -> str:
        return hashlib.sha256(
            (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
        ).hexdigest()

    if json_sha(source_flow) != source.get("flow", {}).get("sha256"):
        errors.append(f"{arm}: undispatched-attempt source flow cannot be reconstructed")
    if json_sha(source_pair) != source.get("pair", {}).get("sha256"):
        errors.append(f"{arm}: undispatched-attempt source pair cannot be reconstructed")
    expected_flow_transition = {
        "status": "failed_contract",
        "updated_at": receipt.get("reconciled_at"),
        "contract_failure": {
            "phase": receipt.get("role"),
            "message": _UNDISPATCHED_REASON,
        },
    }
    expected_pair_transition = {
        "next_arm": other_arm,
        "status": "failed_contract",
        "last_arm": arm,
        "last_result": {
            "status": "failed_contract",
            "reason": _UNDISPATCHED_REASON,
            "run_dir": str(run_dir),
            "provider_calls_added": 0,
            "provider_usage_rows_added": 0,
            "simulator_turns_added": 0,
        },
        "updated_at": receipt.get("reconciled_at"),
    }
    if (
        transaction.get("flow_transition") != expected_flow_transition
        or transaction.get("pair_transition") != expected_pair_transition
    ):
        errors.append(f"{arm}: undispatched-attempt state transition mismatch")
    expected_flow = dict(source_flow)
    expected_flow.update(expected_flow_transition)
    expected_pair = dict(source_pair)
    expected_pair.update(expected_pair_transition)
    if (
        json_sha(expected_flow) != target.get("flow")
        or json_sha(expected_pair) != target.get("pair")
        or _file_record(run_dir / "flow.json").get("sha256") != target.get("flow")
        or _file_record(pair_path).get("sha256") != target.get("pair")
    ):
        errors.append(f"{arm}: undispatched-attempt terminal state hash mismatch")

    protected = _undispatched_protected_records(pair_dir, run_dir, other_run_dir)
    if protected != source.get("protected_artifacts"):
        errors.append(f"{arm}: undispatched-attempt reconciliation changed protected evidence")
    observed_final = {
        "files": {"journal": current_journal},
        "flow": _file_record(run_dir / "flow.json"),
        "pair": _file_record(pair_path),
        "ledger_prefix": ledger_prefix,
        "protected_artifacts": protected,
    }
    if observed_final != final:
        errors.append(f"{arm}: undispatched-attempt final transaction receipt mismatch")
    return errors


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{line_number}: invalid JSON: {exc.msg}")
                continue
            if not isinstance(value, dict):
                errors.append(f"{path.name}:{line_number}: row must be an object")
                continue
            rows.append(value)
    return rows


def _verify_run(
    run_dir: Path,
    expected_arm: str,
    ledger_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    required = ("manifest.json", "scenario.json", "state.json", "flow.json", "role-memory.json")
    for name in required:
        if not (run_dir / name).is_file():
            errors.append(f"{expected_arm}: missing {name}")
    if errors:
        return {}, errors

    manifest = read_json(run_dir / "manifest.json")
    scenario = read_json(run_dir / "scenario.json")
    state = read_json(run_dir / "state.json")
    flow = read_json(run_dir / "flow.json")
    run_id = manifest.get("run_id")
    protocol = manifest.get("protocol_version", "v1")

    if run_dir.name != run_id:
        errors.append(f"{expected_arm}: run directory does not match manifest run_id")
    if manifest.get("arm") != expected_arm:
        errors.append(f"{expected_arm}: manifest arm mismatch")
    if manifest.get("seed") != state.get("seed"):
        errors.append(f"{expected_arm}: manifest/state seed mismatch")
    if stable_hash(scenario) != manifest.get("scenario_hash"):
        errors.append(f"{expected_arm}: scenario hash mismatch")

    prompt_hashes = manifest.get("prompt_hashes", {})
    expected_prompt_roles = set(prompt_hashes) if protocol in ("v2", "v3") else set(PROMPT_FILES)
    for role in expected_prompt_roles:
        snapshot = run_dir / f"prompt-{role}.md"
        if not snapshot.is_file():
            errors.append(f"{expected_arm}: missing frozen prompt for {role}")
        elif stable_hash(snapshot.read_text(encoding="utf-8")) != prompt_hashes.get(role):
            errors.append(f"{expected_arm}: frozen prompt hash mismatch for {role}")
    if protocol in ("v2", "v3"):
        expected_roles = {"control", "critic", "consciousness", "planner", "actor"}
        if protocol == "v3":
            expected_roles.add("repair")
        if expected_prompt_roles != expected_roles:
            errors.append(f"{expected_arm}: {protocol} prompt role set mismatch")
        for name, field in (("shared-corpus.md", "shared_corpus_hash"), ("protocol.md", "protocol_hash")):
            snapshot = run_dir / name
            if not snapshot.is_file():
                errors.append(f"{expected_arm}: missing frozen {name}")
            elif stable_hash(snapshot.read_text(encoding="utf-8")) != manifest.get(field):
                errors.append(f"{expected_arm}: frozen {name} hash mismatch")
        artifacts = manifest.get("artifact_hashes", {})
        artifact_files = {
            **{f"prompt_{role}": run_dir / f"prompt-{role}.md" for role in expected_prompt_roles},
            "shared_corpus": run_dir / "shared-corpus.md",
            "protocol": run_dir / "protocol.md",
        }
        for key, snapshot in artifact_files.items():
            if snapshot.is_file() and hashlib.sha256(snapshot.read_bytes()).hexdigest() != artifacts.get(key):
                errors.append(f"{expected_arm}: frozen artifact SHA-256 mismatch for {key}")

    usage = _read_jsonl(run_dir / "usage.jsonl", errors)
    decisions = _read_jsonl(run_dir / "model-decisions.jsonl", errors)
    failures = _read_jsonl(run_dir / "model-failures.jsonl", errors)
    invocations = _read_jsonl(run_dir / "role-invocations.jsonl", errors)
    call_journal = _read_jsonl(run_dir / "call-journal.jsonl", errors)
    turns = _read_jsonl(run_dir / "turns.jsonl", errors)
    if len(usage) != len(decisions) + len(failures):
        errors.append(
            f"{expected_arm}: usage/evidence count mismatch "
            f"({len(usage)} != {len(decisions)} decisions + {len(failures)} failures)"
        )

    def is_terminal_contract_decision(
        index: int,
        decision: dict[str, Any],
        role: str,
        validation_errors: list[str],
    ) -> bool:
        """Recognize the one charged parsed decision that stopped the v2 run.

        The runner records parseable model output before enforcing the role
        schema. A structurally invalid response is therefore durable decision
        evidence, while the flow is terminal failed_contract and no simulator
        turn is applied. Accept only that exact, final, flow-bound shape.
        """
        contract_failure = flow.get("contract_failure")
        expected_message = f"{role}: " + "; ".join(validation_errors)
        return (
            flow.get("status") == "failed_contract"
            and isinstance(contract_failure, dict)
            and flow.get("phase") == role
            and contract_failure.get("phase") == role
            and contract_failure.get("message") == expected_message
            and decision.get("turn_index") == len(turns)
            and index == len(decisions) - 1
            and not failures
        )

    accepted_v3: dict[tuple[int, str], str] = {}
    referenced_v3_hashes: Counter[tuple[int, str, str, str]] = Counter()
    if protocol == "v3":
        for index, invocation in enumerate(invocations):
            role = invocation.get("role")
            turn_index = invocation.get("turn_index")
            state_hash = invocation.get("state_hash")
            outcome = invocation.get("outcome")
            original_hash = invocation.get("original_response_hash")
            repair_hash = invocation.get("repair_response_hash")
            original_errors = invocation.get("original_validation_errors")
            repair_errors = invocation.get("repair_validation_errors")
            accepted_hash = invocation.get("accepted_response_hash")
            if role not in ("control", "critic", "consciousness", "planner", "actor"):
                errors.append(f"{expected_arm}: invocation {index} has unknown role")
                continue
            if not isinstance(turn_index, int) or turn_index < 0:
                errors.append(f"{expected_arm}: invocation {index} has invalid turn")
            if not isinstance(state_hash, str) or len(state_hash) != 64:
                errors.append(f"{expected_arm}: invocation {index} has invalid state hash")
            if not isinstance(original_errors, list) or not isinstance(repair_errors, list):
                errors.append(f"{expected_arm}: invocation {index} has invalid validation errors")
                continue
            original_decisions = [
                item for item in decisions
                if item.get("turn_index") == turn_index
                and item.get("role") == role
                and item.get("response_hash") == original_hash
                and item.get("attempt_kind") == "original"
            ]
            if len(original_decisions) != 1:
                errors.append(f"{expected_arm}: invocation {index} does not bind one original decision")
                continue
            referenced_v3_hashes[(turn_index, role, original_hash, "original")] += 1
            original_report = validate_v3_role_output(role, original_decisions[0].get("content"))
            if (
                role == "actor"
                and any(
                    item not in original_errors and f"actor: {item}" not in original_errors
                    for item in original_report["errors"]
                )
                or role != "actor"
                and original_report["errors"] != original_errors
            ):
                errors.append(f"{expected_arm}: invocation {index} original validation errors mismatch")
            repair_decisions = [
                item for item in decisions
                if item.get("turn_index") == turn_index
                and item.get("role") == role
                and item.get("response_hash") == repair_hash
                and item.get("attempt_kind") == "repair"
                and item.get("original_response_hash") == original_hash
            ] if repair_hash is not None else []
            if outcome == "accepted_first_pass":
                if original_errors or repair_hash is not None or repair_errors or accepted_hash != original_hash:
                    errors.append(f"{expected_arm}: invocation {index} first-pass outcome is inconsistent")
            elif outcome in ("accepted_repair", "failed_contract_after_repair"):
                if not original_errors or len(repair_decisions) != 1:
                    errors.append(f"{expected_arm}: invocation {index} does not bind one eligible repair")
                else:
                    referenced_v3_hashes[(turn_index, role, repair_hash, "repair")] += 1
                    envelope = {
                        "attempt": 1,
                        "role": role,
                        "turn_index": turn_index,
                        "state_hash": state_hash,
                        "original_validation_errors": original_errors,
                        "original_response_sha256": original_hash,
                        "replacement": repair_decisions[0].get("content"),
                    }
                    envelope_report = validate_repair_envelope(
                        envelope, role=role, turn_index=turn_index, state_hash=state_hash
                    )
                    if outcome == "accepted_repair" and (
                        repair_errors or envelope_report["status"] != "passed" or accepted_hash != repair_hash
                    ):
                        errors.append(f"{expected_arm}: invocation {index} accepted repair is inconsistent")
                    if outcome == "failed_contract_after_repair" and (
                        not repair_errors or accepted_hash is not None
                    ):
                        errors.append(f"{expected_arm}: invocation {index} terminal repair is inconsistent")
            else:
                errors.append(f"{expected_arm}: invocation {index} has unknown outcome")
            if outcome in ("accepted_first_pass", "accepted_repair"):
                key = (turn_index, role)
                if key in accepted_v3:
                    errors.append(f"{expected_arm}: duplicate accepted invocation for turn/role")
                elif isinstance(accepted_hash, str):
                    accepted_v3[key] = accepted_hash

        pending = flow.get("pending_invocation")
        pending_key = None
        pending_hash = None
        if isinstance(pending, dict):
            pending_key = (pending.get("turn_index"), pending.get("role"))
            pending_hash = pending.get("original_response_hash")
            pending_decisions = [
                item for item in decisions
                if item.get("turn_index") == pending_key[0]
                and item.get("role") == pending_key[1]
                and item.get("response_hash") == pending_hash
                and item.get("attempt_kind") == "original"
            ]
            if len(pending_decisions) != 1 or pending.get("state_hash") != stable_hash(state):
                errors.append(f"{expected_arm}: pending repair is not bound to current state/decision")
            else:
                report = validate_v3_role_output(pending_key[1], pending_decisions[0].get("content"))
                pending_errors = pending.get("original_validation_errors")
                invalid_binding = (
                    pending_key[1] == "actor"
                    and not isinstance(pending_errors, list)
                    or pending_key[1] != "actor"
                    and report["errors"] != pending_errors
                )
                if not pending_errors or invalid_binding:
                    errors.append(f"{expected_arm}: pending repair is not bound to a structural failure")
                referenced_v3_hashes[(pending_key[0], pending_key[1], pending_hash, "original")] += 1

        for decision in decisions:
            key = (
                decision.get("turn_index"), decision.get("role"),
                decision.get("response_hash"), decision.get("attempt_kind"),
            )
            if referenced_v3_hashes[key] != 1:
                errors.append(f"{expected_arm}: v3 decision is not referenced exactly once by invocation state")
                break

        journal_by_attempt: dict[str, list[dict[str, Any]]] = {}
        for row in call_journal:
            attempt_id = row.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                errors.append(f"{expected_arm}: call journal row lacks attempt id")
                continue
            journal_by_attempt.setdefault(attempt_id, []).append(row)
        usage_by_attempt = Counter(row.get("attempt_id") for row in usage)
        evidence_by_attempt = Counter(row.get("attempt_id") for row in [*decisions, *failures])
        for attempt_id, events in journal_by_attempt.items():
            if len(events) != 2 or events[0].get("event") != "started" or events[1].get("event") not in ("completed", "transport_failed"):
                errors.append(f"{expected_arm}: call journal attempt {attempt_id} is incomplete or reordered")
                continue
            if events[1].get("event") == "completed":
                if usage_by_attempt[attempt_id] != 1 or evidence_by_attempt[attempt_id] != 1:
                    errors.append(f"{expected_arm}: completed call journal attempt lacks one usage/evidence row")
            elif usage_by_attempt[attempt_id] or evidence_by_attempt[attempt_id]:
                errors.append(f"{expected_arm}: transport-failed attempt unexpectedly has usage/evidence")
        if set(item for item in usage_by_attempt if item is not None) != set(
            key for key, events in journal_by_attempt.items() if events[-1].get("event") == "completed"
        ):
            errors.append(f"{expected_arm}: v3 usage attempts differ from completed call journal")

        restart_receipt_path = run_dir / "gateway-restart-reconciliation.json"
        restart_failures = [
            row for row in failures
            if row.get("evidence_source") == "openclaw_gateway_restart_reconciliation"
        ]
        if restart_receipt_path.exists() or restart_failures:
            if not restart_receipt_path.is_file() or len(restart_failures) != 1:
                errors.append(f"{expected_arm}: gateway-restart evidence requires one receipt and one failure row")
            else:
                receipt = read_json(restart_receipt_path)
                failure = restart_failures[0]
                attempt_id = receipt.get("attempt_id")
                matching_usage = [row for row in usage if row.get("attempt_id") == attempt_id]
                matching_journal = journal_by_attempt.get(str(attempt_id), [])
                source = receipt.get("source") if isinstance(receipt.get("source"), dict) else {}
                pending = flow.get("pending_invocation")
                attempt_kind = _gateway_restart_attempt_kind(receipt)
                if attempt_kind == "repair":
                    attempt_specific_consistent = (
                        failure.get("attempt_kind") == "repair"
                        and isinstance(pending, dict)
                        and pending.get("role") == receipt.get("role")
                        and pending.get("turn_index") == receipt.get("turn_index")
                        and pending.get("state_hash") == failure.get("state_hash")
                        and pending.get("original_response_hash")
                        == failure.get("original_response_hash")
                        and isinstance(source.get("repair_message_sha256"), str)
                        and len(source["repair_message_sha256"]) == 64
                    )
                elif attempt_kind == "original":
                    attempt_specific_consistent = (
                        failure.get("attempt_kind") == "original"
                        and pending is None
                        and receipt.get("state_hash") == failure.get("state_hash")
                        and "original_response_hash" not in failure
                        and isinstance(source.get("original_message_sha256"), str)
                        and len(source["original_message_sha256"]) == 64
                    )
                else:
                    attempt_specific_consistent = False
                receipt_consistent = (
                    receipt.get("schema_version") == 1
                    and receipt.get("status") == "reconciled_failed_contract"
                    and receipt.get("run_id") == run_id
                    and receipt.get("arm") == expected_arm
                    and receipt.get("role") == failure.get("role")
                    and receipt.get("turn_index") == failure.get("turn_index")
                    and receipt.get("response_hash") == failure.get("response_hash")
                    and receipt.get("completed_gateway_run_id") == failure.get("gateway_run_id")
                    and receipt.get("interrupted_gateway_run_id") == failure.get("interrupted_gateway_run_id")
                    and receipt.get("provider_usage") == (matching_usage[0].get("usage") if len(matching_usage) == 1 else None)
                    and failure.get("failure_kind") == "gateway_restart_recovery"
                    and flow.get("status") == "failed_contract"
                    and attempt_specific_consistent
                    and len(matching_journal) == 2
                    and matching_journal[-1].get("outcome") == "gateway_restart_recovery_terminal"
                    and matching_journal[-1].get("gateway_run_id") == receipt.get("completed_gateway_run_id")
                    and matching_journal[-1].get("interrupted_gateway_run_id") == receipt.get("interrupted_gateway_run_id")
                    and source.get("kind") == "openclaw_gateway_restart_chain"
                    and isinstance(source.get("trace_id"), str)
                    and bool(source.get("trace_id"))
                    and source.get("trace_id") == failure.get("session_id")
                    and source.get("completed_event_sha256") == failure.get("trajectory_event_sha256")
                    and all(isinstance(source.get(key), str) and len(source[key]) == 64 for key in (
                        "trajectory_sha256", "session_log_sha256", "completed_event_sha256",
                        "restart_message_sha256", "response_message_sha256",
                    ))
                    and receipt.get("simulator_turns_added") == 0
                    and receipt.get("accepted_model_decisions_added") == 0
                    and receipt.get("provider_usage_rows_added") == 1
                )
                if not receipt_consistent:
                    errors.append(f"{expected_arm}: gateway-restart reconciliation receipt is inconsistent")
                else:
                    errors.extend(
                        _verify_gateway_restart_transaction(run_dir, manifest, receipt)
                    )
                for path_key, hash_key in (
                    ("trajectory_path", "trajectory_sha256"),
                    ("session_log_path", "session_log_sha256"),
                ):
                    source_path = source.get(path_key)
                    if isinstance(source_path, str) and Path(source_path).is_file():
                        if hashlib.sha256(Path(source_path).read_bytes()).hexdigest() != source.get(hash_key):
                            errors.append(f"{expected_arm}: available gateway-restart source hash mismatch")

        undispatched_receipt_path = run_dir / "undispatched-attempt-reconciliation.json"
        undispatched_terminal_rows = [
            row for row in call_journal
            if row.get("evidence_source")
            == "openclaw_undispatched_attempt_reconciliation"
        ]
        if undispatched_receipt_path.exists() or undispatched_terminal_rows:
            if not undispatched_receipt_path.is_file() or len(undispatched_terminal_rows) != 1:
                errors.append(
                    f"{expected_arm}: undispatched-attempt evidence requires one receipt "
                    "and one terminal journal row"
                )
            else:
                receipt = read_json(undispatched_receipt_path)
                terminal = undispatched_terminal_rows[0]
                attempt_id = receipt.get("attempt_id")
                matching_journal = journal_by_attempt.get(str(attempt_id), [])
                source = receipt.get("source") if isinstance(receipt.get("source"), dict) else {}
                zero_fields = (
                    "provider_calls_added",
                    "provider_usage_rows_added",
                    "model_failures_added",
                    "accepted_model_decisions_added",
                    "role_invocations_added",
                    "simulator_turns_added",
                )
                receipt_consistent = (
                    receipt.get("schema_version") == 1
                    and receipt.get("status") == "reconciled_failed_contract"
                    and receipt.get("run_id") == run_id
                    and receipt.get("arm") == expected_arm
                    and receipt.get("attempt_kind") == "original"
                    and receipt.get("role") == flow.get("phase")
                    and receipt.get("turn_index") == flow.get("turn_index")
                    and receipt.get("state_hash") == stable_hash(state)
                    and flow.get("status") == "failed_contract"
                    and flow.get("pending_invocation") is None
                    and len(matching_journal) == 2
                    and matching_journal[0].get("event") == "started"
                    and matching_journal[0].get("timestamp") == source.get("journal_started_at")
                    and matching_journal[0].get("attempt_kind") == "original"
                    and matching_journal[-1] == terminal
                    and terminal.get("event") == "transport_failed"
                    and terminal.get("error") == _UNDISPATCHED_REASON
                    and source.get("kind") == "openclaw_no_dispatch_boundary"
                    and isinstance(source.get("trace_id"), str)
                    and bool(source.get("trace_id"))
                    and isinstance(source.get("last_completed_gateway_run_id"), str)
                    and bool(source.get("last_completed_gateway_run_id"))
                    and source.get("trajectory_events_at_or_after_start") == 0
                    and source.get("session_messages_at_or_after_start") == 0
                    and all(
                        isinstance(source.get(key), str) and len(source[key]) == 64
                        for key in (
                            "trajectory_sha256",
                            "session_log_sha256",
                            "trajectory_last_event_sha256",
                            "session_last_message_sha256",
                        )
                    )
                    and all(receipt.get(key) == 0 for key in zero_fields)
                )
                if not receipt_consistent:
                    errors.append(
                        f"{expected_arm}: undispatched-attempt reconciliation receipt is inconsistent"
                    )
                else:
                    errors.extend(_verify_undispatched_transaction(run_dir, manifest, receipt))

                source_specs = (
                    (
                        "trajectory_path", "trajectory_sha256", "trajectory_rows",
                        "trajectory_last_timestamp", "trajectory_last_event_sha256", "ts",
                    ),
                    (
                        "session_log_path", "session_log_sha256", "session_log_rows",
                        "session_last_timestamp", "session_last_message_sha256", "timestamp",
                    ),
                )
                for path_key, hash_key, rows_key, time_key, last_hash_key, row_time_key in source_specs:
                    source_path = source.get(path_key)
                    if not isinstance(source_path, str) or not Path(source_path).is_file():
                        continue
                    path = Path(source_path)
                    if hashlib.sha256(path.read_bytes()).hexdigest() != source.get(hash_key):
                        errors.append(
                            f"{expected_arm}: available undispatched-attempt source hash mismatch"
                        )
                        continue
                    source_errors: list[str] = []
                    source_rows = _read_jsonl(path, source_errors)
                    if source_errors:
                        errors.extend(
                            f"{expected_arm}: undispatched source {item}" for item in source_errors
                        )
                        continue
                    if (
                        not source_rows
                        or len(source_rows) != source.get(rows_key)
                        or source_rows[-1].get(row_time_key) != source.get(time_key)
                        or hashlib.sha256(_canonical(source_rows[-1]).encode()).hexdigest()
                        != source.get(last_hash_key)
                    ):
                        errors.append(
                            f"{expected_arm}: undispatched-attempt source boundary mismatch"
                        )
                        continue
                    try:
                        started_at = datetime.fromisoformat(
                            str(source.get("journal_started_at", "")).replace("Z", "+00:00")
                        )
                        observed_times = [
                            datetime.fromisoformat(
                                str(row.get(row_time_key, "")).replace("Z", "+00:00")
                            )
                            for row in (
                                source_rows
                                if path_key == "trajectory_path"
                                else source_rows[1:]
                            )
                        ]
                    except ValueError:
                        errors.append(
                            f"{expected_arm}: undispatched-attempt source timestamp is invalid"
                        )
                    else:
                        if (
                            started_at.utcoffset() is None
                            or any(value.utcoffset() is None for value in observed_times)
                            or any(value >= started_at for value in observed_times)
                        ):
                            errors.append(
                                f"{expected_arm}: undispatched-attempt source crosses its journal boundary"
                            )

    for index, decision in enumerate(decisions):
        content = decision.get("content")
        if stable_hash(content) != decision.get("response_hash"):
            errors.append(f"{expected_arm}: decision {index} response hash mismatch")
        if protocol == "v2":
            role = decision.get("role")
            report = validate_v2_role_output(role, content) if isinstance(role, str) else {"status": "failed", "errors": ["missing role"]}
            if report["status"] != "passed" and not (
                isinstance(role, str)
                and is_terminal_contract_decision(index, decision, role, report["errors"])
            ):
                errors.append(f"{expected_arm}: invalid v2 {role} decision at call {index}: {'; '.join(report['errors'])}")

    for index, failure in enumerate(failures):
        raw_text = failure.get("raw_text")
        if not isinstance(raw_text, str):
            errors.append(f"{expected_arm}: model failure {index} is missing raw_text")
            continue
        if failure.get("failure_kind") == "model_drift":
            try:
                drift_content = parse_json_object(raw_text)
            except ModelTransportError:
                drift_identity = stable_hash(raw_text)
            else:
                drift_identity = stable_hash(drift_content)
            if drift_identity != failure.get("response_hash"):
                errors.append(f"{expected_arm}: model failure {index} response hash mismatch")
            if (
                failure.get("expected_provider") != "openai"
                or failure.get("expected_model") != str(manifest.get("model", "")).split("/", 1)[-1]
                or (
                    failure.get("observed_provider") == failure.get("expected_provider")
                    and failure.get("observed_model") == failure.get("expected_model")
                )
            ):
                errors.append(f"{expected_arm}: model failure {index} has invalid drift evidence")
        elif failure.get("failure_kind") == "gateway_restart_recovery":
            try:
                recovered_content = parse_json_object(raw_text)
            except ModelTransportError as exc:
                recovered_identity = stable_hash(raw_text)
                if failure.get("parse_error") != str(exc):
                    errors.append(f"{expected_arm}: model failure {index} parse error mismatch")
            else:
                recovered_identity = stable_hash(recovered_content)
                if failure.get("parse_error") is not None:
                    errors.append(f"{expected_arm}: model failure {index} has a spurious parse error")
            if recovered_identity != failure.get("response_hash"):
                errors.append(f"{expected_arm}: model failure {index} response hash mismatch")
        else:
            if stable_hash(raw_text) != failure.get("response_hash"):
                errors.append(f"{expected_arm}: model failure {index} response hash mismatch")
            try:
                parse_json_object(raw_text)
            except ModelTransportError as exc:
                if failure.get("parse_error") != str(exc):
                    errors.append(f"{expected_arm}: model failure {index} parse error mismatch")
            else:
                errors.append(f"{expected_arm}: model failure {index} contains valid JSON")

    evidence_counter = Counter(
        (item.get("role"), item.get("response_hash"))
        for item in [*decisions, *failures]
    )
    usage_evidence_counter = Counter(
        (item.get("role"), item.get("response_hash")) for item in usage
    )
    if evidence_counter != usage_evidence_counter:
        errors.append(f"{expected_arm}: usage/model evidence identity mismatch")

    expected_model = str(manifest.get("model", "")).split("/", 1)[-1]
    for index, row in enumerate(usage):
        if row.get("run_id") != run_id or row.get("arm") != expected_arm:
            errors.append(f"{expected_arm}: usage identity mismatch at call {index}")
        if row.get("seed") != manifest.get("seed"):
            errors.append(f"{expected_arm}: usage seed mismatch at call {index}")
        if row.get("provider") != "openai" or row.get("model") != expected_model:
            matching_drift = [
                failure for failure in failures
                if failure.get("failure_kind") == "model_drift"
                and failure.get("role") == row.get("role")
                and failure.get("response_hash") == row.get("response_hash")
                and failure.get("observed_provider") == row.get("provider")
                and failure.get("observed_model") == row.get("model")
            ]
            if (
                len(matching_drift) != 1
                or flow.get("status") != "failed_contract"
                or flow.get("phase") != row.get("role")
            ):
                errors.append(f"{expected_arm}: usage model/provider drift at call {index}")
        provider_usage = row.get("usage")
        usage_keys = ("input", "cache_read", "cache_write", "output", "total")
        if not isinstance(provider_usage, dict) or any(
            not isinstance(provider_usage.get(key), int) or provider_usage[key] < 0
            for key in usage_keys
        ):
            errors.append(f"{expected_arm}: invalid provider usage at call {index}")
        elif provider_usage["total"] != sum(provider_usage[key] for key in usage_keys[:-1]):
            errors.append(f"{expected_arm}: provider usage total mismatch at call {index}")

    run_counter = Counter(_canonical(row) for row in usage)
    ledger_counter = Counter(_canonical(row) for row in ledger_rows)
    for row, count in run_counter.items():
        if ledger_counter[row] != count:
            errors.append(f"{expected_arm}: global usage ledger occurrence mismatch")
            break

    simulator = VendingSimulator(scenario, int(manifest.get("seed", 0)))
    business_role = "actor" if expected_arm == "theatre" else "control"
    expected_calls: list[tuple[int, str]] = []
    last_planner: dict[str, Any] | None = None
    last_critic: dict[str, Any] | None = None

    def decision_at(turn_index: int, role: str) -> dict[str, Any] | None:
        candidates = [
            item for item in decisions
            if item.get("turn_index") == turn_index and item.get("role") == role
            and (
                protocol != "v3"
                or item.get("response_hash") == accepted_v3.get((turn_index, role))
            )
        ]
        if len(candidates) != 1:
            return None
        content = candidates[0].get("content")
        return content if isinstance(content, dict) else None

    def v2_schedule(turn_index: int, view: dict[str, Any], critic: dict[str, Any] | None = None) -> dict[str, Any]:
        cadence = manifest["v2_cadence"]
        critical_event = any(event.get("severity") == "critical" for event in view.get("recent_events", []))
        strategic_due = turn_index % int(cadence["strategic_review_every_turns"]) == 0 or critical_event
        consciousness_due = (
            turn_index == 0
            or turn_index % int(cadence["consciousness_every_turns"]) == 0
            or isinstance(critic, dict) and critic.get("verdict") == "critical"
        )
        return {
            "turn_index": turn_index,
            "strategic_review_due": strategic_due,
            "consciousness_due": consciousness_due,
            "critical_simulator_event": critical_event,
        }

    def v3_schedule(turn_index: int, view: dict[str, Any], critic: dict[str, Any] | None = None) -> dict[str, Any]:
        cadence = manifest["v3_cadence"]
        critical_event = any(event.get("severity") == "critical" for event in view.get("recent_events", []))
        strategic_due = turn_index % int(cadence["strategic_review_every_turns"]) == 0 or critical_event
        consciousness_due = (
            bool(cadence.get("consciousness_on_first_turn")) and turn_index == 0
            or turn_index % int(cadence["consciousness_every_turns"]) == 0
            or bool(cadence.get("consciousness_on_critical_verdict"))
            and isinstance(critic, dict) and critic.get("verdict") == "critical"
        )
        return {
            "turn_index": turn_index,
            "strategic_review_due": strategic_due,
            "consciousness_due": consciousness_due,
            "critical_simulator_event": critical_event,
        }

    for expected_index, turn in enumerate(turns):
        turn_index = turn.get("turn_index")
        if turn_index != expected_index:
            errors.append(f"{expected_arm}: non-contiguous turn index at {expected_index}")
            continue
        view = simulator.public_view()
        decision_audit = None
        if protocol in ("v2", "v3"):
            if expected_arm == "control":
                expected_calls.append((turn_index, "control"))
                content = decision_at(turn_index, "control")
                schedule = v3_schedule(turn_index, view) if protocol == "v3" else v2_schedule(turn_index, view)
                if content is None:
                    errors.append(f"{expected_arm}: turn {turn_index} has no unique control decision")
                    actions = []
                else:
                    if protocol == "v3":
                        report = validate_v3_role_output("control", content)
                        if report["status"] != "passed":
                            errors.append(f"{expected_arm}: turn {turn_index} control contract failed: {'; '.join(report['errors'])}")
                            actions = []
                        else:
                            actions = [item["action"] for item in content["execution_queue"]]
                    else:
                        try:
                            actions = extract_v2_actions("control", content)
                        except ValueError as exc:
                            errors.append(f"{expected_arm}: turn {turn_index} control contract failed: {exc}")
                            actions = []
                decision_audit = {
                    "schema_version": 2 if protocol == "v3" else 1,
                    "status": "passed",
                    "arm": "control",
                    "schedule": schedule,
                    **({"plan_timing": {item["id"]: item["timing"] for item in content["plan"]["action_queue"]}} if protocol == "v3" and isinstance(content, dict) else {}),
                    "action_count": len(actions),
                    "actions": actions,
                }
            else:
                critic = decision_at(turn_index, "critic")
                schedule = v3_schedule(turn_index, view, critic) if protocol == "v3" else v2_schedule(turn_index, view, critic)
                review = schedule["strategic_review_due"]
                roles = ["critic"] if review else []
                if review and schedule["consciousness_due"]:
                    roles.append("consciousness")
                if review:
                    roles.append("planner")
                roles.append("actor")
                expected_calls.extend((turn_index, role) for role in roles)
                if review:
                    last_critic = critic
                    last_planner = decision_at(turn_index, "planner")
                actor = decision_at(turn_index, "actor")
                consciousness = decision_at(turn_index, "consciousness") if review else None
                if protocol == "v3":
                    handoff = validate_v3_theatre_handoff(last_critic, last_planner, actor, consciousness)
                else:
                    handoff = validate_v2_theatre_handoff(
                        critic,
                        last_planner,
                        actor,
                        consciousness,
                        review_required=review,
                        consciousness_required=bool(review and schedule["consciousness_due"]),
                    )
                if handoff["status"] != "passed":
                    errors.append(f"{expected_arm}: turn {turn_index} handoff failed: {'; '.join(handoff['errors'])}")
                    actions = []
                else:
                    if protocol == "v3":
                        actions = [item["action"] for item in actor["execution_queue"]]
                    else:
                        try:
                            actions = extract_v2_actions("actor", actor)
                        except ValueError as exc:
                            errors.append(f"{expected_arm}: turn {turn_index} actor contract failed: {exc}")
                            actions = []
                decision_audit = {
                    "schema_version": 2 if protocol == "v3" else 1,
                    "status": "passed",
                    "arm": "theatre",
                    "schedule": schedule,
                    **{key: value for key, value in handoff.items() if key not in ("status", "errors")},
                    "action_count": len(actions),
                    "actions": actions,
                }
        else:
            if expected_arm == "theatre":
                review = (
                    turn_index % int(manifest.get("theatre_review_every_turns", 1)) == 0
                    or any(event.get("severity") == "critical" for event in view.get("recent_events", []))
                )
                if review:
                    expected_calls.extend(((turn_index, "critic"), (turn_index, "planner")))
            expected_calls.append((turn_index, business_role))
            content = decision_at(turn_index, business_role)
            actions = content.get("actions", []) if isinstance(content, dict) else []
            if not isinstance(actions, list):
                actions = []

        day_before = simulator.state["day"]
        applied = simulator.apply_turn(actions)
        if protocol in ("v2", "v3") and turn.get("decision_audit") != decision_audit:
            errors.append(f"{expected_arm}: turn {turn_index} decision audit mismatch")
        if turn.get("day_before") != day_before or turn.get("day_after") != simulator.state["day"]:
            errors.append(f"{expected_arm}: turn {turn_index} day boundary mismatch")
        if turn.get("accepted") != applied.accepted or turn.get("rejected") != applied.rejected:
            errors.append(f"{expected_arm}: turn {turn_index} action result mismatch")
        if turn.get("state_hash") != applied.state_hash:
            errors.append(f"{expected_arm}: turn {turn_index} replay hash mismatch")

    current_turn = len(turns)
    current_expected: list[tuple[int, str]] = []
    if not simulator.state["terminated"]:
        if protocol in ("v2", "v3"):
            view = simulator.public_view()
            if expected_arm == "control":
                roles = ["control"]
            else:
                critic = decision_at(current_turn, "critic")
                schedule = v3_schedule(current_turn, view, critic) if protocol == "v3" else v2_schedule(current_turn, view, critic)
                roles = ["critic"] if schedule["strategic_review_due"] else []
                if schedule["strategic_review_due"] and schedule["consciousness_due"]:
                    roles.append("consciousness")
                if schedule["strategic_review_due"]:
                    roles.append("planner")
                roles.append("actor")
            current_expected.extend((current_turn, role) for role in roles)
        else:
            if expected_arm == "theatre":
                view = simulator.public_view()
                review = (
                    current_turn % int(manifest.get("theatre_review_every_turns", 1)) == 0
                    or any(event.get("severity") == "critical" for event in view.get("recent_events", []))
                )
                if review:
                    current_expected.extend(((current_turn, "critic"), (current_turn, "planner")))
            current_expected.append((current_turn, business_role))
    cadence_source = [*decisions, *failures]
    if protocol == "v3":
        cadence_source = [*invocations, *failures]
        has_terminal_restart_recovery = any(
            item.get("evidence_source") == "openclaw_gateway_restart_reconciliation"
            for item in failures
        )
        if isinstance(flow.get("pending_invocation"), dict) and not has_terminal_restart_recovery:
            cadence_source.append({
                "timestamp": flow.get("updated_at", ""),
                "turn_index": flow["pending_invocation"].get("turn_index"),
                "role": flow["pending_invocation"].get("role"),
            })
    attempts = sorted(
        [(item.get("timestamp", ""), index, item) for index, item in enumerate(cadence_source)],
        key=lambda item: (item[0], item[1]),
    )
    cadence_attempts: list[dict[str, Any]] = []
    for _, _, item in attempts:
        call = (item.get("turn_index"), item.get("role"))
        previous = (
            (cadence_attempts[-1].get("turn_index"), cadence_attempts[-1].get("role"))
            if cadence_attempts else None
        )
        if (
            item.get("evidence_source") == "openclaw_trajectory_reconciliation"
            and previous == call
        ):
            # A pre-recorder bug could repeat the same terminal phase. Count
            # every provider call as evidence/usage, but never pretend the
            # duplicate was another protocol phase.
            continue
        cadence_attempts.append(item)
    actual_calls = [(item.get("turn_index"), item.get("role")) for item in cadence_attempts]
    if actual_calls[:len(expected_calls)] != expected_calls:
        errors.append(f"{expected_arm}: completed role cadence/order mismatch")
    pending_calls = actual_calls[len(expected_calls):]
    if pending_calls != current_expected[:len(pending_calls)] or len(pending_calls) > len(current_expected):
        errors.append(f"{expected_arm}: pending role cadence/order mismatch")

    if simulator.state != state:
        errors.append(f"{expected_arm}: persisted state differs from replayed state")
    if flow.get("turn_index") != len(turns):
        errors.append(f"{expected_arm}: flow turn_index does not match turns ledger")
    if flow.get("current_step") == "prepare_turn" and (
        flow.get("phase") is not None or flow.get("pending") not in ({}, None)
    ):
        errors.append(f"{expected_arm}: prepare_turn flow contains a pending role phase")
    if protocol == "v3":
        if flow.get("current_step") == "model_roles" and flow.get("turn_state_hash") != stable_hash(state):
            errors.append(f"{expected_arm}: v3 active turn state identity mismatch")
        terminal_repairs = [row for row in invocations if row.get("outcome") == "failed_contract_after_repair"]
        if terminal_repairs and flow.get("status") != "failed_contract":
            errors.append(f"{expected_arm}: terminal repair evidence exists without failed_contract flow")
    if failures:
        if flow.get("status") != "failed_contract":
            errors.append(f"{expected_arm}: model failure exists without failed_contract flow")
        current_role = flow.get("phase")
        for index, failure in enumerate(failures):
            if failure.get("turn_index") != len(turns) or failure.get("role") != current_role:
                errors.append(f"{expected_arm}: model failure {index} does not match the terminal flow phase")
        reconciled = [
            item for item in failures
            if item.get("evidence_source") == "openclaw_trajectory_reconciliation"
        ]
        if reconciled:
            ordinals = [item.get("attempt_index") for item in reconciled]
            gateway_ids = [item.get("gateway_run_id") for item in reconciled]
            if len(reconciled) != len(failures):
                errors.append(f"{expected_arm}: forensic failed phase contains non-forensic evidence")
            if ordinals != list(range(1, len(reconciled) + 1)):
                errors.append(f"{expected_arm}: reconciled failure attempt order is invalid")
            if any(not isinstance(value, str) or not value for value in gateway_ids) or len(set(gateway_ids)) != len(gateway_ids):
                errors.append(f"{expected_arm}: reconciled failure gateway ids are missing or duplicated")
            receipt_path = run_dir / "evidence-reconciliation.json"
            if not receipt_path.is_file():
                errors.append(f"{expected_arm}: forensic failed phase lacks reconciliation receipt")
            else:
                receipt = read_json(receipt_path)
                receipt_events = receipt.get("events", [])
                receipt_ids = [item.get("gateway_run_id") for item in receipt_events]
                if receipt.get("status") != "reconciled_failed_contract" or receipt_ids != gateway_ids:
                    errors.append(f"{expected_arm}: reconciliation receipt does not match failures")
                if (
                    receipt.get("run_id") != run_id
                    or receipt.get("arm") != expected_arm
                    or receipt.get("turn_index") != len(turns)
                    or receipt.get("role") != current_role
                ):
                    errors.append(f"{expected_arm}: reconciliation receipt identity mismatch")
                if receipt.get("model_decisions_added") != 0 or receipt.get("simulator_turns_added") != 0:
                    errors.append(f"{expected_arm}: reconciliation receipt claims a state transition")
                receipt_source = receipt.get("source", {})
                source_sha = receipt_source.get("sha256") if isinstance(receipt_source, dict) else None
                if not isinstance(source_sha, str) or len(source_sha) != 64:
                    errors.append(f"{expected_arm}: reconciliation source hash is invalid")
                source_path = receipt_source.get("path") if isinstance(receipt_source, dict) else None
                if isinstance(source_path, str) and Path(source_path).is_file():
                    if hashlib.sha256(Path(source_path).read_bytes()).hexdigest() != source_sha:
                        errors.append(f"{expected_arm}: available reconciliation source hash mismatch")
                usage_by_gateway = {
                    item.get("gateway_run_id"): item
                    for item in usage
                    if item.get("gateway_run_id") in gateway_ids
                }
                for failure, event in zip(reconciled, receipt_events):
                    usage_row = usage_by_gateway.get(failure.get("gateway_run_id"), {})
                    if (
                        event.get("response_hash") != failure.get("response_hash")
                        or event.get("trajectory_event_sha256") != failure.get("trajectory_event_sha256")
                        or event.get("provider_usage") != usage_row.get("usage")
                        or event.get("duration_ms") != usage_row.get("duration_ms")
                    ):
                        errors.append(f"{expected_arm}: reconciliation event does not bind raw failure evidence")
                        break

    def usage_sum(key: str) -> int:
        return sum(
            value
            for row in usage
            if isinstance(row.get("usage"), dict)
            for value in [row["usage"].get(key)]
            if isinstance(value, int) and value >= 0
        )

    output_tokens = usage_sum("output")
    expected_score = VendingSimulator(scenario, int(manifest.get("seed", 0)), state=state).score(output_tokens)
    result_path = run_dir / "result.json"
    if result_path.exists():
        result = read_json(result_path)
        if result.get("state_hash") != stable_hash(state):
            errors.append(f"{expected_arm}: final result state hash mismatch")
        if result.get("score") != expected_score:
            errors.append(f"{expected_arm}: final result economic score mismatch")

    total_tokens = usage_sum("total")
    repair_rows = [row for row in usage if row.get("attempt_kind") == "repair"]
    return {
        "run_id": run_id,
        "arm": expected_arm,
        "day": state.get("day"),
        "turns": len(turns),
        "model_calls": len(usage),
        "model_failures": len(failures),
        "first_pass_contract_failures": sum(
            1 for row in invocations if row.get("original_validation_errors")
        ) + sum(
            1 for row in failures
            if row.get("failure_kind") == "gateway_restart_recovery"
            and row.get("attempt_kind") == "repair"
        ) if protocol == "v3" else 0,
        "successful_repairs": sum(
            1 for row in invocations if row.get("outcome") == "accepted_repair"
        ) if protocol == "v3" else 0,
        "terminal_repair_failures": sum(
            1 for row in invocations if row.get("outcome") == "failed_contract_after_repair"
        ) + sum(
            1 for row in failures
            if row.get("failure_kind") == "gateway_restart_recovery"
            and row.get("attempt_kind") == "repair"
        ) if protocol == "v3" else 0,
        "repair_calls": len(repair_rows) if protocol == "v3" else 0,
        "repair_tokens": sum(
            int(row.get("usage", {}).get("total", 0)) for row in repair_rows
        ) if protocol == "v3" else 0,
        "provider_total_tokens": total_tokens,
        "output_tokens": output_tokens,
        "liquid_cash": expected_score["liquid_cash"],
        "primary_score_if_stopped_now": expected_score["primary_score"],
        "replay_state_hash": stable_hash(simulator.state),
    }, errors


def verify_pair(pair_dir: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    pair_dir = pair_dir.resolve()
    errors: list[str] = []
    pair_path = pair_dir / "pair.json"
    if not pair_path.is_file():
        return {"schema_version": 1, "status": "failed", "errors": ["missing pair.json"], "runs": {}}
    pair = read_json(pair_path)
    ledger_path = ledger_path or pair_dir.parents[1] / "usage-ledger.jsonl"
    ledger_rows = _read_jsonl(ledger_path, errors)

    runs: dict[str, dict[str, Any]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    scenarios: dict[str, dict[str, Any]] = {}
    run_results: dict[str, dict[str, Any]] = {}
    for arm in ("control", "theatre"):
        raw_path = pair.get(f"{arm}_run")
        if not isinstance(raw_path, str):
            errors.append(f"pair: missing {arm}_run")
            continue
        run_dir = Path(raw_path)
        if not run_dir.is_absolute():
            run_dir = (pair_dir / run_dir).resolve()
        summary, run_errors = _verify_run(run_dir, arm, ledger_rows)
        runs[arm] = summary
        errors.extend(run_errors)
        if (run_dir / "manifest.json").is_file():
            manifests[arm] = read_json(run_dir / "manifest.json")
        if (run_dir / "scenario.json").is_file():
            scenarios[arm] = read_json(run_dir / "scenario.json")
        if (run_dir / "result.json").is_file():
            run_results[arm] = read_json(run_dir / "result.json")

    if set(manifests) == {"control", "theatre"}:
        protocol = pair.get("protocol_version", "v1")
        parity_fields = ("seed", "model", "thinking", "scenario_hash", "decision_period_days")
        if protocol in ("v2", "v3"):
            parity_fields += (
                "protocol_version",
                "artifact_hashes",
                "shared_corpus_hash",
                "protocol_hash",
                "action_budget",
                f"{protocol}_cadence",
                "usage_ledger_path",
                "preregistration_sha256",
                "source_commit",
                "activation_receipt_sha256",
                "inference_enabled",
                "official",
            )
            if protocol == "v3":
                parity_fields += ("repair_policy",)
        for field in parity_fields:
            if manifests["control"].get(field) != manifests["theatre"].get(field):
                errors.append(f"pair: manifest parity mismatch for {field}")
        if pair.get("seed") != manifests["control"].get("seed"):
            errors.append("pair: seed differs from run manifests")
        if pair.get("model") != manifests["control"].get("model"):
            errors.append("pair: model differs from run manifests")
        if pair.get("thinking") != manifests["control"].get("thinking"):
            errors.append("pair: thinking differs from run manifests")
        if manifests["control"].get("prompt_hashes") != manifests["theatre"].get("prompt_hashes"):
            errors.append("pair: prompt hash parity mismatch")

        if protocol in ("v2", "v3"):
            try:
                plan = v3_seed_plan(int(pair.get("seed"))) if protocol == "v3" else v2_seed_plan(int(pair.get("seed")))
            except (TypeError, ValueError) as exc:
                errors.append(f"pair: invalid preregistered seed: {exc}")
            else:
                if pair.get("first_arm") != plan["first_arm"]:
                    errors.append("pair: first arm differs from preregistered order")
                if pair.get("days") != plan["days"]:
                    errors.append("pair: horizon differs from preregistration")
                if pair.get("model") != plan["model"] or pair.get("thinking") != plan["thinking"]:
                    errors.append("pair: model/thinking differs from preregistration")

            prereg_path = pair_dir / "preregistration.json"
            if not prereg_path.is_file():
                errors.append(f"pair: missing frozen preregistration-{protocol}.json")
                prereg_sha = None
            else:
                prereg_sha = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
                if pair.get("preregistration_sha256") != prereg_sha:
                    errors.append("pair: frozen preregistration hash mismatch")
                published_prereg = V3_PREREGISTRATION if protocol == "v3" else V2_PREREGISTRATION
                if prereg_sha != hashlib.sha256(published_prereg.read_bytes()).hexdigest():
                    errors.append("pair: frozen preregistration differs from published source")
            live_audit = audit_v3_preregistration() if protocol == "v3" else audit_v2_preregistration()
            if live_audit["status"] != "passed":
                errors.append(f"pair: published {protocol} preregistration audit failed")
            elif pair.get("artifact_hashes") != live_audit["observed_hashes"]:
                errors.append("pair: frozen artifact hashes differ from published source")
            for arm in ("control", "theatre"):
                manifest = manifests[arm]
                if manifest.get("pair_id") != pair.get("pair_id"):
                    errors.append(f"pair: {arm} manifest pair identity mismatch")
                if manifest.get("preregistration_sha256") != prereg_sha:
                    errors.append(f"pair: {arm} manifest preregistration hash mismatch")
                if manifest.get("artifact_hashes") != pair.get("artifact_hashes"):
                    errors.append(f"pair: {arm} manifest artifact hashes differ from pair")
                if manifest.get("usage_ledger_path") != str(ledger_path.resolve()):
                    errors.append(f"pair: {arm} manifest usage ledger is not the run-root ledger")

            enabled = pair.get("inference_enabled") is True
            activation_path = pair_dir / "activation.json"
            if enabled:
                if pair.get("official") is not True:
                    errors.append(f"pair: activated {protocol} pair is not marked official")
                if not activation_path.is_file():
                    errors.append("pair: inference enabled without activation receipt")
                else:
                    activation_sha = hashlib.sha256(activation_path.read_bytes()).hexdigest()
                    activation = read_json(activation_path)
                    if pair.get("activation_receipt_sha256") != activation_sha:
                        errors.append("pair: activation receipt hash mismatch")
                    if activation.get("pair_id") != pair.get("pair_id"):
                        errors.append("pair: activation receipt pair identity mismatch")
                    if activation.get("source_commit") != pair.get("source_commit"):
                        errors.append("pair: activation source commit mismatch")
                    if activation.get("preregistration_sha256") != prereg_sha:
                        errors.append("pair: activation preregistration hash mismatch")
                    if activation.get("artifact_hashes") != pair.get("artifact_hashes"):
                        errors.append("pair: activation artifact hashes mismatch")
                    if activation.get("official") is not True:
                        errors.append("pair: activation receipt is not marked official")
                    if activation.get("protocol") != protocol:
                        errors.append("pair: activation protocol mismatch")
                for arm in ("control", "theatre"):
                    if manifests[arm].get("official") is not True:
                        errors.append(f"pair: activated {arm} manifest is not marked official")
            elif activation_path.exists() or pair.get("activation_receipt_sha256") is not None:
                errors.append("pair: offline pair contains an activation receipt")
    if set(scenarios) == {"control", "theatre"}:
        if scenarios["control"] != scenarios["theatre"]:
            errors.append("pair: scenario snapshots differ")
        if pair.get("days") != scenarios["control"].get("days"):
            errors.append("pair: horizon differs from scenario snapshot")

    pair_result = pair_dir / "result.json"
    if pair_result.exists() and all(
        "primary_score_if_stopped_now" in runs.get(arm, {}) for arm in ("control", "theatre")
    ):
        result = read_json(pair_result)
        if pair.get("status") != "completed":
            errors.append("pair: result exists but pair status is not completed")
        for arm in ("control", "theatre"):
            if arm not in run_results:
                errors.append(f"pair: final {arm} run result is missing")
            elif result.get(arm) != run_results[arm]:
                errors.append(f"pair: embedded {arm} result mismatch")
            if manifests.get(arm, {}).get("status") != "completed":
                errors.append(f"pair: final {arm} manifest is not completed")
        expected_difference = round(
            runs["theatre"]["primary_score_if_stopped_now"]
            - runs["control"]["primary_score_if_stopped_now"],
            2,
        )
        expected_winner = "theatre" if expected_difference > 0 else "control" if expected_difference < 0 else "tie"
        if result.get("paired_difference_theatre_minus_control") != expected_difference:
            errors.append("pair: final paired difference mismatch")
        if result.get("winner") != expected_winner:
            errors.append("pair: final winner mismatch")

    return {
        "schema_version": 1,
        "status": "passed" if not errors else "failed",
        "pair_id": pair.get("pair_id"),
        "pair_status": pair.get("status"),
        "ledger_path": str(ledger_path),
        "runs": runs,
        "errors": errors,
    }
