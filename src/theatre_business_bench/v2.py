from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PREREGISTRATION = ROOT / "preregistration" / "v2.json"
ALLOWED_ACTIONS = {
    "research_supplier",
    "negotiate",
    "place_order",
    "set_price",
    "restock",
    "collect_cash",
}
ROLES = ("control", "critic", "consciousness", "planner", "actor")
RESPONSIBILITIES = {
    "critical_audit",
    "financial_and_supply_planning",
    "business_operations",
    "strategic_challenge",
}


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

    if prereg.get("schema") != "theatre-business-bench-preregistration/v2":
        errors.append("schema must be theatre-business-bench-preregistration/v2")
    if prereg.get("run_status") != "not_started":
        errors.append("run_status must remain not_started before first inference")
    if prereg.get("frozen") is not True:
        errors.append("pre-registration must be frozen")

    design = prereg.get("design", {})
    seeds = design.get("paired_seeds", []) if isinstance(design, dict) else []
    if len(seeds) != 5 or len(set(seeds)) != 5 or not all(isinstance(seed, int) for seed in seeds):
        errors.append("design.paired_seeds must contain five unique integers")
    arm_order = design.get("arm_order", {}) if isinstance(design, dict) else {}
    if set(map(str, seeds)) != set(arm_order):
        errors.append("arm_order must pre-register every seed exactly once")
    if any(value not in ("control_first", "theatre_first") for value in arm_order.values()):
        errors.append("arm_order values must be control_first or theatre_first")
    if design.get("human_intervention") != "forbidden":
        errors.append("live human intervention must be forbidden")
    if design.get("live_internet") != "forbidden":
        errors.append("live internet must be forbidden")
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

    artifacts = prereg.get("artifacts", {})
    observed_hashes: dict[str, str] = {}
    required_keys = {"scenario", "shared_corpus", "protocol", *(f"prompt_{role}" for role in ROLES)}
    if not isinstance(artifacts, dict) or set(artifacts) != required_keys:
        errors.append("artifacts must contain only the frozen scenario, corpus, protocol, and five prompts")
    else:
        for key, descriptor in artifacts.items():
            if not isinstance(descriptor, dict):
                errors.append(f"artifact {key} must be an object")
                continue
            relative = descriptor.get("path")
            expected = descriptor.get("sha256")
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


def _require_object(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    return value


def _validate_execution_queue(payload: dict[str, Any], errors: list[str], prefix: str = "") -> None:
    queue = payload.get("execution_queue")
    capacity = _require_object(payload.get("action_capacity"), f"{prefix}action_capacity", errors)
    if not isinstance(queue, list):
        errors.append(f"{prefix}execution_queue must be a list")
        return
    if len(queue) > 14:
        errors.append(f"{prefix}execution_queue exceeds the frozen 14-action limit")
    if capacity.get("limit") != 14:
        errors.append(f"{prefix}action_capacity.limit must equal 14")
    if capacity.get("used") != len(queue):
        errors.append(f"{prefix}action_capacity.used must equal execution_queue length")
    plan_ids: set[str] = set()
    for index, item in enumerate(queue):
        if not isinstance(item, dict):
            errors.append(f"{prefix}execution_queue[{index}] must be an object")
            continue
        plan_id = item.get("plan_item_id")
        action = item.get("action")
        if not isinstance(plan_id, str) or not plan_id.strip():
            errors.append(f"{prefix}execution_queue[{index}] must bind a plan_item_id")
        else:
            plan_ids.add(plan_id)
        if not isinstance(action, dict) or action.get("type") not in ALLOWED_ACTIONS:
            errors.append(f"{prefix}execution_queue[{index}] uses an unknown action type")
    payload["_validated_plan_ids"] = sorted(plan_ids)


def validate_role_output(role: str, value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if role not in ROLES:
        return {"status": "failed", "role": role, "errors": ["unknown v2 role"]}
    payload = _require_object(deepcopy(value), role, errors)

    if role == "critic":
        if payload.get("verdict") not in ("on_track", "correction_required", "critical"):
            errors.append("critic.verdict is invalid")
        correction = _require_object(payload.get("correction"), "critic.correction", errors)
        if payload.get("verdict") == "critical":
            if correction.get("required") is not True:
                errors.append("critical verdict requires correction.required=true")
            if not correction.get("required_action_types"):
                errors.append("critical verdict requires executable action types")
            if not correction.get("verification"):
                errors.append("critical verdict requires verification criteria")
    elif role == "consciousness":
        hypotheses = payload.get("alternative_hypotheses")
        if not isinstance(hypotheses, list) or len(hypotheses) != 3:
            errors.append("consciousness must provide exactly three alternative hypotheses")
        _require_object(payload.get("reversible_experiment"), "consciousness.reversible_experiment", errors)
        _require_object(payload.get("rules"), "consciousness.rules", errors)
    elif role == "planner":
        queue = payload.get("action_queue")
        if not isinstance(queue, list) or not queue:
            errors.append("planner.action_queue must be non-empty")
        else:
            for index, item in enumerate(queue):
                if not isinstance(item, dict) or item.get("action_type") not in ALLOWED_ACTIONS:
                    errors.append(f"planner.action_queue[{index}] uses an unknown action type")
        _require_object(payload.get("capital_budget"), "planner.capital_budget", errors)
        _require_object(payload.get("correction_binding"), "planner.correction_binding", errors)
    elif role == "actor":
        if payload.get("plan_adherence") not in ("followed", "justified_deviation", "blocked"):
            errors.append("actor.plan_adherence is invalid")
        _validate_execution_queue(payload, errors)
    elif role == "control":
        audit = _require_object(payload.get("audit"), "control.audit", errors)
        challenge = _require_object(payload.get("strategic_challenge"), "control.strategic_challenge", errors)
        plan = _require_object(payload.get("plan"), "control.plan", errors)
        hypotheses = challenge.get("alternative_hypotheses")
        if not isinstance(hypotheses, list) or len(hypotheses) != 3:
            errors.append("control must provide exactly three alternative hypotheses")
        if audit.get("verdict") == "critical":
            correction = _require_object(audit.get("correction"), "control.audit.correction", errors)
            if correction.get("required") is not True or not correction.get("verification"):
                errors.append("control critical verdict requires a verifiable correction")
        if not isinstance(plan.get("action_queue"), list) or not plan.get("action_queue"):
            errors.append("control.plan.action_queue must be non-empty")
        _validate_execution_queue(payload, errors, prefix="control.")
        plan_items = {
            item.get("id"): item.get("action_type")
            for item in plan.get("action_queue", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        executed_ids: set[str] = set()
        for index, item in enumerate(payload.get("execution_queue", [])):
            if not isinstance(item, dict):
                continue
            plan_id = item.get("plan_item_id")
            action = item.get("action")
            if isinstance(plan_id, str):
                executed_ids.add(plan_id)
                if plan_id not in plan_items:
                    errors.append(f"control.execution_queue[{index}] references an unknown plan item")
                elif isinstance(action, dict) and action.get("type") != plan_items[plan_id]:
                    errors.append(f"control.execution_queue[{index}] action type differs from its plan item")
        if audit.get("verdict") == "critical":
            correction = audit.get("correction", {})
            binding = plan.get("correction_binding", {})
            bound = set(binding.get("queue_item_ids", [])) if isinstance(binding, dict) else set()
            if binding.get("correction_id") != correction.get("id"):
                errors.append("control critical correction id is not bound to the plan")
            if not bound or not bound.issubset(plan_items):
                errors.append("control critical correction must bind known plan items")
            required_types = set(correction.get("required_action_types", []))
            planned_types = {plan_items[item_id] for item_id in bound if item_id in plan_items}
            if not required_types.issubset(planned_types):
                errors.append("control plan omits a required critical-correction action type")
            if not bound.issubset(executed_ids):
                errors.append("control did not execute every bound critical-correction item")

    payload.pop("_validated_plan_ids", None)
    return {"status": "passed" if not errors else "failed", "role": role, "errors": errors}


def extract_actions(role: str, value: Any) -> list[dict[str, Any]]:
    audit = validate_role_output(role, value)
    if audit["status"] != "passed" or role not in ("actor", "control"):
        raise ValueError("; ".join(audit["errors"]) or f"{role} cannot submit actions")
    return [deepcopy(item["action"]) for item in value["execution_queue"]]


def validate_theatre_handoff(
    critic: Any,
    planner: Any,
    actor: Any,
) -> dict[str, Any]:
    """Confront role handoffs mechanically; never trust actor self-attestation."""
    errors: list[str] = []
    for role, value in (("critic", critic), ("planner", planner), ("actor", actor)):
        report = validate_role_output(role, value)
        errors.extend(f"{role}: {error}" for error in report["errors"])
    if errors:
        return {"status": "failed", "errors": errors}

    plan_items = {
        item["id"]: item["action_type"]
        for item in planner["action_queue"]
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("action_type") in ALLOWED_ACTIONS
    }
    executed_ids: set[str] = set()
    for index, item in enumerate(actor["execution_queue"]):
        plan_id = item["plan_item_id"]
        executed_ids.add(plan_id)
        if plan_id not in plan_items:
            errors.append(f"actor execution_queue[{index}] references an unknown plan item")
        elif item["action"]["type"] != plan_items[plan_id]:
            errors.append(f"actor execution_queue[{index}] action type differs from its plan item")

    correction = critic["correction"]
    if correction.get("required") is True:
        binding = planner["correction_binding"]
        bound = set(binding.get("queue_item_ids", []))
        if binding.get("correction_id") != correction.get("id"):
            errors.append("critical correction id is not bound to the plan")
        if not bound or not bound.issubset(plan_items):
            errors.append("critical correction must bind known plan items")
        required_types = set(correction.get("required_action_types", []))
        planned_types = {plan_items[item_id] for item_id in bound if item_id in plan_items}
        if not required_types.issubset(planned_types):
            errors.append("plan omits a required critical-correction action type")
        if not bound.issubset(executed_ids):
            errors.append("actor did not execute every bound critical-correction item")

    return {
        "status": "passed" if not errors else "failed",
        "plan_items": sorted(plan_items),
        "executed_plan_items": sorted(executed_ids),
        "errors": errors,
    }
