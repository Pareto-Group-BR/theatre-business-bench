from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .v2 import ALLOWED_ACTIONS, RESPONSIBILITIES, _published_source_identity


ROOT = Path(__file__).resolve().parents[2]
PREREGISTRATION = ROOT / "preregistration" / "v3.json"
ROLES = ("control", "critic", "consciousness", "planner", "actor")
TIMING_CLASSES = {"now", "conditional_future"}


class V3ContractError(ValueError):
    """The frozen v3 protocol, timing gate, or bounded repair failed closed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def audit_preregistration(path: Path = PREREGISTRATION) -> dict[str, Any]:
    errors: list[str] = []
    try:
        prereg = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"schema_version": 1, "status": "failed", "errors": [str(exc)]}

    if prereg.get("schema") != "theatre-business-bench-preregistration/v3":
        errors.append("schema must be theatre-business-bench-preregistration/v3")
    if prereg.get("run_status") != "not_started":
        errors.append("run_status must remain not_started before first inference")
    if prereg.get("frozen") is not True:
        errors.append("pre-registration must be frozen")

    design = prereg.get("design", {})
    seeds = design.get("paired_seeds", []) if isinstance(design, dict) else []
    if len(seeds) != 5 or len(set(seeds)) != 5 or not all(isinstance(seed, int) for seed in seeds):
        errors.append("design.paired_seeds must contain five unique integers")
    if set(seeds) & {2201, 2202, 2203, 2204, 2205}:
        errors.append("v3 seeds must not reuse immutable v2 seeds")
    arm_order = design.get("arm_order", {}) if isinstance(design, dict) else {}
    if set(map(str, seeds)) != set(arm_order):
        errors.append("arm_order must pre-register every seed exactly once")
    if any(value not in ("control_first", "theatre_first") for value in arm_order.values()):
        errors.append("arm_order values must be control_first or theatre_first")
    for key in ("human_intervention", "live_internet", "opponent_information", "v1_v2_results_in_role_context"):
        if design.get(key) != "forbidden":
            errors.append(f"design.{key} must be forbidden")
    if design.get("max_actions_per_turn") != 14:
        errors.append("max_actions_per_turn must be explicitly frozen at 14")

    parity = prereg.get("functional_parity", {})
    control = set(parity.get("control_responsibilities", [])) if isinstance(parity, dict) else set()
    theatre_by_role = parity.get("theatre_responsibilities", {}) if isinstance(parity, dict) else {}
    theatre = {
        responsibility
        for values in theatre_by_role.values()
        if isinstance(values, list)
        for responsibility in values
    } if isinstance(theatre_by_role, dict) else set()
    if control != RESPONSIBILITIES or theatre != RESPONSIBILITIES:
        errors.append("control and Theatre must expose the same four frozen responsibilities")

    gates = prereg.get("execution_gates", {})
    if set(gates.get("timing_classes", [])) != TIMING_CLASSES:
        errors.append("execution_gates must freeze exactly now and conditional_future")
    for key in (
        "conditional_future_requires_observable_precondition",
        "conditional_future_forbidden_in_current_execution",
        "all_immediate_correction_items_executed_same_handoff",
        "required_action_types_covered_across_timing_classes",
        "plan_action_binding_required",
        "actor_adherence_system_validated",
        "action_capacity_system_validated",
    ):
        if gates.get(key) is not True:
            errors.append(f"execution_gates.{key} must be true")

    repair = prereg.get("repair_policy", {})
    expected_repair = {
        "eligible_failure": "json_parseable_structural_contract_failure_only",
        "max_repairs_per_role_invocation": 1,
        "same_role_turn_and_state_required": True,
        "original_and_repair_calls_preserved": True,
        "original_and_repair_tokens_charged": True,
        "simulator_transition_between_attempts": "forbidden",
        "parse_transport_quota_failures_repairable": False,
        "second_failure": "failed_contract",
    }
    for key, expected in expected_repair.items():
        if repair.get(key) != expected:
            errors.append(f"repair_policy.{key} must equal {expected!r}")

    artifacts = prereg.get("artifacts", {})
    observed_hashes: dict[str, str] = {}
    required_keys = {
        "scenario", "shared_corpus", "protocol", "prompt_repair",
        *(f"prompt_{role}" for role in ROLES),
    }
    if not isinstance(artifacts, dict) or set(artifacts) != required_keys:
        errors.append("artifacts must contain scenario, corpus, protocol, six prompts, and nothing else")
    else:
        for key, descriptor in artifacts.items():
            if not isinstance(descriptor, dict):
                errors.append(f"artifact {key} must be an object")
                continue
            relative, expected = descriptor.get("path"), descriptor.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                errors.append(f"artifact {key} must contain path and sha256 strings")
                continue
            candidate = (ROOT / relative).resolve()
            try:
                candidate.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"artifact {key} escapes repository root")
                continue
            if not candidate.is_file():
                errors.append(f"artifact {key} is missing: {relative}")
                continue
            observed = _sha256(candidate)
            observed_hashes[key] = observed
            if observed != expected:
                errors.append(f"artifact {key} SHA-256 mismatch")

    return {
        "schema_version": 1,
        "status": "passed" if not errors else "failed",
        "preregistration": str(path),
        "run_status": prereg.get("run_status"),
        "paired_seeds": seeds,
        "observed_hashes": observed_hashes,
        "errors": errors,
    }


def seed_plan(seed: int) -> dict[str, Any]:
    """Return the exact frozen execution plan for one registered v3 seed."""
    audit = audit_preregistration()
    if audit["status"] != "passed":
        raise V3ContractError("v3 preregistration is invalid: " + "; ".join(audit["errors"]))
    prereg = _read_json(PREREGISTRATION)
    seeds = prereg["design"]["paired_seeds"]
    if seed not in seeds:
        raise V3ContractError(f"seed {seed} is not preregistered for v3")
    design = prereg["design"]
    raw_order = design["arm_order"][str(seed)]
    return {
        "seed": seed,
        "first_arm": raw_order.removesuffix("_first"),
        "days": design["days"],
        "model": design["model"],
        "thinking": design["thinking"],
    }


def activate_v3_pair(pair_dir: Path, source_commit: str) -> dict[str, Any]:
    """Atomically bind an untouched offline v3 pair to published main."""
    from .runner import atomic_json, read_json
    from .verify import verify_pair

    _published_source_identity(source_commit)
    audit = audit_preregistration()
    if audit["status"] != "passed":
        raise V3ContractError("v3 preregistration is invalid: " + "; ".join(audit["errors"]))

    pair_dir = pair_dir.resolve()
    pair = read_json(pair_dir / "pair.json")
    if pair.get("protocol_version") != "v3" or pair.get("status") != "ready":
        raise V3ContractError("activation requires a ready v3 pair")
    planned = seed_plan(int(pair.get("seed")))
    if pair.get("first_arm") != planned["first_arm"] or pair.get("next_arm") != planned["first_arm"]:
        raise V3ContractError("pair first arm differs from v3 preregistration")
    if pair.get("inference_enabled"):
        raise V3ContractError("pair is already activated")

    prereg_snapshot = pair_dir / "preregistration.json"
    if not prereg_snapshot.is_file():
        raise V3ContractError("pair is missing its frozen v3 preregistration snapshot")
    prereg_hash = _sha256(prereg_snapshot)
    if prereg_hash != pair.get("preregistration_sha256") or prereg_hash != _sha256(PREREGISTRATION):
        raise V3ContractError("pair preregistration snapshot differs from published v3 source")
    if pair.get("artifact_hashes") != audit["observed_hashes"]:
        raise V3ContractError("pair frozen artifact hashes differ from published v3 source")

    integrity = verify_pair(pair_dir)
    if integrity["status"] != "passed":
        raise V3ContractError("pair integrity failed before activation: " + "; ".join(integrity["errors"]))

    evidence_names = (
        "usage.jsonl",
        "model-decisions.jsonl",
        "model-failures.jsonl",
        "role-invocations.jsonl",
        "call-journal.jsonl",
        "turns.jsonl",
        "result.json",
    )
    run_dirs: list[Path] = []
    for arm in ("control", "theatre"):
        run_dir = Path(pair[f"{arm}_run"])
        run_dirs.append(run_dir)
        for ledger_name in evidence_names:
            if (run_dir / ledger_name).exists():
                raise V3ContractError(f"activation refuses existing inference evidence: {arm}/{ledger_name}")
        if read_json(run_dir / "state.json").get("day") != 0:
            raise V3ContractError(f"activation refuses non-zero {arm} state")

    receipt = {
        "schema_version": 1,
        "protocol": "v3",
        "official": True,
        "pair_id": pair["pair_id"],
        "source_commit": source_commit,
        "preregistration_sha256": prereg_hash,
        "artifact_hashes": audit["observed_hashes"],
        "seed": pair["seed"],
        "first_arm": pair["first_arm"],
    }
    atomic_json(pair_dir / "activation.json", receipt)
    receipt_hash = _sha256(pair_dir / "activation.json")
    for run_dir in run_dirs:
        manifest = read_json(run_dir / "manifest.json")
        manifest.update({
            "inference_enabled": True,
            "official": True,
            "source_commit": source_commit,
            "activation_receipt_sha256": receipt_hash,
        })
        atomic_json(run_dir / "manifest.json", manifest)
    pair.update({
        "inference_enabled": True,
        "official": True,
        "source_commit": source_commit,
        "activation_receipt_sha256": receipt_hash,
    })
    atomic_json(pair_dir / "pair.json", pair)
    return receipt


def _object(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    return value


def _plan_items(queue: Any, label: str, errors: list[str]) -> dict[str, dict[str, str]]:
    if not isinstance(queue, list) or not queue:
        errors.append(f"{label} must be non-empty")
        return {}
    result: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(queue):
        item = _object(raw, f"{label}[{index}]", errors)
        item_id, action_type, timing = item.get("id"), item.get("action_type"), item.get("timing")
        precondition = item.get("precondition")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{label}[{index}].id must be non-empty")
            continue
        if item_id in result:
            errors.append(f"{label}[{index}] duplicates {item_id}")
            continue
        if action_type not in ALLOWED_ACTIONS:
            errors.append(f"{label}[{index}].action_type is unknown")
        if timing not in TIMING_CLASSES:
            errors.append(f"{label}[{index}].timing is invalid")
        if timing == "now" and precondition != "already_satisfied":
            errors.append(f"{label}[{index}] now item must use precondition=already_satisfied")
        if timing == "conditional_future" and (
            not isinstance(precondition, str) or not precondition.strip() or precondition == "already_satisfied"
        ):
            errors.append(f"{label}[{index}] conditional item needs an observable future precondition")
        if action_type in ALLOWED_ACTIONS and timing in TIMING_CLASSES:
            result[item_id] = {"action_type": action_type, "timing": timing}
    return result


def _execution(payload: dict[str, Any], prefix: str, errors: list[str]) -> set[str]:
    queue = payload.get("execution_queue")
    capacity = _object(payload.get("action_capacity"), f"{prefix}action_capacity", errors)
    if not isinstance(queue, list):
        errors.append(f"{prefix}execution_queue must be a list")
        return set()
    if len(queue) > 14:
        errors.append(f"{prefix}execution_queue exceeds 14")
    if capacity.get("limit") != 14 or capacity.get("used") != len(queue):
        errors.append(f"{prefix}action_capacity must report limit=14 and used=queue length")
    ids: set[str] = set()
    for index, raw in enumerate(queue):
        item = _object(raw, f"{prefix}execution_queue[{index}]", errors)
        item_id = item.get("plan_item_id")
        action = item.get("action")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{prefix}execution_queue[{index}] needs plan_item_id")
        else:
            if item_id in ids:
                errors.append(f"{prefix}execution_queue[{index}] duplicates plan item {item_id}")
            ids.add(item_id)
        if not isinstance(action, dict) or action.get("type") not in ALLOWED_ACTIONS:
            errors.append(f"{prefix}execution_queue[{index}] uses unknown action")
    return ids


def _binding(binding: Any, label: str, errors: list[str]) -> tuple[set[str], set[str]]:
    value = _object(binding, label, errors)
    sets: list[set[str]] = []
    for key in ("immediate_queue_item_ids", "conditional_queue_item_ids"):
        ids = value.get(key)
        if not isinstance(ids, list) or any(not isinstance(item, str) or not item.strip() for item in ids):
            errors.append(f"{label}.{key} must be a string list")
            sets.append(set())
        else:
            sets.append(set(ids))
    if sets[0] & sets[1]:
        errors.append(f"{label} may not bind one item to both timing classes")
    return sets[0], sets[1]


def _correction(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    correction = _object(value, label, errors)
    if correction.get("required") is True:
        if not isinstance(correction.get("id"), str) or not correction["id"].strip():
            errors.append(f"{label}.id must be non-empty")
        types = correction.get("required_action_types")
        if not isinstance(types, list) or not types or any(item not in ALLOWED_ACTIONS for item in types):
            errors.append(f"{label}.required_action_types must be a non-empty allowed list")
        verification = correction.get("verification")
        if not isinstance(verification, list) or not verification or any(not isinstance(item, str) or not item.strip() for item in verification):
            errors.append(f"{label}.verification must be non-empty")
    return correction


def _confront_plan_execution(
    correction: dict[str, Any], plan: dict[str, Any], execution: dict[str, Any], prefix: str, errors: list[str]
) -> dict[str, Any]:
    items = _plan_items(plan.get("action_queue"), f"{prefix}plan.action_queue", errors)
    immediate, conditional = _binding(plan.get("correction_binding"), f"{prefix}plan.correction_binding", errors)
    executed = _execution(execution, prefix, errors)
    acknowledgement = execution.get("future_queue_acknowledgement")
    ack = set(acknowledgement) if isinstance(acknowledgement, list) and all(isinstance(x, str) for x in acknowledgement) else set()
    if not isinstance(acknowledgement, list) or any(
        not isinstance(item, str) or not item.strip() for item in acknowledgement
    ):
        errors.append(f"{prefix}future_queue_acknowledgement must be a string list")
    elif len(ack) != len(acknowledgement):
        errors.append(f"{prefix}future_queue_acknowledgement may not contain duplicates")
    for item_id in executed:
        if item_id not in items:
            errors.append(f"{prefix}execution references unknown plan item {item_id}")
        elif items[item_id]["timing"] != "now":
            errors.append(f"{prefix}conditional_future item {item_id} cannot execute now")
    for raw in execution.get("execution_queue", []) if isinstance(execution.get("execution_queue"), list) else []:
        if isinstance(raw, dict) and raw.get("plan_item_id") in items and isinstance(raw.get("action"), dict):
            if raw["action"].get("type") != items[raw["plan_item_id"]]["action_type"]:
                errors.append(f"{prefix}execution action type differs from plan item")
    if acknowledgement is not None and ack != {item_id for item_id, item in items.items() if item["timing"] == "conditional_future"}:
        errors.append(f"{prefix}future queue acknowledgement must equal all conditional plan ids")
    if correction.get("required") is True:
        binding = plan.get("correction_binding", {})
        if isinstance(binding, dict) and binding.get("correction_id") != correction.get("id"):
            errors.append(f"{prefix}critical correction id is not bound")
        bound = immediate | conditional
        if not bound or not bound.issubset(items):
            errors.append(f"{prefix}critical correction must bind known plan items")
        if any(items.get(item_id, {}).get("timing") != "now" for item_id in immediate):
            errors.append(f"{prefix}immediate correction ids must be timing=now")
        if any(items.get(item_id, {}).get("timing") != "conditional_future" for item_id in conditional):
            errors.append(f"{prefix}conditional correction ids must be timing=conditional_future")
        covered = {items[item_id]["action_type"] for item_id in bound if item_id in items}
        if not set(correction.get("required_action_types", [])).issubset(covered):
            errors.append(f"{prefix}plan omits a required correction action type")
        if not immediate.issubset(executed):
            errors.append(f"{prefix}did not execute every immediate correction item")
        if not conditional.issubset(ack):
            errors.append(f"{prefix}did not acknowledge every conditional correction item")
    return {"plan_items": sorted(items), "executed_plan_items": sorted(executed), "conditional_plan_items": sorted(ack)}


def validate_planner_handoff(critic: Any, planner: Any) -> dict[str, Any]:
    """Validate the Critic→Planner boundary before an Actor call can consume it."""
    errors: list[str] = []
    for role, value in (("critic", critic), ("planner", planner)):
        report = validate_role_output(role, value)
        errors.extend(f"{role}: {item}" for item in report["errors"])
    if errors:
        return {"status": "failed", "errors": errors}
    correction = critic["correction"]
    items = _plan_items(planner.get("action_queue"), "theatre.plan.action_queue", errors)
    immediate, conditional = _binding(
        planner.get("correction_binding"), "theatre.plan.correction_binding", errors
    )
    if correction.get("required") is True:
        binding = planner.get("correction_binding", {})
        if isinstance(binding, dict) and binding.get("correction_id") != correction.get("id"):
            errors.append("theatre.critical correction id is not bound")
        bound = immediate | conditional
        if not bound or not bound.issubset(items):
            errors.append("theatre.critical correction must bind known plan items")
        if any(items.get(item_id, {}).get("timing") != "now" for item_id in immediate):
            errors.append("theatre.immediate correction ids must be timing=now")
        if any(items.get(item_id, {}).get("timing") != "conditional_future" for item_id in conditional):
            errors.append("theatre.conditional correction ids must be timing=conditional_future")
        covered = {items[item_id]["action_type"] for item_id in bound if item_id in items}
        if not set(correction.get("required_action_types", [])).issubset(covered):
            errors.append("theatre.plan omits a required correction action type")
    return {
        "status": "passed" if not errors else "failed",
        "plan_items": sorted(items),
        "immediate_plan_items": sorted(immediate),
        "conditional_plan_items": sorted(conditional),
        "errors": errors,
    }


def validate_role_output(role: str, value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if role not in ROLES:
        return {"status": "failed", "role": role, "errors": ["unknown v3 role"]}
    payload = _object(deepcopy(value), role, errors)
    if role == "critic":
        if payload.get("verdict") not in ("on_track", "correction_required", "critical"):
            errors.append("critic.verdict is invalid")
        correction = _correction(payload.get("correction"), "critic.correction", errors)
        if payload.get("verdict") in ("correction_required", "critical") and correction.get("required") is not True:
            errors.append("critic correction must be required for correction_required or critical")
    elif role == "consciousness":
        if not isinstance(payload.get("alternative_hypotheses"), list) or len(payload["alternative_hypotheses"]) != 3:
            errors.append("consciousness must provide exactly three alternative hypotheses")
        _object(payload.get("reversible_experiment"), "consciousness.reversible_experiment", errors)
        _object(payload.get("rules"), "consciousness.rules", errors)
    elif role == "planner":
        _plan_items(payload.get("action_queue"), "planner.action_queue", errors)
        _binding(payload.get("correction_binding"), "planner.correction_binding", errors)
        _object(payload.get("capital_budget"), "planner.capital_budget", errors)
    elif role == "actor":
        if payload.get("plan_adherence") not in ("followed", "justified_deviation", "blocked"):
            errors.append("actor.plan_adherence is invalid")
        _execution(payload, "actor.", errors)
        if not isinstance(payload.get("future_queue_acknowledgement"), list):
            errors.append("actor.future_queue_acknowledgement must be a list")
    else:
        audit = _object(payload.get("audit"), "control.audit", errors)
        if audit.get("verdict") not in ("on_track", "correction_required", "critical"):
            errors.append("control.audit.verdict is invalid")
        correction = _correction(audit.get("correction"), "control.audit.correction", errors)
        if audit.get("verdict") in ("correction_required", "critical") and correction.get("required") is not True:
            errors.append("control correction must be required for correction_required or critical")
        challenge = _object(payload.get("strategic_challenge"), "control.strategic_challenge", errors)
        if not isinstance(challenge.get("alternative_hypotheses"), list) or len(challenge["alternative_hypotheses"]) != 3:
            errors.append("control must provide exactly three alternative hypotheses")
        plan = _object(payload.get("plan"), "control.plan", errors)
        _confront_plan_execution(correction, plan, payload, "control.", errors)
    return {"status": "passed" if not errors else "failed", "role": role, "errors": errors}


def validate_theatre_handoff(critic: Any, planner: Any, actor: Any, consciousness: Any = None) -> dict[str, Any]:
    errors: list[str] = []
    for role, value in (("critic", critic), ("planner", planner), ("actor", actor)):
        report = validate_role_output(role, value)
        errors.extend(f"{role}: {item}" for item in report["errors"])
    if consciousness is not None:
        report = validate_role_output("consciousness", consciousness)
        errors.extend(f"consciousness: {item}" for item in report["errors"])
    if errors:
        return {"status": "failed", "errors": errors}
    detail = _confront_plan_execution(critic["correction"], planner, actor, "theatre.", errors)
    return {"status": "passed" if not errors else "failed", **detail, "errors": errors}


def extract_actions(role: str, value: Any) -> list[dict[str, Any]]:
    """Return only simulator actions that passed the frozen v3 role contract."""
    report = validate_role_output(role, value)
    if report["status"] != "passed":
        raise V3ContractError(f"{role}: " + "; ".join(report["errors"]))
    if role not in ("control", "actor"):
        return []
    return [item["action"] for item in value["execution_queue"]]


def validate_repair_envelope(envelope: Any, *, role: str, turn_index: int, state_hash: str) -> dict[str, Any]:
    errors: list[str] = []
    value = _object(envelope, "repair", errors)
    if value.get("attempt") != 1:
        errors.append("repair.attempt must equal 1")
    if value.get("role") != role or value.get("turn_index") != turn_index or value.get("state_hash") != state_hash:
        errors.append("repair must preserve role, turn, and state identity")
    original_errors = value.get("original_validation_errors")
    if not isinstance(original_errors, list) or not original_errors or any(not isinstance(item, str) for item in original_errors):
        errors.append("repair must preserve non-empty deterministic validation errors")
    original_hash = value.get("original_response_sha256")
    if not isinstance(original_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", original_hash):
        errors.append("repair must bind the preserved original response SHA-256")
    replacement = value.get("replacement")
    report = validate_role_output(role, replacement)
    errors.extend(f"replacement: {item}" for item in report["errors"])
    return {"status": "passed" if not errors else "failed", "role": role, "errors": errors}
