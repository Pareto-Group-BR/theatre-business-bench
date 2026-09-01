from __future__ import annotations

import hashlib
import json
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
        return receipt

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
    if completed[-1].get("data", {}).get("status") != "completed":
        raise V3ContractError("gateway continuation does not end as completed")
    data = model_event.get("data")
    if not isinstance(data, dict) or any(data.get(key) not in (False, None) for key in ("timedOut", "aborted", "promptError")):
        raise V3ContractError("gateway continuation is not one completed provider response")
    texts = data.get("assistantTexts")
    if not isinstance(texts, list) or len(texts) != 1 or not isinstance(texts[0], str):
        raise V3ContractError("gateway continuation must contain exactly one assistant text")
    raw_text = texts[0]
    try:
        content = parse_json_object(raw_text)
    except ModelTransportError as exc:
        content = None
        parse_error: str | None = str(exc)
        response_hash = stable_hash(raw_text)
    else:
        parse_error = None
        response_hash = stable_hash(content)

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
    _atomic_jsonl(run_dir / "usage.jsonl", [*usage, usage_row])
    _atomic_jsonl(run_dir / "model-failures.jsonl", [*failures, failure_row])
    _atomic_jsonl(ledger_path, [*ledger, usage_row])
    _atomic_jsonl(run_dir / "call-journal.jsonl", [*journal, {
        **{key: started[key] for key in (
            "attempt_id", "attempt_kind", "turn_index", "state_hash", "original_response_hash", "role"
        )},
        "event": "completed",
        "timestamp": timestamp,
        "outcome": "gateway_restart_recovery_terminal",
        "response_hash": response_hash,
        "gateway_run_id": completed_run_id,
        "interrupted_gateway_run_id": interrupted_run_id,
    }])

    reconciled_at = utc_now()
    reason = "gateway restarted during the frozen repair; auto-continuation preserved and charged but not applied"
    flow["status"] = "failed_contract"
    flow["updated_at"] = reconciled_at
    flow["contract_failure"] = {"phase": role, "message": reason}
    atomic_json(flow_path, flow)
    pair["status"] = "failed_contract"
    pair["last_arm"] = arm
    pair["last_result"] = {
        "status": "failed_contract",
        "reason": reason,
        "run_dir": str(run_dir),
        "provider_usage_charged": provider_usage,
        "simulator_turns_added": 0,
    }
    pair["updated_at"] = reconciled_at
    atomic_json(pair_path, pair)

    repair_message_row, restart_message_row, response_message_row = matched[0]
    receipt = {
        "schema_version": 1,
        "status": "reconciled_failed_contract",
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
    }
    atomic_json(receipt_path, receipt)
    return receipt
