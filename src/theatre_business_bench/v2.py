from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_ROOT = ROOT / "protocols" / "v2"
PREREGISTRATION = PROTOCOL_ROOT / "preregistration.json"
V2_ROLES = ("control", "critic", "planner", "consciousness", "actor")


class V2ContractError(RuntimeError):
    """A v2 model response failed the pre-registered functional contract."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_functional_contract() -> dict[str, Any]:
    return json.loads((PROTOCOL_ROOT / "functional_contract.json").read_text(encoding="utf-8"))


def frozen_profile_hashes() -> dict[str, Any]:
    prompt_root = ROOT / "prompts" / "v2"
    return {
        "scenario": file_sha256(ROOT / "scenarios" / "vending_v2.json"),
        "functional_contract": file_sha256(PROTOCOL_ROOT / "functional_contract.json"),
        "knowledge": file_sha256(PROTOCOL_ROOT / "knowledge.md"),
        "prompts": {role: file_sha256(prompt_root / f"{role}.md") for role in V2_ROLES},
    }


def load_preregistration() -> dict[str, Any]:
    return json.loads(PREREGISTRATION.read_text(encoding="utf-8"))


def verify_v2_preregistration() -> dict[str, Any]:
    errors: list[str] = []
    prereg = load_preregistration()
    if prereg.get("status") != "prepared_pre_inference":
        errors.append("preregistration status must be prepared_pre_inference")
    seeds = prereg.get("paired_seeds")
    if not isinstance(seeds, list) or len(seeds) != 5:
        errors.append("exactly five paired seeds are required")
        seeds = []
    seed_values = [row.get("seed") for row in seeds if isinstance(row, dict)]
    if len(seed_values) != len(set(seed_values)) or 1201 in seed_values or 1101 in seed_values:
        errors.append("v2 seeds must be unique and must not reuse v1 evidence seeds")
    for row in seeds:
        if not isinstance(row, dict) or row.get("first_arm") not in ("control", "theatre"):
            errors.append("every paired seed requires a valid first_arm")
    actual = frozen_profile_hashes()
    if prereg.get("frozen_hashes_sha256") != actual:
        errors.append("frozen v2 artifact hashes differ from preregistration")
    contract = load_functional_contract()
    if contract.get("tools", {}).get("live_internet") is not False:
        errors.append("v2 must keep live internet disabled")
    if contract.get("shared_functions") != [
        "critical_business_review",
        "financial_and_supply_planning",
        "autonomous_strategic_challenge",
        "operational_execution",
    ]:
        errors.append("shared functional parity contract is incomplete")
    return {
        "schema_version": 1,
        "status": "passed" if not errors else "failed",
        "protocol_id": prereg.get("protocol_id"),
        "seeds": seeds,
        "frozen_hashes_sha256": actual,
        "preregistration_sha256": file_sha256(PREREGISTRATION),
        "errors": errors,
    }


def seed_plan(seed: int) -> dict[str, Any]:
    verification = verify_v2_preregistration()
    if verification["status"] != "passed":
        raise V2ContractError("v2 preregistration is not valid: " + "; ".join(verification["errors"]))
    for row in verification["seeds"]:
        if row["seed"] == seed:
            return row
    raise V2ContractError(f"seed {seed} is not pre-registered for v2")


def activate_v2_pair(pair_dir: Path, source_commit: str) -> dict[str, Any]:
    from .runner import atomic_json, read_json
    from .verify import verify_pair

    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise V2ContractError("source_commit must be a full lowercase 40-character SHA")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V2ContractError(f"cannot establish clean source identity: {exc}") from exc
    if source_commit != head:
        raise V2ContractError("source_commit is not the checked-out HEAD")
    if dirty:
        raise V2ContractError("v2 activation requires a clean source checkout")

    pair_dir = pair_dir.resolve()
    pair = read_json(pair_dir / "pair.json")
    if pair.get("protocol_version") != "v2" or pair.get("status") != "ready":
        raise V2ContractError("activation requires a ready v2 pair")
    planned = seed_plan(int(pair.get("seed")))
    if pair.get("first_arm") != planned["first_arm"] or pair.get("next_arm") != planned["first_arm"]:
        raise V2ContractError("pair first arm differs from preregistration")
    if pair.get("inference_enabled"):
        raise V2ContractError("pair is already activated")
    integrity = verify_pair(pair_dir)
    if integrity["status"] != "passed":
        raise V2ContractError(
            "pair integrity failed before activation: " + "; ".join(integrity["errors"])
        )

    verification = verify_v2_preregistration()
    frozen_prereg = pair_dir / "preregistration.json"
    if not frozen_prereg.is_file():
        raise V2ContractError("pair is missing its frozen preregistration snapshot")
    frozen_prereg_hash = file_sha256(frozen_prereg)
    if (
        frozen_prereg_hash != pair.get("preregistration_sha256")
        or frozen_prereg_hash != verification["preregistration_sha256"]
    ):
        raise V2ContractError("pair preregistration snapshot differs from the published source")
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
        "protocol_id": verification["protocol_id"],
        "source_commit": source_commit,
        "preregistration_sha256": frozen_prereg_hash,
        "frozen_hashes_sha256": verification["frozen_hashes_sha256"],
        "seed": pair["seed"],
        "first_arm": pair["first_arm"],
    }
    atomic_json(pair_dir / "activation.json", receipt)
    receipt_hash = file_sha256(pair_dir / "activation.json")
    for run_dir in run_dirs:
        manifest = read_json(run_dir / "manifest.json")
        manifest["inference_enabled"] = True
        manifest["source_commit"] = source_commit
        manifest["preregistration_sha256"] = frozen_prereg_hash
        manifest["activation_receipt_sha256"] = receipt_hash
        atomic_json(run_dir / "manifest.json", manifest)
    pair["inference_enabled"] = True
    pair["source_commit"] = source_commit
    pair["preregistration_sha256"] = frozen_prereg_hash
    pair["activation_receipt_sha256"] = receipt_hash
    atomic_json(pair_dir / "pair.json", pair)
    return receipt


def build_v2_bundle(
    arm: str,
    decision: dict[str, Any],
    pending: dict[str, Any],
) -> dict[str, Any]:
    if arm == "control":
        return {
            "critical_review": decision.get("critical_review"),
            "operating_plan": decision.get("operating_plan"),
            "strategic_review": decision.get("strategic_review"),
            "execution": decision.get("execution"),
        }
    return {
        "critical_review": (pending.get("critic") or {}).get("critical_review"),
        "operating_plan": (pending.get("planner") or {}).get("operating_plan"),
        "strategic_review": (pending.get("consciousness") or {}).get("strategic_review"),
        "execution": decision.get("execution"),
    }


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise V2ContractError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise V2ContractError(f"{label} must be an array")
    return value


def _unique_ids(rows: list[Any], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _require_object(row, label)
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise V2ContractError(f"{label} id must be a non-empty string")
        if item_id in indexed:
            raise V2ContractError(f"duplicate {label} id: {item_id}")
        indexed[item_id] = item
    return indexed


def _state_value(view: dict[str, Any], path: str) -> Any:
    if not path or path.startswith(".") or path.endswith("."):
        raise V2ContractError(f"invalid blocked state_path: {path!r}")
    value: Any = view
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise V2ContractError(f"blocked state_path does not exist: {path}")
        value = value[part]
    return value


def _validate_action(action: Any, view: dict[str, Any], label: str) -> dict[str, Any]:
    item = _require_object(action, label)
    action_type = item.get("type")
    contract = {
        row.get("type"): row.get("required", [])
        for row in view.get("allowed_actions", [])
        if isinstance(row, dict)
    }
    if action_type not in contract:
        raise V2ContractError(f"{label} uses an unknown action type: {action_type!r}")
    missing = [key for key in contract[action_type] if key not in item]
    if missing:
        raise V2ContractError(f"{label} is missing required fields: {', '.join(missing)}")
    return item


def audit_v2_bundle(
    bundle: dict[str, Any],
    view: dict[str, Any],
    simulator: Any | None = None,
) -> dict[str, Any]:
    critical = _require_object(bundle.get("critical_review"), "critical_review")
    plan = _require_object(bundle.get("operating_plan"), "operating_plan")
    strategy = _require_object(bundle.get("strategic_review"), "strategic_review")
    execution = _require_object(bundle.get("execution"), "execution")

    verdict = critical.get("verdict")
    if verdict not in ("on_track", "correction_required", "critical"):
        raise V2ContractError("critical_review.verdict is invalid")
    corrections = _unique_ids(
        _require_list(critical.get("required_corrections"), "required_corrections"),
        "correction",
    )
    if verdict != "on_track" and not corrections:
        raise V2ContractError(f"{verdict} requires at least one correction")

    queue = _unique_ids(
        _require_list(plan.get("execution_queue"), "execution_queue"),
        "queue item",
    )
    correction_to_queue: dict[str, list[str]] = {item_id: [] for item_id in corrections}
    for queue_id, item in queue.items():
        sources = _require_list(item.get("source_correction_ids"), f"queue {queue_id} source_correction_ids")
        for source in sources:
            if source not in corrections:
                raise V2ContractError(f"queue {queue_id} references unknown correction {source!r}")
            correction_to_queue[source].append(queue_id)
        _validate_action(item.get("action"), view, f"queue {queue_id} action")
    unmapped = [item_id for item_id, queue_ids in correction_to_queue.items() if not queue_ids]
    if unmapped:
        raise V2ContractError(f"critical corrections missing from execution queue: {', '.join(unmapped)}")

    reviewed = _require_list(strategy.get("reviewed_correction_ids"), "reviewed_correction_ids")
    if set(reviewed) != set(corrections):
        raise V2ContractError("Consciência must review every and only current correction id")
    required_queue_ids = _require_list(strategy.get("required_queue_ids"), "required_queue_ids")
    if len(required_queue_ids) != len(set(required_queue_ids)):
        raise V2ContractError("required_queue_ids contains duplicates")
    if any(queue_id not in queue for queue_id in required_queue_ids):
        raise V2ContractError("Consciência requires an unknown queue id")
    for correction_id, queue_ids in correction_to_queue.items():
        if not set(queue_ids).intersection(required_queue_ids):
            raise V2ContractError(f"correction {correction_id} does not reach a required execution item")

    executed = _require_list(execution.get("executed_queue_ids"), "executed_queue_ids")
    if len(executed) != len(set(executed)) or any(queue_id not in queue for queue_id in executed):
        raise V2ContractError("executed_queue_ids must be unique known queue ids")
    blocked_rows = _require_list(execution.get("blocked_queue_ids"), "blocked_queue_ids")
    blocked: dict[str, dict[str, Any]] = {}
    for row in blocked_rows:
        item = _require_object(row, "blocked queue item")
        queue_id = item.get("queue_id")
        if queue_id not in queue or queue_id in blocked:
            raise V2ContractError("blocked_queue_ids must contain unique known queue ids")
        if queue_id in executed:
            raise V2ContractError(f"queue {queue_id} cannot be both executed and blocked")
        path = item.get("state_path")
        if not isinstance(path, str):
            raise V2ContractError(f"blocked queue {queue_id} requires state_path")
        observed = _state_value(view, path)
        if item.get("observed_value") != observed:
            raise V2ContractError(f"blocked queue {queue_id} observed_value does not match {path}")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise V2ContractError(f"blocked queue {queue_id} requires a reason")
        if simulator is None:
            raise V2ContractError("state-blocked execution requires simulator preflight")
        preflight = copy.deepcopy(simulator).apply_turn([queue[queue_id]["action"]], advance_days=0)
        if preflight.accepted or len(preflight.rejected) != 1:
            raise V2ContractError(f"blocked queue {queue_id} is executable in the current simulator state")
        rejection = preflight.rejected[0].get("reason")
        if item.get("simulator_rejection") != rejection:
            raise V2ContractError(f"blocked queue {queue_id} simulator_rejection does not match preflight")
        blocked[queue_id] = item

    unresolved = set(required_queue_ids) - set(executed) - set(blocked)
    if unresolved:
        raise V2ContractError(f"required queue items neither executed nor state-blocked: {', '.join(sorted(unresolved))}")

    actions = [queue[queue_id]["action"] for queue_id in executed]
    additional_rows = _require_list(execution.get("additional_actions"), "additional_actions")
    for index, row in enumerate(additional_rows):
        item = _require_object(row, f"additional action {index}")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise V2ContractError(f"additional action {index} requires a reason")
        actions.append(_validate_action(item.get("action"), view, f"additional action {index}"))

    budget = view.get("action_budget", {}).get("max_actions_per_turn")
    if not isinstance(budget, int) or budget <= 0:
        raise V2ContractError("v2 shared evidence is missing the explicit action budget")
    if len(actions) > budget:
        raise V2ContractError(f"execution uses {len(actions)} actions but the budget is {budget}")

    return {
        "schema_version": 1,
        "status": "passed",
        "verdict": verdict,
        "correction_ids": sorted(corrections),
        "correction_queue_map": {key: sorted(value) for key, value in sorted(correction_to_queue.items())},
        "required_queue_ids": required_queue_ids,
        "executed_queue_ids": executed,
        "blocked_queue_ids": sorted(blocked),
        "additional_action_count": len(additional_rows),
        "action_count": len(actions),
        "action_budget": budget,
        "actions": actions,
    }
