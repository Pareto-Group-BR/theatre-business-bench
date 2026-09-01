from __future__ import annotations

import fcntl
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .runner import _v3_repair_message, atomic_json, read_json, utc_now
from .simulator import stable_hash
from .transport import ModelTransportError, parse_json_object
from .v2 import V2ContractError
from .v3 import V3ContractError


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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
                raise V2ContractError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise V2ContractError(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)
    temporary = path.with_name(path.name + ".reconcile-tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


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


def _json_sha256(value: Any) -> str:
    raw = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    return hashlib.sha256(raw).hexdigest()


def _require_official_lock() -> None:
    raw_fd = os.environ.get("THEATRE_OFFICIAL_LOCK_FD")
    try:
        if raw_fd is None:
            raise ValueError("missing")
        fd = int(raw_fd)
        held = os.fstat(fd)
        runtime_dir = Path(os.environ.get("THEATRE_RUNTIME_DIR", ".runtime")).resolve()
        expected = os.stat(runtime_dir / "official-inference.lock")
        if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("wrong descriptor")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ValueError, OSError) as exc:
        raise V3ContractError(
            "v3 forensic reconciliation requires the canonical global-lock wrapper"
        ) from exc


def _restart_protected_artifacts(
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


def _trajectory_events(
    path: Path, gateway_run_ids: list[str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    wanted = set(gateway_run_ids)
    if len(wanted) != len(gateway_run_ids):
        raise V2ContractError("gateway run ids must be unique")
    rows = _read_jsonl(path)
    events = [
        row for row in rows
        if row.get("type") == "model.completed" and row.get("runId") in wanted
    ]
    starts = {
        str(row.get("runId")): str(row.get("ts"))
        for row in rows
        if row.get("type") == "session.started" and row.get("runId") in wanted
    }
    found = [str(row.get("runId")) for row in events]
    missing = [run_id for run_id in gateway_run_ids if run_id not in found]
    non_unique = sorted(run_id for run_id in wanted if found.count(run_id) != 1)
    if missing or non_unique:
        raise V2ContractError(
            f"trajectory selection mismatch: missing={missing}, non_unique={non_unique}"
        )
    missing_starts = [run_id for run_id in gateway_run_ids if run_id not in starts]
    if missing_starts:
        raise V2ContractError(f"trajectory is missing session.started events: {missing_starts}")
    by_id = {str(row["runId"]): row for row in events}
    return [by_id[run_id] for run_id in gateway_run_ids], starts


def reconcile_openclaw_failures(
    pair_dir: Path,
    arm: str,
    trajectory_path: Path,
    gateway_run_ids: list[str],
) -> dict[str, Any]:
    """Import pre-fix failed calls from their immutable OpenClaw trace."""
    pair_dir = pair_dir.resolve()
    trajectory_path = trajectory_path.resolve()
    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    if pair.get("protocol_version") != "v2" or pair.get("official") is not True:
        raise V2ContractError("forensic reconciliation requires an official v2 pair")
    if arm not in ("control", "theatre"):
        raise V2ContractError("arm must be control or theatre")
    if not gateway_run_ids:
        raise V2ContractError("at least one gateway run id is required")

    run_dir = Path(pair[f"{arm}_run"]).resolve()
    manifest = read_json(run_dir / "manifest.json")
    flow_path = run_dir / "flow.json"
    flow = read_json(flow_path)
    role = flow.get("phase")
    allowed_roles = (
        {"control"}
        if arm == "control"
        else {"critic", "consciousness", "planner", "actor"}
    )
    if manifest.get("protocol_version") != "v2" or manifest.get("official") is not True:
        raise V2ContractError("selected run is not an official v2 run")
    if role not in allowed_roles or flow.get("current_step") != "model_roles":
        raise V2ContractError("selected run is not stopped at the expected model role")
    if flow.get("status") not in ("running", "failed_contract"):
        raise V2ContractError(f"run flow cannot be reconciled from status {flow.get('status')!r}")

    safe_run = str(manifest["run_id"]).lower().replace("_", "-").replace(":", "-")
    expected_session_key = f"agent:{manifest['agent_id']}:bench-{safe_run}-{role}"
    events, started_at = _trajectory_events(trajectory_path, gateway_run_ids)
    source_sha256 = hashlib.sha256(trajectory_path.read_bytes()).hexdigest()
    source_trace_ids = {str(event.get("traceId", "")) for event in events}
    if len(source_trace_ids) != 1 or "" in source_trace_ids:
        raise V2ContractError("selected events do not share one explicit trace id")

    existing_usage = _read_jsonl(run_dir / "usage.jsonl")
    existing_failures = _read_jsonl(run_dir / "model-failures.jsonl")
    ledger_path = Path(manifest["usage_ledger_path"])
    existing_ledger = _read_jsonl(ledger_path)
    existing_gateway_ids = {
        str(row.get("gateway_run_id")) for row in existing_failures
        if row.get("gateway_run_id")
    }
    if existing_gateway_ids and existing_gateway_ids != set(gateway_run_ids):
        raise V2ContractError(
            "an existing reconciliation must be replayed with its complete gateway run-id set"
        )
    imported_usage: list[dict[str, Any]] = []
    imported_failures: list[dict[str, Any]] = []
    receipt_events: list[dict[str, Any]] = []

    for attempt_index, event in enumerate(events, 1):
        run_id = str(event["runId"])
        data = event.get("data")
        if not isinstance(data, dict):
            raise V2ContractError(f"{run_id}: model.completed event is missing data")
        texts = data.get("assistantTexts")
        if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
            raise V2ContractError(f"{run_id}: expected exactly one assistant text")
        raw_text = texts[0]
        try:
            parse_json_object(raw_text)
        except ModelTransportError as exc:
            parse_error = str(exc)
        else:
            raise V2ContractError(f"{run_id}: selected response is valid JSON, not failed evidence")
        if event.get("sessionKey") != expected_session_key:
            raise V2ContractError(f"{run_id}: session key does not identify the selected run/role")
        if event.get("provider") != "openai" or event.get("modelId") != str(manifest["model"]).split("/", 1)[-1]:
            raise V2ContractError(f"{run_id}: provider/model differs from the frozen manifest")
        if any(data.get(key) not in (False, None) for key in ("timedOut", "aborted", "promptError")):
            raise V2ContractError(f"{run_id}: event is not a completed provider response")
        raw_usage = data.get("usage")
        if not isinstance(raw_usage, dict):
            raise V2ContractError(f"{run_id}: missing provider usage")
        usage = {
            "input": int(raw_usage.get("input", 0)),
            "cache_read": int(raw_usage.get("cacheRead", 0)),
            "cache_write": int(raw_usage.get("cacheWrite", 0)),
            "output": int(raw_usage.get("output", 0)),
            "total": int(raw_usage.get("total", 0)),
        }
        if usage["total"] != sum(usage[key] for key in ("input", "cache_read", "cache_write", "output")):
            raise V2ContractError(f"{run_id}: provider usage total is inconsistent")
        response_hash = stable_hash(raw_text)
        timestamp = str(event.get("ts", ""))
        try:
            started = datetime.fromisoformat(started_at[run_id].replace("Z", "+00:00"))
            completed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise V2ContractError(f"{run_id}: invalid trajectory timestamp") from exc
        duration_ms = int((completed - started).total_seconds() * 1000)
        if duration_ms < 0:
            raise V2ContractError(f"{run_id}: completion predates session start")
        event_sha256 = hashlib.sha256(_canonical(event).encode()).hexdigest()
        usage_row = {
            "timestamp": timestamp,
            "run_id": manifest["run_id"],
            "arm": arm,
            "seed": manifest["seed"],
            "role": role,
            "gateway_run_id": run_id,
            "session_id": str(event.get("sessionId", "")),
            "provider": "openai",
            "model": event["modelId"],
            "duration_ms": duration_ms,
            "usage": usage,
            "response_hash": response_hash,
            "outcome": "invalid_model_json",
            "parse_error": parse_error,
            "evidence_source": "openclaw_trajectory_reconciliation",
        }
        failure_row = {
            "timestamp": timestamp,
            "duration_ms": duration_ms,
            "turn_index": flow["turn_index"],
            "role": role,
            "raw_text": raw_text,
            "response_hash": response_hash,
            "parse_error": parse_error,
            "gateway_run_id": run_id,
            "session_id": str(event.get("sessionId", "")),
            "attempt_index": attempt_index,
            "evidence_source": "openclaw_trajectory_reconciliation",
            "trajectory_event_sha256": event_sha256,
        }
        receipt_events.append({
            "gateway_run_id": run_id,
            "timestamp": timestamp,
            "duration_ms": duration_ms,
            "response_hash": response_hash,
            "provider_usage": usage,
            "trajectory_event_sha256": event_sha256,
        })
        imported_usage.append(usage_row)
        imported_failures.append(failure_row)

    selected = set(gateway_run_ids)
    _atomic_jsonl(
        run_dir / "usage.jsonl",
        [row for row in existing_usage if row.get("gateway_run_id") not in selected] + imported_usage,
    )
    _atomic_jsonl(
        run_dir / "model-failures.jsonl",
        [row for row in existing_failures if row.get("gateway_run_id") not in selected] + imported_failures,
    )
    new_ledger = [
        row for row in existing_ledger
        if not (row.get("run_id") == manifest["run_id"] and row.get("gateway_run_id") in selected)
    ] + imported_usage
    _atomic_jsonl(ledger_path, new_ledger)

    reconciled_at = utc_now()
    flow["status"] = "failed_contract"
    flow["updated_at"] = reconciled_at
    flow["contract_failure"] = {
        "phase": role,
        "message": "historical invalid model JSON reconciled from OpenClaw trajectory",
        "attempts": len(gateway_run_ids),
    }
    atomic_json(flow_path, flow)
    pair["status"] = "failed_contract"
    pair["last_arm"] = arm
    pair["last_result"] = {
        "status": "failed_contract",
        "reason": "historical invalid model JSON reconciled from OpenClaw trajectory",
        "run_dir": str(run_dir),
        "attempts": len(gateway_run_ids),
    }
    pair["updated_at"] = reconciled_at
    atomic_json(pair_path, pair)

    receipt = {
        "schema_version": 1,
        "status": "reconciled_failed_contract",
        "reconciled_at": reconciled_at,
        "pair_id": pair["pair_id"],
        "run_id": manifest["run_id"],
        "arm": arm,
        "turn_index": flow["turn_index"],
        "role": role,
        "source": {
            "kind": "openclaw_trajectory",
            "path": str(trajectory_path),
            "sha256": source_sha256,
            "trace_id": next(iter(source_trace_ids)),
        },
        "events": receipt_events,
        "simulator_turns_added": 0,
        "model_decisions_added": 0,
    }
    atomic_json(run_dir / "evidence-reconciliation.json", receipt)
    return receipt


def _message_text(row: dict[str, Any]) -> str | None:
    message = row.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
            return None
        parts.append(item["text"])
    return "".join(parts)


def _finish_v3_restart_reconciliation(
    pair_dir: Path, run_dir: Path, other_run_dir: Path, receipt_path: Path,
    receipt: dict[str, Any], *, resume: bool = True,
) -> dict[str, Any]:
    transaction = receipt.get("transaction")
    if not isinstance(transaction, dict):
        raise V3ContractError("gateway-restart receipt lacks its durable transaction")
    source = transaction.get("source", {})
    target = transaction.get("target", {})
    rows = transaction.get("rows", {})
    ledger_path = Path(transaction.get("ledger_path", "")).resolve()
    expected_ledger_path = Path(
        read_json(run_dir / "manifest.json")["usage_ledger_path"]
    ).resolve()
    if ledger_path != expected_ledger_path:
        raise V3ContractError("gateway-restart transaction targets the wrong usage ledger")
    paths = {
        "usage": run_dir / "usage.jsonl",
        "failures": run_dir / "model-failures.jsonl",
        "ledger": ledger_path,
        "journal": run_dir / "call-journal.jsonl",
    }
    expected_row_keys = {"usage", "failures", "ledger", "journal"}
    if set(rows) != expected_row_keys or set(source.get("files", {})) != expected_row_keys:
        raise V3ContractError("gateway-restart transaction has an invalid file set")
    if set(target.get("files", {})) != expected_row_keys:
        raise V3ContractError("gateway-restart transaction target set is invalid")
    usage_row = rows.get("usage", [])[-1] if rows.get("usage") else {}
    failure_row = rows.get("failures", [])[-1] if rows.get("failures") else {}
    ledger_row = rows.get("ledger", [])[-1] if rows.get("ledger") else {}
    journal_row = rows.get("journal", [])[-1] if rows.get("journal") else {}
    expected_identity = {
        "attempt_id": receipt.get("attempt_id"),
        "role": receipt.get("role"),
        "turn_index": receipt.get("turn_index"),
    }
    if (
        any(usage_row.get(key) != value for key, value in expected_identity.items())
        or any(failure_row.get(key) != value for key, value in expected_identity.items())
        or any(journal_row.get(key) != value for key, value in expected_identity.items())
        or usage_row.get("usage") != receipt.get("provider_usage")
        or usage_row.get("response_hash") != receipt.get("response_hash")
        or failure_row.get("response_hash") != receipt.get("response_hash")
        or failure_row.get("failure_kind") != "gateway_restart_recovery"
        or ledger_row != usage_row
        or journal_row.get("event") != "completed"
        or journal_row.get("outcome") != "gateway_restart_recovery_terminal"
        or journal_row.get("gateway_run_id") != receipt.get("completed_gateway_run_id")
        or journal_row.get("interrupted_gateway_run_id") != receipt.get("interrupted_gateway_run_id")
    ):
        raise V3ContractError("gateway-restart transaction rows exceed the forensic receipt")
    for name in expected_row_keys:
        source_record = source["files"][name]
        target_rows = rows[name]
        if len(target_rows) != int(source_record.get("rows", -1)) + 1:
            raise V3ContractError(f"gateway-restart {name} does not append exactly one row")
        prefix_sha = _jsonl_sha256(target_rows[:-1])
        if source_record.get("sha256") is None:
            if target_rows[:-1]:
                raise V3ContractError(f"gateway-restart {name} source absence is inconsistent")
        elif prefix_sha != source_record.get("sha256"):
            raise V3ContractError(f"gateway-restart {name} target does not preserve source rows")
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
        raise V3ContractError("gateway-restart transaction id does not bind its source evidence")
    for name, path in paths.items():
        target_rows = rows[name]
        if not isinstance(target_rows, list) or _jsonl_sha256(target_rows) != target["files"][name]:
            raise V3ContractError(f"gateway-restart {name} target hash is invalid")
        current_record = _file_record(path)
        current = current_record["sha256"]
        if current == source["files"][name]["sha256"]:
            if not resume:
                raise V3ContractError(
                    f"completed gateway-restart reconciliation left {name} uncommitted"
                )
            _atomic_jsonl(path, target_rows)
        elif name == "ledger":
            current_raw = path.read_bytes() if path.exists() else b""
            if not current_raw.startswith(_jsonl_bytes(target_rows)):
                raise V3ContractError(
                    "gateway-restart ledger changed before its append-only continuation"
                )
        elif current != target["files"][name]:
            raise V3ContractError(
                f"gateway-restart {name} changed outside the prepared transaction"
            )

    reason = (
        "gateway restarted during the frozen repair; auto-continuation "
        "preserved and charged but not applied"
    )
    expected_flow_transition = {
        "status": "failed_contract",
        "updated_at": receipt.get("reconciled_at"),
        "contract_failure": {"phase": receipt.get("role"), "message": reason},
    }
    expected_pair_transition = {
        "status": "failed_contract",
        "last_arm": receipt.get("arm"),
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
        raise V3ContractError("gateway-restart transaction exceeds its allowed state transition")

    flow_path = run_dir / "flow.json"
    flow = read_json(flow_path)
    flow_sha = _file_record(flow_path)["sha256"]
    if flow_sha == source["flow"]["sha256"]:
        if not resume:
            raise V3ContractError(
                "completed gateway-restart reconciliation left run flow uncommitted"
            )
        flow.update(deepcopy(expected_flow_transition))
        atomic_json(flow_path, flow)
    elif flow_sha != target["flow"]:
        raise V3ContractError("run flow changed outside the prepared transaction")

    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    pair_sha = _file_record(pair_path)["sha256"]
    if pair_sha == source["pair"]["sha256"]:
        if not resume:
            raise V3ContractError(
                "completed gateway-restart reconciliation left pair state uncommitted"
            )
        pair.update(deepcopy(expected_pair_transition))
        atomic_json(pair_path, pair)
    elif pair_sha != target["pair"]:
        raise V3ContractError("pair state changed outside the prepared transaction")

    protected = _restart_protected_artifacts(pair_dir, run_dir, other_run_dir)
    if protected != source.get("protected_artifacts"):
        raise V3ContractError("protected evidence changed during gateway-restart reconciliation")
    final_files = {
        name: _jsonl_record(rows[name]) if name == "ledger" else _file_record(path)
        for name, path in paths.items()
    }
    final = {
        "files": final_files,
        "flow": _file_record(flow_path),
        "pair": _file_record(pair_path),
        "protected_artifacts": protected,
    }
    if any(final["files"][name]["sha256"] != target["files"][name] for name in paths):
        raise V3ContractError("gateway-restart transaction did not persist its exact ledgers")
    if final["flow"]["sha256"] != target["flow"] or final["pair"]["sha256"] != target["pair"]:
        raise V3ContractError("gateway-restart transaction did not persist its exact terminal state")

    if not resume:
        if receipt.get("status") != "reconciled_failed_contract" or receipt.get("final") != final:
            raise V3ContractError(
                "completed gateway-restart reconciliation receipt was modified"
            )
        return receipt
    completed_receipt = deepcopy(receipt)
    completed_receipt["status"] = "reconciled_failed_contract"
    completed_receipt["final"] = final
    atomic_json(receipt_path, completed_receipt)
    return completed_receipt


def reconcile_openclaw_v3_gateway_restart(
    pair_dir: Path,
    arm: str,
    trajectory_path: Path,
    session_log_path: Path,
    interrupted_run_id: str,
    completed_run_id: str,
) -> dict[str, Any]:
    """Terminally preserve one v3 repair interrupted and auto-continued by OpenClaw.

    A gateway restart can kill the benchmark process after the write-ahead
    ``started`` row while OpenClaw later continues the same session as a new
    provider run.  That continuation is not the one frozen repair call, so it
    can never be accepted into the simulator.  It still consumed model usage
    and therefore must be imported as terminal evidence instead of retried or
    discarded.
    """
    _require_official_lock()
    pair_dir = pair_dir.resolve()
    trajectory_path = trajectory_path.resolve()
    session_log_path = session_log_path.resolve()
    if arm not in ("control", "theatre"):
        raise V3ContractError("arm must be control or theatre")
    if not interrupted_run_id or not completed_run_id or interrupted_run_id == completed_run_id:
        raise V3ContractError("interrupted and completed gateway run ids must be distinct")

    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    if pair.get("protocol_version") != "v3" or pair.get("official") is not True:
        raise V3ContractError("gateway-restart reconciliation requires an official v3 pair")
    run_dir = Path(pair[f"{arm}_run"]).resolve()
    other_arm = "theatre" if arm == "control" else "control"
    other_run_dir = Path(pair[f"{other_arm}_run"]).resolve()
    manifest = read_json(run_dir / "manifest.json")
    flow_path = run_dir / "flow.json"
    flow = read_json(flow_path)
    pending = flow.get("pending_invocation")
    if manifest.get("protocol_version") != "v3" or manifest.get("official") is not True:
        raise V3ContractError("selected run is not an official v3 run")
    if not isinstance(pending, dict):
        raise V3ContractError("selected run has no pending v3 repair")
    role = pending.get("role")
    allowed_roles = {"control"} if arm == "control" else {"critic", "consciousness", "planner", "actor"}
    if role not in allowed_roles or flow.get("current_step") != "model_roles":
        raise V3ContractError("selected run is not stopped at the expected pending repair")

    receipt_path = run_dir / "gateway-restart-reconciliation.json"
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        expected = {
            "interrupted_gateway_run_id": interrupted_run_id,
            "completed_gateway_run_id": completed_run_id,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise V3ContractError("existing gateway-restart receipt belongs to different run ids")
        if receipt.get("source", {}).get("trajectory_sha256") != hashlib.sha256(trajectory_path.read_bytes()).hexdigest():
            raise V3ContractError("trajectory bytes differ from the existing reconciliation receipt")
        if receipt.get("source", {}).get("session_log_sha256") != hashlib.sha256(session_log_path.read_bytes()).hexdigest():
            raise V3ContractError("session-log bytes differ from the existing reconciliation receipt")
        if receipt.get("status") == "prepared_gateway_restart_reconciliation":
            return _finish_v3_restart_reconciliation(
                pair_dir, run_dir, other_run_dir, receipt_path, receipt
            )
        if receipt.get("status") != "reconciled_failed_contract":
            raise V3ContractError("existing gateway-restart receipt has an invalid status")
        return _finish_v3_restart_reconciliation(
            pair_dir, run_dir, other_run_dir, receipt_path, receipt, resume=False
        )

    journal = _read_jsonl(run_dir / "call-journal.jsonl")
    usage = _read_jsonl(run_dir / "usage.jsonl")
    decisions = _read_jsonl(run_dir / "model-decisions.jsonl")
    failures = _read_jsonl(run_dir / "model-failures.jsonl")
    open_attempts: list[dict[str, Any]] = []
    for row in journal:
        if row.get("event") != "started":
            continue
        attempt_id = row.get("attempt_id")
        if sum(item.get("attempt_id") == attempt_id for item in journal) == 1:
            open_attempts.append(row)
    if len(open_attempts) != 1:
        raise V3ContractError("run must contain exactly one incomplete call-journal attempt")
    started = open_attempts[0]
    attempt_id = started.get("attempt_id")
    expected_started = {
        "attempt_kind": "repair",
        "role": role,
        "turn_index": pending.get("turn_index"),
        "state_hash": pending.get("state_hash"),
        "original_response_hash": pending.get("original_response_hash"),
    }
    if any(started.get(key) != value for key, value in expected_started.items()):
        raise V3ContractError("incomplete journal row does not identify the pending repair")
    if any(row.get("attempt_id") == attempt_id for row in [*usage, *decisions, *failures]):
        raise V3ContractError("incomplete repair already has usage or decision/failure evidence")
    if started != journal[-1]:
        raise V3ContractError("incomplete repair is not the final call-journal row")
    control_state = read_json(Path(pair["control_run"]) / "state.json")
    theatre_state = read_json(Path(pair["theatre_run"]) / "state.json")
    control_done = bool(control_state.get("terminated"))
    theatre_done = bool(theatre_state.get("terminated"))
    if control_done and theatre_done:
        selected_arm = None
    elif control_done:
        selected_arm = "theatre"
    elif theatre_done:
        selected_arm = "control"
    elif int(control_state["day"]) < int(theatre_state["day"]):
        selected_arm = "control"
    elif int(theatre_state["day"]) < int(control_state["day"]):
        selected_arm = "theatre"
    else:
        selected_arm = pair.get("next_arm")
    if pair.get("status") not in ("ready", "running", "paused_quota") or selected_arm != arm:
        raise V3ContractError("pair is not stopped at the selected arm")
    state = read_json(run_dir / "state.json")
    if (
        started.get("state_hash") != stable_hash(state)
        or started.get("state_hash") != flow.get("turn_state_hash")
        or started.get("turn_index") != flow.get("turn_index")
    ):
        raise V3ContractError("incomplete repair does not match the active turn/state")
    try:
        serial = int(str(attempt_id).rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise V3ContractError("incomplete repair attempt id lacks its durable serial") from exc
    if serial != flow.get("provider_attempt_serial"):
        raise V3ContractError("incomplete repair is not the latest provider serial")

    from .verify import verify_pair

    expected_error = f"{arm}: call journal attempt {attempt_id} is incomplete or reordered"
    verification = verify_pair(pair_dir)
    if verification.get("errors") != [expected_error]:
        raise V3ContractError(
            "pair has integrity errors beyond the selected incomplete repair: "
            + "; ".join(verification.get("errors", []))
        )

    trajectory_rows = _read_jsonl(trajectory_path)
    interrupted = [row for row in trajectory_rows if row.get("runId") == interrupted_run_id]
    completed = [row for row in trajectory_rows if row.get("runId") == completed_run_id]
    if [row.get("type") for row in interrupted] != ["session.started", "context.compiled", "prompt.submitted"]:
        raise V3ContractError("interrupted gateway run is not the exact start/context/prompt-only shape")
    if [row.get("type") for row in completed] != [
        "session.started", "context.compiled", "prompt.submitted", "model.completed", "session.ended"
    ]:
        raise V3ContractError("completed gateway continuation has an unexpected event shape")
    trace_ids = {str(row.get("traceId", "")) for row in [*interrupted, *completed]}
    if len(trace_ids) != 1 or "" in trace_ids:
        raise V3ContractError("gateway runs do not share one explicit OpenClaw trace")
    trace_id = next(iter(trace_ids))
    safe_run = str(manifest["run_id"]).lower().replace("_", "-").replace(":", "-")
    expected_session_key = f"agent:{manifest['agent_id']}:bench-{safe_run}-{role}"
    for row in [*interrupted, *completed]:
        if row.get("sessionId") != trace_id:
            raise V3ContractError("gateway event session id differs from the shared OpenClaw trace")
        if row.get("sessionKey") != expected_session_key:
            raise V3ContractError("gateway event session key differs from the pending run/role")
        if row.get("provider") != "openai" or row.get("modelId") != str(manifest["model"]).split("/", 1)[-1]:
            raise V3ContractError("gateway event provider/model differs from the frozen manifest")

    model_event = completed[3]
    end_data = completed[-1].get("data")
    if not isinstance(end_data, dict) or end_data.get("status") not in ("completed", "success"):
        raise V3ContractError("gateway continuation does not end successfully")
    if any(end_data.get(key) not in (False, None) for key in (
        "timedOut", "aborted", "yieldDetected", "promptError", "terminalError"
    )):
        raise V3ContractError("gateway continuation session.ended reports failure")

    data = model_event.get("data")
    if not isinstance(data, dict) or any(data.get(key) not in (False, None) for key in (
        "timedOut", "aborted", "yieldDetected", "promptError", "terminalError"
    )):
        raise V3ContractError("gateway continuation is not one completed provider response")
    texts = data.get("assistantTexts")
    if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
        raise V3ContractError("gateway continuation must contain exactly one assistant text")
    raw_text = texts[0]

    session_rows = _read_jsonl(session_log_path)
    if not session_rows or session_rows[0].get("type") != "session" or session_rows[0].get("id") != trace_id:
        raise V3ContractError("session log does not identify the selected OpenClaw trace")
    expected_repair = _v3_repair_message(
        run_dir,
        str(role),
        pending["original"],
        pending["original_validation_errors"],
        pending["original_message"],
    )

    def trajectory_prompt_matches(row: dict[str, Any]) -> bool:
        data = row.get("data")
        observed = data.get("prompt") if isinstance(data, dict) else None
        if observed == expected_repair:
            return True
        # OpenClaw trajectory rows cap long prompt fields at 20,000 content
        # characters plus one explicit ellipsis. The full session-log message
        # is independently required below, so only this exact prefix form is
        # accepted as a lossy trajectory representation.
        return (
            isinstance(observed, str)
            and len(observed) == 20_001
            and observed.endswith("…")
            and len(expected_repair) > 20_000
            and observed[:-1] == expected_repair[:20_000]
        )

    if any(not trajectory_prompt_matches(row) for row in interrupted[1:]):
        raise V3ContractError("interrupted trajectory does not contain the exact frozen repair prompt")
    messages = [row for row in session_rows if row.get("type") == "message"]
    matched = []
    for index in range(len(messages) - 2):
        first, second, third = messages[index:index + 3]
        if (
            first.get("message", {}).get("role") == "user"
            and _message_text(first) == expected_repair
            and second.get("message", {}).get("role") == "user"
            and (_message_text(second) or "").startswith("[System] Your previous turn was interrupted by a gateway restart")
            and third.get("message", {}).get("role") == "assistant"
            and _message_text(third) == raw_text
        ):
            matched.append((first, second, third))
    if len(matched) != 1:
        raise V3ContractError("session log does not contain one exact repair→restart→response chain")
    repair_message_row, restart_message_row, response_message_row = matched[0]

    try:
        content = parse_json_object(raw_text)
    except ModelTransportError as exc:
        content = None
        parse_error: str | None = str(exc)
        response_hash = stable_hash(raw_text)
    else:
        parse_error = None
        response_hash = stable_hash(content)

    raw_usage = data.get("usage")
    if not isinstance(raw_usage, dict):
        raise V3ContractError("gateway continuation is missing provider usage")
    usage_fields = {
        "input": raw_usage.get("input", 0),
        "cache_read": raw_usage.get("cacheRead", 0),
        "cache_write": raw_usage.get("cacheWrite", 0),
        "output": raw_usage.get("output", 0),
        "total": raw_usage.get("total", 0),
    }
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in usage_fields.values()):
        raise V3ContractError("gateway continuation provider usage contains an invalid token count")
    provider_usage = {key: int(value) for key, value in usage_fields.items()}
    if provider_usage["total"] != sum(provider_usage[key] for key in ("input", "cache_read", "cache_write", "output")):
        raise V3ContractError("gateway continuation provider usage total is inconsistent")
    try:
        continuation_started = datetime.fromisoformat(str(completed[0].get("ts", "")).replace("Z", "+00:00"))
        continuation_completed = datetime.fromisoformat(str(model_event.get("ts", "")).replace("Z", "+00:00"))
        interrupted_started = datetime.fromisoformat(str(interrupted[0].get("ts", "")).replace("Z", "+00:00"))
        journal_started = datetime.fromisoformat(str(started.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3ContractError("gateway restart trajectory contains an invalid timestamp") from exc
    if any(value.utcoffset() is None for value in (
        continuation_started, continuation_completed, interrupted_started, journal_started
    )):
        raise V3ContractError("gateway restart timestamps must include an explicit timezone")
    duration_ms = int((continuation_completed - continuation_started).total_seconds() * 1000)
    total_elapsed_ms = int((continuation_completed - interrupted_started).total_seconds() * 1000)
    if duration_ms < 0 or total_elapsed_ms < 0 or interrupted_started < journal_started:
        raise V3ContractError("gateway completion predates the interrupted repair")
    timestamp = str(model_event["ts"])
    event_sha256 = hashlib.sha256(_canonical(model_event).encode()).hexdigest()
    usage_row = {
        "timestamp": timestamp,
        "run_id": manifest["run_id"],
        "arm": arm,
        "seed": manifest["seed"],
        "role": role,
        "gateway_run_id": completed_run_id,
        "interrupted_gateway_run_id": interrupted_run_id,
        "session_id": str(model_event.get("sessionId", "")),
        "provider": "openai",
        "model": model_event["modelId"],
        "duration_ms": duration_ms,
        "usage": provider_usage,
        "response_hash": response_hash,
        "outcome": "gateway_restart_recovery_terminal",
        "evidence_source": "openclaw_gateway_restart_reconciliation",
        "attempt_id": attempt_id,
        "attempt_kind": "repair",
        "turn_index": pending["turn_index"],
        "state_hash": pending["state_hash"],
        "original_response_hash": pending["original_response_hash"],
    }
    if parse_error is not None:
        usage_row["parse_error"] = parse_error
    failure_row = {
        **{key: usage_row[key] for key in (
            "timestamp", "duration_ms", "turn_index", "role", "gateway_run_id",
            "session_id", "attempt_id", "attempt_kind", "state_hash", "original_response_hash",
            "evidence_source", "interrupted_gateway_run_id",
        )},
        "failure_kind": "gateway_restart_recovery",
        "raw_text": raw_text,
        "response_hash": response_hash,
        "parse_error": parse_error,
        "trajectory_event_sha256": event_sha256,
    }
    ledger_path = Path(manifest["usage_ledger_path"])
    ledger = _read_jsonl(ledger_path)
    journal_terminal_row = {
        **{key: started[key] for key in (
            "attempt_id", "attempt_kind", "turn_index", "state_hash", "original_response_hash", "role"
        )},
        "event": "completed",
        "timestamp": timestamp,
        "outcome": "gateway_restart_recovery_terminal",
        "response_hash": response_hash,
        "gateway_run_id": completed_run_id,
        "interrupted_gateway_run_id": interrupted_run_id,
    }

    reconciled_at = utc_now()
    reason = "gateway restarted during the frozen repair; auto-continuation preserved and charged but not applied"
    flow_transition = {
        "status": "failed_contract",
        "updated_at": reconciled_at,
        "contract_failure": {"phase": role, "message": reason},
    }
    pair_transition = {
        "status": "failed_contract",
        "last_arm": arm,
        "last_result": {
            "status": "failed_contract",
            "reason": reason,
            "run_dir": str(run_dir),
            "provider_usage_charged": provider_usage,
            "simulator_turns_added": 0,
        },
        "updated_at": reconciled_at,
    }
    target_rows = {
        "usage": [*usage, usage_row],
        "failures": [*failures, failure_row],
        "ledger": [*ledger, usage_row],
        "journal": [*journal, journal_terminal_row],
    }
    final_flow = deepcopy(flow)
    final_flow.update(deepcopy(flow_transition))
    final_pair = deepcopy(pair)
    final_pair.update(deepcopy(pair_transition))

    receipt = {
        "schema_version": 1,
        "status": "prepared_gateway_restart_reconciliation",
        "reconciled_at": reconciled_at,
        "pair_id": pair["pair_id"],
        "run_id": manifest["run_id"],
        "arm": arm,
        "turn_index": pending["turn_index"],
        "role": role,
        "attempt_id": attempt_id,
        "interrupted_gateway_run_id": interrupted_run_id,
        "completed_gateway_run_id": completed_run_id,
        "response_hash": response_hash,
        "provider_usage": provider_usage,
        "source": {
            "kind": "openclaw_gateway_restart_chain",
            "trajectory_path": str(trajectory_path),
            "trajectory_sha256": hashlib.sha256(trajectory_path.read_bytes()).hexdigest(),
            "session_log_path": str(session_log_path),
            "session_log_sha256": hashlib.sha256(session_log_path.read_bytes()).hexdigest(),
            "trace_id": trace_id,
            "completed_event_sha256": event_sha256,
            "repair_message_sha256": hashlib.sha256(_canonical(repair_message_row).encode()).hexdigest(),
            "restart_message_sha256": hashlib.sha256(_canonical(restart_message_row).encode()).hexdigest(),
            "response_message_sha256": hashlib.sha256(_canonical(response_message_row).encode()).hexdigest(),
        },
        "continuation_duration_ms": duration_ms,
        "total_elapsed_ms": total_elapsed_ms,
        "simulator_turns_added": 0,
        "accepted_model_decisions_added": 0,
        "provider_usage_rows_added": 1,
        "transaction": {
            "transaction_id": stable_hash({
                "pair_id": pair["pair_id"],
                "run_id": manifest["run_id"],
                "attempt_id": attempt_id,
                "interrupted_gateway_run_id": interrupted_run_id,
                "completed_gateway_run_id": completed_run_id,
                "trajectory_sha256": hashlib.sha256(trajectory_path.read_bytes()).hexdigest(),
                "session_log_sha256": hashlib.sha256(session_log_path.read_bytes()).hexdigest(),
            }),
            "ledger_path": str(ledger_path.resolve()),
            "source": {
                "files": {
                    "usage": _file_record(run_dir / "usage.jsonl"),
                    "failures": _file_record(run_dir / "model-failures.jsonl"),
                    "ledger": _file_record(ledger_path),
                    "journal": _file_record(run_dir / "call-journal.jsonl"),
                },
                "flow": _file_record(flow_path),
                "flow_transition_fields": {
                    key: deepcopy(flow[key])
                    for key in ("status", "updated_at", "contract_failure")
                    if key in flow
                },
                "pair": _file_record(pair_path),
                "pair_transition_fields": {
                    key: deepcopy(pair[key])
                    for key in ("status", "last_arm", "last_result", "updated_at")
                    if key in pair
                },
                "protected_artifacts": _restart_protected_artifacts(
                    pair_dir, run_dir, other_run_dir
                ),
            },
            "target": {
                "files": {
                    name: _jsonl_sha256(rows) for name, rows in target_rows.items()
                },
                "flow": _json_sha256(final_flow),
                "pair": _json_sha256(final_pair),
            },
            "rows": target_rows,
            "flow_transition": flow_transition,
            "pair_transition": pair_transition,
        },
    }
    atomic_json(receipt_path, receipt)
    return _finish_v3_restart_reconciliation(
        pair_dir, run_dir, other_run_dir, receipt_path, receipt
    )


_UNDISPATCHED_REASON = (
    "gateway restart left a write-ahead attempt with no OpenClaw dispatch observed; "
    "terminalized without retry"
)


def _undispatched_protected_artifacts(
    pair_dir: Path, run_dir: Path, other_run_dir: Path
) -> dict[str, dict[str, Any]]:
    protected = _restart_protected_artifacts(pair_dir, run_dir, other_run_dir)
    protected.update({
        "run/usage.jsonl": _file_record(run_dir / "usage.jsonl"),
        "run/model-failures.jsonl": _file_record(run_dir / "model-failures.jsonl"),
    })
    return protected


def _forensic_timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3ContractError(f"{label} contains an invalid timestamp") from exc
    if parsed.utcoffset() is None:
        raise V3ContractError(f"{label} timestamp must include an explicit timezone")
    return parsed


def _provider_usage_from_trajectory(data: dict[str, Any], label: str) -> dict[str, int]:
    raw = data.get("usage")
    if not isinstance(raw, dict):
        raise V3ContractError(f"{label} is missing provider usage")
    fields = {
        "input": raw.get("input", 0),
        "cache_read": raw.get("cacheRead", 0),
        "cache_write": raw.get("cacheWrite", 0),
        "output": raw.get("output", 0),
        "total": raw.get("total", 0),
    }
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in fields.values()
    ):
        raise V3ContractError(f"{label} contains an invalid provider token count")
    usage = {key: int(value) for key, value in fields.items()}
    if usage["total"] != sum(
        usage[key] for key in ("input", "cache_read", "cache_write", "output")
    ):
        raise V3ContractError(f"{label} provider usage total is inconsistent")
    return usage


def _session_usage(row: dict[str, Any], label: str) -> dict[str, int]:
    message = row.get("message")
    raw = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(raw, dict):
        raise V3ContractError(f"{label} is missing assistant usage")
    fields = {
        "input": raw.get("input", 0),
        "cache_read": raw.get("cacheRead", 0),
        "cache_write": raw.get("cacheWrite", 0),
        "output": raw.get("output", 0),
        "total": raw.get("totalTokens", 0),
    }
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in fields.values()
    ):
        raise V3ContractError(f"{label} contains an invalid assistant token count")
    usage = {key: int(value) for key, value in fields.items()}
    if usage["total"] != sum(
        usage[key] for key in ("input", "cache_read", "cache_write", "output")
    ):
        raise V3ContractError(f"{label} assistant usage total is inconsistent")
    return usage


def _trajectory_prompt_matches(expected: str, observed: Any) -> bool:
    if observed == expected:
        return True
    return (
        isinstance(observed, str)
        and len(observed) == 20_001
        and observed.endswith("…")
        and len(expected) > 20_000
        and observed[:-1] == expected[:20_000]
    )


def _ledger_prefix_matches(
    ledger_path: Path, prefix: dict[str, Any], run_id: str
) -> bool:
    if not ledger_path.is_file():
        return prefix.get("sha256") is None and prefix.get("bytes") == 0
    raw = ledger_path.read_bytes()
    try:
        size = int(prefix.get("bytes", -1))
    except (TypeError, ValueError):
        return False
    if size < 0 or len(raw) < size:
        return False
    original = raw[:size]
    expected_hash = prefix.get("sha256")
    if expected_hash is None:
        if original:
            return False
    elif hashlib.sha256(original).hexdigest() != expected_hash:
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


def _finish_v3_undispatched_reconciliation(
    pair_dir: Path,
    run_dir: Path,
    other_run_dir: Path,
    receipt_path: Path,
    receipt: dict[str, Any],
    *,
    resume: bool = True,
) -> dict[str, Any]:
    transaction = receipt.get("transaction")
    if not isinstance(transaction, dict):
        raise V3ContractError("undispatched-attempt receipt lacks its durable transaction")
    source = transaction.get("source", {})
    target = transaction.get("target", {})
    rows = transaction.get("rows", {})
    if set(rows) != {"journal"} or set(source.get("files", {})) != {"journal"}:
        raise V3ContractError("undispatched-attempt transaction has an invalid file set")
    if set(target.get("files", {})) != {"journal"}:
        raise V3ContractError("undispatched-attempt transaction target set is invalid")
    journal_rows = rows.get("journal")
    if not isinstance(journal_rows, list) or not journal_rows:
        raise V3ContractError("undispatched-attempt transaction lacks its terminal journal row")
    source_record = source["files"]["journal"]
    if len(journal_rows) != int(source_record.get("rows", -1)) + 1:
        raise V3ContractError("undispatched-attempt transaction must append one journal row")
    if source_record.get("sha256") is None:
        if journal_rows[:-1]:
            raise V3ContractError("undispatched-attempt journal source absence is inconsistent")
    elif _jsonl_sha256(journal_rows[:-1]) != source_record.get("sha256"):
        raise V3ContractError("undispatched-attempt transaction changed prior journal rows")

    terminal_row = journal_rows[-1]
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
    if terminal_row != expected_terminal:
        raise V3ContractError("undispatched-attempt terminal row exceeds its forensic receipt")
    expected_transaction_id = stable_hash({
        "pair_id": receipt.get("pair_id"),
        "run_id": receipt.get("run_id"),
        "attempt_id": receipt.get("attempt_id"),
        "trajectory_sha256": receipt.get("source", {}).get("trajectory_sha256"),
        "session_log_sha256": receipt.get("source", {}).get("session_log_sha256"),
    })
    if transaction.get("transaction_id") != expected_transaction_id:
        raise V3ContractError("undispatched-attempt transaction id does not bind its evidence")
    if _jsonl_sha256(journal_rows) != target["files"]["journal"]:
        raise V3ContractError("undispatched-attempt journal target hash is invalid")

    journal_path = run_dir / "call-journal.jsonl"
    current_journal = _file_record(journal_path)["sha256"]
    if current_journal == source_record.get("sha256"):
        if not resume:
            raise V3ContractError("completed undispatched-attempt receipt left journal uncommitted")
        _atomic_jsonl(journal_path, journal_rows)
    elif current_journal != target["files"]["journal"]:
        raise V3ContractError("call journal changed outside the prepared undispatched transaction")

    ledger_path = Path(transaction.get("ledger_path", "")).resolve()
    expected_ledger = Path(read_json(run_dir / "manifest.json")["usage_ledger_path"]).resolve()
    if ledger_path != expected_ledger:
        raise V3ContractError("undispatched-attempt transaction targets the wrong usage ledger")
    ledger_prefix = source.get("ledger_prefix")
    if not isinstance(ledger_prefix, dict) or not _ledger_prefix_matches(
        ledger_path, ledger_prefix, str(receipt.get("run_id"))
    ):
        raise V3ContractError("usage ledger changed for the reconciled run after its no-usage boundary")

    expected_flow_transition = {
        "status": "failed_contract",
        "updated_at": receipt.get("reconciled_at"),
        "contract_failure": {
            "phase": receipt.get("role"),
            "message": _UNDISPATCHED_REASON,
        },
    }
    other_arm = "theatre" if receipt.get("arm") == "control" else "control"
    expected_pair_transition = {
        "next_arm": other_arm,
        "status": "failed_contract",
        "last_arm": receipt.get("arm"),
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
        raise V3ContractError("undispatched-attempt transaction exceeds its allowed state transition")

    flow_path = run_dir / "flow.json"
    flow = read_json(flow_path)
    flow_sha = _file_record(flow_path)["sha256"]
    if flow_sha == source["flow"]["sha256"]:
        if not resume:
            raise V3ContractError("completed undispatched-attempt receipt left flow uncommitted")
        flow.update(deepcopy(expected_flow_transition))
        atomic_json(flow_path, flow)
    elif flow_sha != target.get("flow"):
        raise V3ContractError("run flow changed outside the prepared undispatched transaction")

    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    pair_sha = _file_record(pair_path)["sha256"]
    if pair_sha == source["pair"]["sha256"]:
        if not resume:
            raise V3ContractError("completed undispatched-attempt receipt left pair uncommitted")
        pair.update(deepcopy(expected_pair_transition))
        atomic_json(pair_path, pair)
    elif pair_sha != target.get("pair"):
        raise V3ContractError("pair changed outside the prepared undispatched transaction")

    protected = _undispatched_protected_artifacts(pair_dir, run_dir, other_run_dir)
    if protected != source.get("protected_artifacts"):
        raise V3ContractError("protected evidence changed during undispatched reconciliation")
    final = {
        "files": {"journal": _file_record(journal_path)},
        "flow": _file_record(flow_path),
        "pair": _file_record(pair_path),
        "ledger_prefix": ledger_prefix,
        "protected_artifacts": protected,
    }
    if (
        final["files"]["journal"]["sha256"] != target["files"]["journal"]
        or final["flow"]["sha256"] != target.get("flow")
        or final["pair"]["sha256"] != target.get("pair")
    ):
        raise V3ContractError("undispatched-attempt transaction did not persist its exact terminal state")
    if not resume:
        if receipt.get("status") != "reconciled_failed_contract" or receipt.get("final") != final:
            raise V3ContractError("completed undispatched-attempt receipt was modified")
        return receipt
    completed = deepcopy(receipt)
    completed["status"] = "reconciled_failed_contract"
    completed["final"] = final
    atomic_json(receipt_path, completed)
    return completed


def reconcile_openclaw_v3_undispatched_attempt(
    pair_dir: Path,
    arm: str,
    trajectory_path: Path,
    session_log_path: Path,
) -> dict[str, Any]:
    """Terminalize one v3 write-ahead attempt for which no dispatch is observed.

    This transition is deliberately narrower than gateway continuation recovery:
    it accepts only the final original attempt, proves that the complete role
    trajectory and session log end before its journal timestamp, adds no usage,
    decision, failure, invocation, or simulator turn, and never authorizes retry.
    """
    _require_official_lock()
    pair_dir = pair_dir.resolve()
    trajectory_path = trajectory_path.resolve()
    session_log_path = session_log_path.resolve()
    if arm not in ("control", "theatre"):
        raise V3ContractError("arm must be control or theatre")
    if not trajectory_path.is_file() or not session_log_path.is_file():
        raise V3ContractError("undispatched reconciliation requires both immutable source files")

    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    if pair.get("protocol_version") != "v3" or pair.get("official") is not True:
        raise V3ContractError("undispatched reconciliation requires an official v3 pair")
    run_dir = Path(pair[f"{arm}_run"]).resolve()
    other_arm = "theatre" if arm == "control" else "control"
    other_run_dir = Path(pair[f"{other_arm}_run"]).resolve()
    manifest = read_json(run_dir / "manifest.json")
    flow_path = run_dir / "flow.json"
    flow = read_json(flow_path)
    if manifest.get("protocol_version") != "v3" or manifest.get("official") is not True:
        raise V3ContractError("selected run is not an official v3 run")

    receipt_path = run_dir / "undispatched-attempt-reconciliation.json"
    trajectory_sha256 = hashlib.sha256(trajectory_path.read_bytes()).hexdigest()
    session_log_sha256 = hashlib.sha256(session_log_path.read_bytes()).hexdigest()
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        source = receipt.get("source", {})
        if (
            source.get("trajectory_sha256") != trajectory_sha256
            or source.get("session_log_sha256") != session_log_sha256
        ):
            raise V3ContractError("source bytes differ from the existing undispatched receipt")
        if receipt.get("status") == "prepared_undispatched_attempt_reconciliation":
            return _finish_v3_undispatched_reconciliation(
                pair_dir, run_dir, other_run_dir, receipt_path, receipt
            )
        if receipt.get("status") != "reconciled_failed_contract":
            raise V3ContractError("existing undispatched-attempt receipt has an invalid status")
        return _finish_v3_undispatched_reconciliation(
            pair_dir, run_dir, other_run_dir, receipt_path, receipt, resume=False
        )
    if (run_dir / "gateway-restart-reconciliation.json").exists():
        raise V3ContractError("selected run already has a different gateway-restart receipt")

    role = flow.get("phase")
    allowed_roles = (
        {"control"}
        if arm == "control"
        else {"critic", "consciousness", "planner", "actor"}
    )
    if (
        role not in allowed_roles
        or flow.get("current_step") != "model_roles"
        or flow.get("status") != "running"
        or flow.get("pending_invocation") is not None
    ):
        raise V3ContractError("selected run is not stopped at an original v3 role attempt")

    journal = _read_jsonl(run_dir / "call-journal.jsonl")
    usage = _read_jsonl(run_dir / "usage.jsonl")
    decisions = _read_jsonl(run_dir / "model-decisions.jsonl")
    failures = _read_jsonl(run_dir / "model-failures.jsonl")
    open_attempts = [
        row for row in journal
        if row.get("event") == "started"
        and sum(item.get("attempt_id") == row.get("attempt_id") for item in journal) == 1
    ]
    if len(open_attempts) != 1 or open_attempts[0] != journal[-1]:
        raise V3ContractError("run must end in exactly one incomplete call-journal attempt")
    started = open_attempts[0]
    attempt_id = started.get("attempt_id")
    state = read_json(run_dir / "state.json")
    expected_started = {
        "attempt_id": attempt_id,
        "attempt_kind": "original",
        "event": "started",
        "role": role,
        "state_hash": stable_hash(state),
        "timestamp": started.get("timestamp"),
        "turn_index": flow.get("turn_index"),
    }
    if started != expected_started:
        raise V3ContractError("incomplete journal row is not the exact active original attempt")
    if (
        started.get("state_hash") != flow.get("turn_state_hash")
        or any(row.get("attempt_id") == attempt_id for row in [*usage, *decisions, *failures])
    ):
        raise V3ContractError("incomplete attempt has state or provider evidence inconsistent with no dispatch")
    try:
        serial = int(str(attempt_id).rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise V3ContractError("incomplete original attempt id lacks its durable serial") from exc
    if serial != flow.get("provider_attempt_serial"):
        raise V3ContractError("incomplete original attempt is not the latest provider serial")

    control_state = read_json(Path(pair["control_run"]) / "state.json")
    theatre_state = read_json(Path(pair["theatre_run"]) / "state.json")
    if control_state.get("terminated") and theatre_state.get("terminated"):
        selected_arm = None
    elif control_state.get("terminated"):
        selected_arm = "theatre"
    elif theatre_state.get("terminated"):
        selected_arm = "control"
    elif int(control_state["day"]) < int(theatre_state["day"]):
        selected_arm = "control"
    elif int(theatre_state["day"]) < int(control_state["day"]):
        selected_arm = "theatre"
    else:
        selected_arm = pair.get("next_arm")
    if pair.get("status") not in ("ready", "running", "paused_quota") or selected_arm != arm:
        raise V3ContractError("pair is not stopped at the selected arm")

    from .verify import verify_pair

    expected_error = f"{arm}: call journal attempt {attempt_id} is incomplete or reordered"
    verification = verify_pair(pair_dir)
    if verification.get("errors") != [expected_error]:
        raise V3ContractError(
            "pair has integrity errors beyond the selected incomplete attempt: "
            + "; ".join(verification.get("errors", []))
        )

    journal_started = _forensic_timestamp(started.get("timestamp"), "call journal")
    trajectory_rows = _read_jsonl(trajectory_path)
    if not trajectory_rows:
        raise V3ContractError("trajectory contains no completed prior role call")
    safe_run = str(manifest["run_id"]).lower().replace("_", "-").replace(":", "-")
    expected_session_key = f"agent:{manifest['agent_id']}:bench-{safe_run}-{role}"
    trace_ids = {str(row.get("traceId", "")) for row in trajectory_rows}
    if len(trace_ids) != 1 or "" in trace_ids:
        raise V3ContractError("trajectory does not contain one explicit OpenClaw trace")
    trace_id = next(iter(trace_ids))
    expected_model = str(manifest["model"]).split("/", 1)[-1]
    trajectory_times: list[datetime] = []
    ordered_run_ids: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    previous_run_id: str | None = None
    closed_run_ids: set[str] = set()
    for row in trajectory_rows:
        run_id = str(row.get("runId", ""))
        if not run_id:
            raise V3ContractError("trajectory event lacks a gateway run id")
        if run_id != previous_run_id:
            if run_id in closed_run_ids:
                raise V3ContractError("trajectory gateway runs are not contiguous")
            if previous_run_id is not None:
                closed_run_ids.add(previous_run_id)
            ordered_run_ids.append(run_id)
            grouped[run_id] = []
            previous_run_id = run_id
        grouped[run_id].append(row)
        if (
            row.get("traceId") != trace_id
            or row.get("sessionId") != trace_id
            or row.get("sessionKey") != expected_session_key
            or row.get("provider") != "openai"
            or row.get("modelId") != expected_model
        ):
            raise V3ContractError("trajectory identity differs from the selected frozen role session")
        observed_at = _forensic_timestamp(row.get("ts"), "trajectory")
        if observed_at >= journal_started:
            raise V3ContractError("trajectory contains an event at or after the incomplete attempt started")
        trajectory_times.append(observed_at)
    if trajectory_times != sorted(trajectory_times):
        raise V3ContractError("trajectory events are not timestamp ordered")

    model_events: list[dict[str, Any]] = []
    trajectory_usages: list[dict[str, int]] = []
    for run_id in ordered_run_ids:
        events = grouped[run_id]
        if [row.get("type") for row in events] != [
            "session.started", "context.compiled", "prompt.submitted",
            "model.completed", "session.ended",
        ]:
            raise V3ContractError("trajectory contains a gateway run that is not exactly complete")
        data = events[3].get("data")
        end_data = events[4].get("data")
        if not isinstance(data, dict) or any(data.get(key) not in (False, None) for key in (
            "timedOut", "aborted", "yieldDetected", "promptError", "terminalError"
        )):
            raise V3ContractError("trajectory contains a prior provider response with terminal flags")
        if (
            not isinstance(end_data, dict)
            or end_data.get("status") not in ("success", "completed")
            or any(end_data.get(key) not in (False, None) for key in (
                "timedOut", "aborted", "yieldDetected", "promptError", "terminalError"
            ))
        ):
            raise V3ContractError("trajectory contains a prior gateway run without successful closure")
        texts = data.get("assistantTexts")
        if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
            raise V3ContractError("trajectory prior call must contain exactly one assistant text")
        model_events.append(events[3])
        trajectory_usages.append(_provider_usage_from_trajectory(data, f"trajectory run {run_id}"))

    session_rows = _read_jsonl(session_log_path)
    if (
        not session_rows
        or session_rows[0].get("type") != "session"
        or session_rows[0].get("id") != trace_id
    ):
        raise V3ContractError("session log does not identify the complete role trace")
    messages = session_rows[1:]
    if len(messages) != 2 * len(ordered_run_ids) or any(
        row.get("type") != "message" for row in messages
    ):
        raise V3ContractError("session log is not one complete user/assistant pair per gateway run")
    message_times = [
        _forensic_timestamp(row.get("timestamp"), "session log") for row in messages
    ]
    if message_times != sorted(message_times):
        raise V3ContractError("session messages are not timestamp ordered")
    if any(observed_at >= journal_started for observed_at in message_times):
        raise V3ContractError("session log contains a message at or after the incomplete attempt started")
    message_ids: set[str] = set()
    prior_message_id: str | None = None
    for index, row in enumerate(messages):
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id or row_id in message_ids:
            raise V3ContractError("session log contains a missing or duplicate message id")
        message_ids.add(row_id)
        if index > 0 and row.get("parentId") != prior_message_id:
            raise V3ContractError("session message parent chain is incomplete")
        prior_message_id = row_id

    role_usage = [
        row for row in usage
        if row.get("role") == role and row.get("session_id") == trace_id
    ]
    if [row.get("gateway_run_id") for row in role_usage] != ordered_run_ids:
        raise V3ContractError("trajectory gateway runs do not equal prior recorded role usage")
    for index, (run_id, model_event, provider_usage, usage_row) in enumerate(zip(
        ordered_run_ids, model_events, trajectory_usages, role_usage, strict=True
    )):
        user_row, assistant_row = messages[index * 2:index * 2 + 2]
        user_message = user_row.get("message")
        assistant_message = assistant_row.get("message")
        if (
            not isinstance(user_message, dict)
            or user_message.get("role") != "user"
            or not isinstance(assistant_message, dict)
            or assistant_message.get("role") != "assistant"
        ):
            raise V3ContractError("session log does not alternate exact user and assistant messages")
        prompt = _message_text(user_row)
        context_data = grouped[run_id][1].get("data")
        submitted_data = grouped[run_id][2].get("data")
        context_prompt = context_data.get("prompt") if isinstance(context_data, dict) else None
        submitted_prompt = submitted_data.get("prompt") if isinstance(submitted_data, dict) else None
        if (
            not isinstance(prompt, str)
            or not _trajectory_prompt_matches(prompt, context_prompt)
            or not _trajectory_prompt_matches(prompt, submitted_prompt)
        ):
            raise V3ContractError("session prompt does not match its trajectory call")
        raw_text = model_event["data"]["assistantTexts"][0]
        if (
            _message_text(assistant_row) != raw_text
            or assistant_message.get("provider") != "openai"
            or assistant_message.get("model") != expected_model
            or assistant_message.get("stopReason") != "stop"
            or _session_usage(assistant_row, f"session response {run_id}") != provider_usage
        ):
            raise V3ContractError("session response does not match its completed trajectory call")
        try:
            content = parse_json_object(raw_text)
        except ModelTransportError:
            response_hash = stable_hash(raw_text)
        else:
            response_hash = stable_hash(content)
        if (
            usage_row.get("run_id") != manifest["run_id"]
            or usage_row.get("arm") != arm
            or usage_row.get("provider") != "openai"
            or usage_row.get("model") != expected_model
            or usage_row.get("usage") != provider_usage
            or usage_row.get("response_hash") != response_hash
        ):
            raise V3ContractError("prior recorded usage does not match the complete session trace")

    reconciled_at = utc_now()
    if _forensic_timestamp(reconciled_at, "reconciliation") < journal_started:
        raise V3ContractError("reconciliation timestamp predates the incomplete attempt")
    ledger_path = Path(manifest["usage_ledger_path"]).resolve()
    journal_terminal_row = {
        "attempt_id": attempt_id,
        "attempt_kind": "original",
        "turn_index": flow["turn_index"],
        "state_hash": started["state_hash"],
        "role": role,
        "event": "transport_failed",
        "timestamp": reconciled_at,
        "error": _UNDISPATCHED_REASON,
        "evidence_source": "openclaw_undispatched_attempt_reconciliation",
        "trajectory_sha256": trajectory_sha256,
        "session_log_sha256": session_log_sha256,
    }
    flow_transition = {
        "status": "failed_contract",
        "updated_at": reconciled_at,
        "contract_failure": {"phase": role, "message": _UNDISPATCHED_REASON},
    }
    pair_transition = {
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
        "updated_at": reconciled_at,
    }
    target_journal = [*journal, journal_terminal_row]
    final_flow = deepcopy(flow)
    final_flow.update(deepcopy(flow_transition))
    final_pair = deepcopy(pair)
    final_pair.update(deepcopy(pair_transition))
    source = {
        "kind": "openclaw_no_dispatch_boundary",
        "trajectory_path": str(trajectory_path),
        "trajectory_sha256": trajectory_sha256,
        "trajectory_rows": len(trajectory_rows),
        "trajectory_last_timestamp": str(trajectory_rows[-1]["ts"]),
        "trajectory_last_event_sha256": hashlib.sha256(
            _canonical(trajectory_rows[-1]).encode()
        ).hexdigest(),
        "session_log_path": str(session_log_path),
        "session_log_sha256": session_log_sha256,
        "session_log_rows": len(session_rows),
        "session_last_timestamp": str(messages[-1]["timestamp"]),
        "session_last_message_sha256": hashlib.sha256(
            _canonical(messages[-1]).encode()
        ).hexdigest(),
        "trace_id": trace_id,
        "last_completed_gateway_run_id": ordered_run_ids[-1],
        "journal_started_at": str(started["timestamp"]),
        "trajectory_events_at_or_after_start": 0,
        "session_messages_at_or_after_start": 0,
    }
    receipt = {
        "schema_version": 1,
        "status": "prepared_undispatched_attempt_reconciliation",
        "reconciled_at": reconciled_at,
        "pair_id": pair["pair_id"],
        "run_id": manifest["run_id"],
        "arm": arm,
        "turn_index": flow["turn_index"],
        "role": role,
        "state_hash": started["state_hash"],
        "attempt_id": attempt_id,
        "attempt_kind": "original",
        "source": source,
        "provider_calls_added": 0,
        "provider_usage_rows_added": 0,
        "model_failures_added": 0,
        "accepted_model_decisions_added": 0,
        "role_invocations_added": 0,
        "simulator_turns_added": 0,
        "transaction": {
            "transaction_id": stable_hash({
                "pair_id": pair["pair_id"],
                "run_id": manifest["run_id"],
                "attempt_id": attempt_id,
                "trajectory_sha256": trajectory_sha256,
                "session_log_sha256": session_log_sha256,
            }),
            "ledger_path": str(ledger_path),
            "source": {
                "files": {"journal": _file_record(run_dir / "call-journal.jsonl")},
                "ledger_prefix": _file_record(ledger_path),
                "flow": _file_record(flow_path),
                "flow_transition_fields": {
                    key: deepcopy(flow[key])
                    for key in ("status", "updated_at", "contract_failure")
                    if key in flow
                },
                "pair": _file_record(pair_path),
                "pair_transition_fields": {
                    key: deepcopy(pair[key])
                    for key in ("next_arm", "status", "last_arm", "last_result", "updated_at")
                    if key in pair
                },
                "protected_artifacts": _undispatched_protected_artifacts(
                    pair_dir, run_dir, other_run_dir
                ),
            },
            "target": {
                "files": {"journal": _jsonl_sha256(target_journal)},
                "flow": _json_sha256(final_flow),
                "pair": _json_sha256(final_pair),
            },
            "rows": {"journal": target_journal},
            "flow_transition": flow_transition,
            "pair_transition": pair_transition,
        },
    }
    atomic_json(receipt_path, receipt)
    return _finish_v3_undispatched_reconciliation(
        pair_dir, run_dir, other_run_dir, receipt_path, receipt
    )
