from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .runner import PROMPT_FILES, V2_PROMPT_FILES, read_json
from .simulator import VendingSimulator, stable_hash
from .v2 import V2ContractError, audit_v2_bundle, build_v2_bundle, file_sha256


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


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

    if run_dir.name != run_id:
        errors.append(f"{expected_arm}: run directory does not match manifest run_id")
    if manifest.get("arm") != expected_arm:
        errors.append(f"{expected_arm}: manifest arm mismatch")
    if manifest.get("seed") != state.get("seed"):
        errors.append(f"{expected_arm}: manifest/state seed mismatch")
    if stable_hash(scenario) != manifest.get("scenario_hash"):
        errors.append(f"{expected_arm}: scenario hash mismatch")

    protocol = manifest.get("protocol_version", "v1")
    prompt_files = V2_PROMPT_FILES if protocol == "v2" else PROMPT_FILES
    prompt_hashes = manifest.get("prompt_hashes", {})
    for role in prompt_files:
        snapshot = run_dir / f"prompt-{role}.md"
        if not snapshot.is_file():
            errors.append(f"{expected_arm}: missing frozen prompt for {role}")
        elif stable_hash(snapshot.read_text(encoding="utf-8")) != prompt_hashes.get(role):
            errors.append(f"{expected_arm}: frozen prompt hash mismatch for {role}")
    if protocol == "v2":
        contract_path = run_dir / "functional-contract.json"
        knowledge_path = run_dir / "knowledge.md"
        if not contract_path.is_file() or stable_hash(read_json(contract_path)) != manifest.get("functional_contract_hash"):
            errors.append(f"{expected_arm}: frozen functional contract hash mismatch")
        if not knowledge_path.is_file() or stable_hash(knowledge_path.read_text(encoding="utf-8")) != manifest.get("knowledge_hash"):
            errors.append(f"{expected_arm}: frozen knowledge hash mismatch")
        if manifest.get("action_budget") != scenario.get("max_actions_per_turn"):
            errors.append(f"{expected_arm}: explicit action budget differs from scenario")
        if not scenario.get("expose_action_budget"):
            errors.append(f"{expected_arm}: v2 scenario does not expose the action budget")

    usage = _read_jsonl(run_dir / "usage.jsonl", errors)
    decisions = _read_jsonl(run_dir / "model-decisions.jsonl", errors)
    turns = _read_jsonl(run_dir / "turns.jsonl", errors)
    if len(usage) != len(decisions):
        errors.append(f"{expected_arm}: usage/decision count mismatch ({len(usage)} != {len(decisions)})")

    for index, decision in enumerate(decisions):
        content = decision.get("content")
        if stable_hash(content) != decision.get("response_hash"):
            errors.append(f"{expected_arm}: decision {index} response hash mismatch")
        if index < len(usage):
            usage_row = usage[index]
            if decision.get("response_hash") != usage_row.get("response_hash"):
                errors.append(f"{expected_arm}: usage/decision response hash mismatch at call {index}")
            if decision.get("role") != usage_row.get("role"):
                errors.append(f"{expected_arm}: usage/decision role mismatch at call {index}")

    expected_model = str(manifest.get("model", "")).split("/", 1)[-1]
    for index, row in enumerate(usage):
        if row.get("run_id") != run_id or row.get("arm") != expected_arm:
            errors.append(f"{expected_arm}: usage identity mismatch at call {index}")
        if row.get("seed") != manifest.get("seed"):
            errors.append(f"{expected_arm}: usage seed mismatch at call {index}")
        if row.get("provider") != "openai" or row.get("model") != expected_model:
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
    for expected_index, turn in enumerate(turns):
        turn_index = turn.get("turn_index")
        if turn_index != expected_index:
            errors.append(f"{expected_arm}: non-contiguous turn index at {expected_index}")
            continue
        view = simulator.public_view()
        if expected_arm == "theatre":
            review = protocol == "v2" or (
                turn_index % int(manifest.get("theatre_review_every_turns", 1)) == 0
                or any(event.get("severity") == "critical" for event in view.get("recent_events", []))
            )
            if review:
                expected_calls.extend(((turn_index, "critic"), (turn_index, "planner")))
                if protocol == "v2":
                    expected_calls.append((turn_index, "consciousness"))
        expected_calls.append((turn_index, business_role))
        candidates = [
            item for item in decisions
            if item.get("turn_index") == turn_index and item.get("role") == business_role
        ]
        if len(candidates) != 1:
            errors.append(f"{expected_arm}: turn {turn_index} has {len(candidates)} business decisions")
            continue
        content = candidates[0].get("content")
        if protocol == "v2" and isinstance(content, dict):
            turn_decisions = {
                item.get("role"): item.get("content")
                for item in decisions
                if item.get("turn_index") == turn_index
            }
            pending = {
                role: turn_decisions.get(role)
                for role in ("critic", "planner", "consciousness")
            }
            try:
                audit = audit_v2_bundle(build_v2_bundle(expected_arm, content, pending), view, simulator)
                actions = audit["actions"]
                if turn.get("decision_audit") != audit:
                    errors.append(f"{expected_arm}: turn {turn_index} decision audit mismatch")
            except V2ContractError as exc:
                errors.append(f"{expected_arm}: turn {turn_index} v2 contract failure: {exc}")
                actions = []
        else:
            actions = content.get("actions", []) if isinstance(content, dict) else []
            if not isinstance(actions, list):
                actions = []
        day_before = simulator.state["day"]
        applied = simulator.apply_turn(actions)
        if turn.get("day_before") != day_before or turn.get("day_after") != simulator.state["day"]:
            errors.append(f"{expected_arm}: turn {turn_index} day boundary mismatch")
        if turn.get("accepted") != applied.accepted or turn.get("rejected") != applied.rejected:
            errors.append(f"{expected_arm}: turn {turn_index} action result mismatch")
        if turn.get("state_hash") != applied.state_hash:
            errors.append(f"{expected_arm}: turn {turn_index} replay hash mismatch")

    current_turn = len(turns)
    current_expected: list[tuple[int, str]] = []
    if not simulator.state["terminated"]:
        if expected_arm == "theatre":
            view = simulator.public_view()
            review = protocol == "v2" or (
                current_turn % int(manifest.get("theatre_review_every_turns", 1)) == 0
                or any(event.get("severity") == "critical" for event in view.get("recent_events", []))
            )
            if review:
                current_expected.extend(((current_turn, "critic"), (current_turn, "planner")))
                if protocol == "v2":
                    current_expected.append((current_turn, "consciousness"))
        current_expected.append((current_turn, business_role))
    actual_calls = [(item.get("turn_index"), item.get("role")) for item in decisions]
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
    return {
        "run_id": run_id,
        "arm": expected_arm,
        "day": state.get("day"),
        "turns": len(turns),
        "model_calls": len(usage),
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
        for field in (
            "seed", "model", "thinking", "scenario_hash", "decision_period_days",
            "protocol_version", "functional_contract_hash", "knowledge_hash",
            "shared_functions", "action_budget", "usage_ledger_path",
        ):
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
        if pair.get("protocol_version", "v1") != manifests["control"].get("protocol_version", "v1"):
            errors.append("pair: protocol differs from run manifests")
        if pair.get("protocol_version") == "v2":
            prereg_path = pair_dir / "preregistration.json"
            prereg: dict[str, Any] = {}
            if not prereg_path.is_file():
                errors.append("pair: frozen v2 preregistration snapshot is missing")
            else:
                prereg = read_json(prereg_path)
                if file_sha256(prereg_path) != pair.get("preregistration_sha256"):
                    errors.append("pair: frozen v2 preregistration hash mismatch")
                matching = [
                    row for row in prereg.get("paired_seeds", [])
                    if isinstance(row, dict) and row.get("seed") == pair.get("seed")
                ]
                if len(matching) != 1 or matching[0].get("first_arm") != pair.get("first_arm"):
                    errors.append("pair: seed is not uniquely present in frozen preregistration")
            if pair.get("inference_enabled"):
                activation = pair_dir / "activation.json"
                if not activation.is_file():
                    errors.append("pair: activated v2 pair is missing activation.json")
                elif read_json(activation).get("preregistration_sha256") != pair.get("preregistration_sha256"):
                    errors.append("pair: activation preregistration hash mismatch")
            for arm in ("control", "theatre"):
                manifest = manifests[arm]
                if bool(manifest.get("inference_enabled")) != bool(pair.get("inference_enabled")):
                    errors.append(f"pair: {arm} inference gate differs from pair")
                for field in ("source_commit", "preregistration_sha256", "activation_receipt_sha256"):
                    if manifest.get(field) != pair.get(field):
                        errors.append(f"pair: {arm} activation field mismatch for {field}")
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
