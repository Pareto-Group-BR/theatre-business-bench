from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .runner import atomic_json, read_json, utc_now
from .simulator import stable_hash
from .transport import ModelTransportError, parse_json_object
from .v2 import V2ContractError


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
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


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
