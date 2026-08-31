from __future__ import annotations

import hashlib
import json
import re
import subprocess
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


class V2ContractError(ValueError):
    """The frozen v2 protocol or one model handoff failed closed."""


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


def seed_plan(seed: int) -> dict[str, Any]:
    """Return the frozen first-arm plan for one registered v2 seed."""
    audit = audit_preregistration()
    if audit["status"] != "passed":
        raise V2ContractError("v2 preregistration is invalid: " + "; ".join(audit["errors"]))
    prereg = _read_json(PREREGISTRATION)
    seeds = prereg["design"]["paired_seeds"]
    if seed not in seeds:
        raise V2ContractError(f"seed {seed} is not preregistered for v2")
    raw_order = prereg["design"]["arm_order"][str(seed)]
    return {
        "seed": seed,
        "first_arm": raw_order.removesuffix("_first"),
        "days": prereg["design"]["days"],
        "model": prereg["design"]["model"],
        "thinking": prereg["design"]["thinking"],
    }


def _published_source_identity(source_commit: str) -> None:
    """Fail unless source_commit is the clean checkout published at origin/main."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise V2ContractError("source_commit must be a full lowercase 40-character SHA")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        remote_main = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.split()[0]
    except (OSError, subprocess.CalledProcessError, IndexError) as exc:
        raise V2ContractError(f"cannot establish published source identity: {exc}") from exc
    if head != source_commit:
        raise V2ContractError("source_commit is not the checked-out HEAD")
    if dirty:
        raise V2ContractError("v2 activation requires a clean source checkout")
    if remote_main != source_commit:
        raise V2ContractError("source_commit is not the SHA currently published at origin/main")


def activate_v2_pair(pair_dir: Path, source_commit: str) -> dict[str, Any]:
    """Bind an untouched offline pair to the exact published preregistration."""
    from .runner import atomic_json, read_json
    from .verify import verify_pair

    _published_source_identity(source_commit)
    audit = audit_preregistration()
    if audit["status"] != "passed":
        raise V2ContractError("v2 preregistration is invalid: " + "; ".join(audit["errors"]))

    pair_dir = pair_dir.resolve()
    pair = read_json(pair_dir / "pair.json")
    if pair.get("protocol_version") != "v2" or pair.get("status") != "ready":
        raise V2ContractError("activation requires a ready v2 pair")
    planned = seed_plan(int(pair.get("seed")))
    if pair.get("first_arm") != planned["first_arm"] or pair.get("next_arm") != planned["first_arm"]:
        raise V2ContractError("pair first arm differs from preregistration")
    if pair.get("inference_enabled"):
        raise V2ContractError("pair is already activated")

    prereg_snapshot = pair_dir / "preregistration.json"
    if not prereg_snapshot.is_file():
        raise V2ContractError("pair is missing its frozen preregistration snapshot")
    prereg_hash = _sha256(prereg_snapshot)
    if prereg_hash != pair.get("preregistration_sha256") or prereg_hash != _sha256(PREREGISTRATION):
        raise V2ContractError("pair preregistration snapshot differs from the published source")
    if pair.get("artifact_hashes") != audit["observed_hashes"]:
        raise V2ContractError("pair frozen artifact hashes differ from the published source")

    integrity = verify_pair(pair_dir)
    if integrity["status"] != "passed":
        raise V2ContractError("pair integrity failed before activation: " + "; ".join(integrity["errors"]))

    run_dirs: list[Path] = []
    for arm in ("control", "theatre"):
        run_dir = Path(pair[f"{arm}_run"])
        run_dirs.append(run_dir)
        for ledger_name in ("usage.jsonl", "model-decisions.jsonl", "turns.jsonl", "result.json"):
            if (run_dir / ledger_name).exists():
                raise V2ContractError(f"activation refuses existing inference evidence: {arm}/{ledger_name}")
        if read_json(run_dir / "state.json").get("day") != 0:
            raise V2ContractError(f"activation refuses non-zero {arm} state")

    receipt = {
        "schema_version": 1,
        "protocol": "v2",
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
            "source_commit": source_commit,
            "activation_receipt_sha256": receipt_hash,
        })
        atomic_json(run_dir / "manifest.json", manifest)
    pair.update({
        "inference_enabled": True,
        "source_commit": source_commit,
        "activation_receipt_sha256": receipt_hash,
    })
    atomic_json(pair_dir / "pair.json", pair)
    return receipt


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


def _validate_action_queue(queue: Any, errors: list[str], label: str) -> dict[str, str]:
    if not isinstance(queue, list) or not queue:
        errors.append(f"{label} must be non-empty")
        return {}
    items: dict[str, str] = {}
    for index, item in enumerate(queue):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        item_id = item.get("id")
        action_type = item.get("action_type")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{label}[{index}] must have a non-empty id")
        elif item_id in items:
            errors.append(f"{label}[{index}] duplicates plan item id {item_id}")
        elif action_type in ALLOWED_ACTIONS:
            items[item_id] = action_type
        if action_type not in ALLOWED_ACTIONS:
            errors.append(f"{label}[{index}] uses an unknown action type")
    return items


def _validate_required_correction(correction: dict[str, Any], errors: list[str], label: str) -> None:
    if correction.get("required") is not True:
        errors.append(f"{label}.required must be true")
    if not isinstance(correction.get("id"), str) or not correction["id"].strip():
        errors.append(f"{label}.id must be non-empty")
    action_types = correction.get("required_action_types")
    if (
        not isinstance(action_types, list)
        or not action_types
        or any(action_type not in ALLOWED_ACTIONS for action_type in action_types)
    ):
        errors.append(f"{label}.required_action_types must be a non-empty allowed-action list")
    verification = correction.get("verification")
    if (
        not isinstance(verification, list)
        or not verification
        or any(not isinstance(item, str) or not item.strip() for item in verification)
    ):
        errors.append(f"{label}.verification must be a non-empty string list")


def _validate_correction_binding(binding: dict[str, Any], errors: list[str], label: str) -> None:
    queue_ids = binding.get("queue_item_ids")
    if not isinstance(queue_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in queue_ids
    ):
        errors.append(f"{label}.queue_item_ids must be a string list")


def validate_role_output(role: str, value: Any) -> dict[str, Any]:
    errors: list[str] = []
    if role not in ROLES:
        return {"status": "failed", "role": role, "errors": ["unknown v2 role"]}
    payload = _require_object(deepcopy(value), role, errors)

    if role == "critic":
        if payload.get("verdict") not in ("on_track", "correction_required", "critical"):
            errors.append("critic.verdict is invalid")
        correction = _require_object(payload.get("correction"), "critic.correction", errors)
        if payload.get("verdict") in ("correction_required", "critical"):
            _validate_required_correction(correction, errors, "critic.correction")
    elif role == "consciousness":
        hypotheses = payload.get("alternative_hypotheses")
        if not isinstance(hypotheses, list) or len(hypotheses) != 3:
            errors.append("consciousness must provide exactly three alternative hypotheses")
        _require_object(payload.get("reversible_experiment"), "consciousness.reversible_experiment", errors)
        _require_object(payload.get("rules"), "consciousness.rules", errors)
    elif role == "planner":
        _validate_action_queue(payload.get("action_queue"), errors, "planner.action_queue")
        _require_object(payload.get("capital_budget"), "planner.capital_budget", errors)
        binding = _require_object(payload.get("correction_binding"), "planner.correction_binding", errors)
        _validate_correction_binding(binding, errors, "planner.correction_binding")
    elif role == "actor":
        if payload.get("plan_adherence") not in ("followed", "justified_deviation", "blocked"):
            errors.append("actor.plan_adherence is invalid")
        _validate_execution_queue(payload, errors)
    elif role == "control":
        audit = _require_object(payload.get("audit"), "control.audit", errors)
        challenge = _require_object(payload.get("strategic_challenge"), "control.strategic_challenge", errors)
        plan = _require_object(payload.get("plan"), "control.plan", errors)
        if audit.get("verdict") not in ("on_track", "correction_required", "critical"):
            errors.append("control.audit.verdict is invalid")
        hypotheses = challenge.get("alternative_hypotheses")
        if not isinstance(hypotheses, list) or len(hypotheses) != 3:
            errors.append("control must provide exactly three alternative hypotheses")
        if audit.get("verdict") in ("correction_required", "critical"):
            correction = _require_object(audit.get("correction"), "control.audit.correction", errors)
            _validate_required_correction(correction, errors, "control.audit.correction")
        plan_items = _validate_action_queue(
            plan.get("action_queue"), errors, "control.plan.action_queue"
        )
        binding = _require_object(
            plan.get("correction_binding"), "control.plan.correction_binding", errors
        )
        _validate_correction_binding(binding, errors, "control.plan.correction_binding")
        _validate_execution_queue(payload, errors, prefix="control.")
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
        correction = audit.get("correction", {})
        if isinstance(correction, dict) and correction.get("required") is True:
            raw_bound = binding.get("queue_item_ids", [])
            bound = set(raw_bound) if isinstance(raw_bound, list) else set()
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
        raise V2ContractError("; ".join(audit["errors"]) or f"{role} cannot submit actions")
    return [deepcopy(item["action"]) for item in value["execution_queue"]]


def validate_theatre_handoff(
    critic: Any,
    planner: Any,
    actor: Any,
    consciousness: Any = None,
    *,
    review_required: bool = True,
    consciousness_required: bool = False,
) -> dict[str, Any]:
    """Confront role handoffs mechanically; never trust actor self-attestation."""
    errors: list[str] = []
    role_values: list[tuple[str, Any]] = [("planner", planner), ("actor", actor)]
    if review_required:
        role_values.insert(0, ("critic", critic))
    if consciousness is not None:
        role_values.insert(1 if review_required else 0, ("consciousness", consciousness))
    elif consciousness_required:
        errors.append("consciousness: required scheduled passage is missing")
    for role, value in role_values:
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

    correction = critic["correction"] if review_required else {"required": False}
    if correction.get("required") is True:
        binding = planner["correction_binding"]
        raw_bound = binding.get("queue_item_ids", [])
        bound = set(raw_bound) if isinstance(raw_bound, list) else set()
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
        "review_required": review_required,
        "consciousness_required": consciousness_required,
        "consciousness_present": consciousness is not None,
        "errors": errors,
    }
