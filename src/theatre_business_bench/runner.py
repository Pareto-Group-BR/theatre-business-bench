from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .simulator import VendingSimulator, stable_hash
from .transport import ModelResult, ModelTransportError, OpenClawCodexTransport
from .v2 import (
    PREREGISTRATION,
    V2ContractError,
    audit_preregistration,
    extract_actions,
    seed_plan,
    validate_role_output,
    validate_theatre_handoff,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "scenarios" / "vending_v1.json"
PROMPT_FILES = {
    "control": ROOT / "prompts" / "control.md",
    "critic": ROOT / "prompts" / "critic.md",
    "planner": ROOT / "prompts" / "planner.md",
    "actor": ROOT / "prompts" / "actor.md",
}
V2_PROMPT_FILES = {
    role: ROOT / "prompts" / "v2" / f"{role}.md"
    for role in ("control", "critic", "consciousness", "planner", "actor")
}
V2_CORPUS = ROOT / "corpus" / "vending_operations_v2.md"
V2_PROTOCOL = ROOT / "docs" / "EXPERIMENT_PROTOCOL_V2.md"


class QuotaPause(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def prompt_hashes(files: dict[str, Path] | None = None) -> dict[str, str]:
    files = files or PROMPT_FILES
    return {role: stable_hash(path.read_text(encoding="utf-8")) for role, path in files.items()}


def make_run_id(arm: str, seed: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{arm}-s{seed}"


@dataclass
class TokenBudget:
    ledger_path: Path
    daily_limit: int | None = None
    reserve_per_call: int = 25_000

    def used_today(self) -> int:
        if not self.ledger_path.exists():
            return 0
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0
        with self.ledger_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if str(item.get("timestamp", "")).startswith(today):
                    total += int(item.get("usage", {}).get("total", 0))
        return total

    def assert_call_allowed(self) -> None:
        if self.daily_limit is None:
            return
        used = self.used_today()
        if used + self.reserve_per_call > self.daily_limit:
            raise QuotaPause(
                f"local daily token budget paused the run: used={used}, "
                f"reserve={self.reserve_per_call}, limit={self.daily_limit}"
            )


def create_run(
    arm: str,
    seed: int,
    days: int | None = None,
    run_root: Path | None = None,
    model: str = "openai/gpt-5.6-sol",
    agent_id: str = "business-bench",
    thinking: str = "medium",
    protocol: str = "v1",
) -> Path:
    if arm not in ("control", "theatre"):
        raise ValueError("arm must be control or theatre")
    if protocol not in ("v1", "v2"):
        raise ValueError("protocol must be v1 or v2")
    role_prompts = V2_PROMPT_FILES if protocol == "v2" else PROMPT_FILES
    v2_audit = audit_preregistration() if protocol == "v2" else None
    if v2_audit is not None and v2_audit["status"] != "passed":
        raise V2ContractError("v2 preregistration is invalid: " + "; ".join(v2_audit["errors"]))
    scenario = read_json(DEFAULT_SCENARIO)
    if days is not None:
        scenario["days"] = int(days)
    run_root = run_root or ROOT / "runs"
    run_dir = run_root / make_run_id(arm, seed)
    run_dir.mkdir(parents=True, exist_ok=False)
    atomic_json(run_dir / "scenario.json", scenario)
    for role, prompt_path in role_prompts.items():
        shutil.copy2(prompt_path, run_dir / f"prompt-{role}.md")
    if protocol == "v2":
        shutil.copy2(V2_CORPUS, run_dir / "shared-corpus.md")
        shutil.copy2(V2_PROTOCOL, run_dir / "protocol.md")
    simulator = VendingSimulator(scenario, seed)
    atomic_json(run_dir / "state.json", simulator.state)
    manifest = {
        "schema_version": 2 if protocol == "v2" else 1,
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "arm": arm,
        "seed": int(seed),
        "model": model,
        "agent_id": agent_id,
        "thinking": thinking,
        "scenario_hash": stable_hash(scenario),
        "prompt_hashes": prompt_hashes(role_prompts),
        "protocol_version": protocol,
        "decision_period_days": scenario["decision_period_days"],
        "theatre_review_every_turns": max(1, round(28 / scenario["decision_period_days"])),
        "virtual_output_cost_per_million_tokens": scenario["virtual_output_cost_per_million_tokens"],
        "official": False,
        "status": "ready",
        "usage_ledger_path": str((run_root / "usage-ledger.jsonl").resolve()),
    }
    if protocol == "v2":
        prereg = read_json(PREREGISTRATION)
        manifest.update({
            "inference_enabled": False,
            "artifact_hashes": v2_audit["observed_hashes"],
            "shared_corpus_hash": stable_hash((run_dir / "shared-corpus.md").read_text(encoding="utf-8")),
            "protocol_hash": stable_hash((run_dir / "protocol.md").read_text(encoding="utf-8")),
            "action_budget": int(scenario["max_actions_per_turn"]),
            "v2_cadence": prereg["cadence"],
        })
    atomic_json(run_dir / "manifest.json", manifest)
    atomic_json(run_dir / "role-memory.json", {
        "critic": None,
        "consciousness": None,
        "planner": None,
        "actor": None,
        "control": None,
        "cycle_audit": None,
    })
    atomic_json(run_dir / "flow.json", {
        "status": "ready",
        "current_step": "prepare_turn",
        "turn_index": 0,
        "phase": None,
        "review_required": None,
        "pending": {},
        "updated_at": utc_now(),
    })
    return run_dir


def _role_message(
    run_dir: Path,
    manifest: dict[str, Any],
    role: str,
    view: dict[str, Any],
    flow: dict[str, Any],
    memories: dict[str, Any],
) -> str:
    # A durable run executes the bytes frozen at creation. Reading ROOT/prompts
    # here would let a later merge silently change an in-flight treatment while
    # manifest.json continued to attest the older hash.
    prompt = (run_dir / f"prompt-{role}.md").read_text(encoding="utf-8")
    if manifest.get("protocol_version") == "v2":
        shared_evidence = {
            "business_state": view,
            "action_contract": view.get("allowed_actions"),
            "max_actions_per_turn": view.get("max_actions_per_turn"),
            "frozen_domain_corpus": (run_dir / "shared-corpus.md").read_text(encoding="utf-8"),
            "schedule": flow.get("schedule", {}),
            "prior_cycle_audit": memories.get("cycle_audit"),
        }
        context: dict[str, Any] = {"shared_evidence": shared_evidence}
        if role in ("consciousness", "planner", "actor"):
            context["critic_passage"] = flow["pending"].get("critic")
        if role in ("planner", "actor"):
            context["consciousness_passage"] = flow["pending"].get("consciousness")
        if role == "actor":
            context["planner_passage"] = flow["pending"].get("planner") or memories.get("planner")
        return prompt + "\n\nCURRENT V2 INPUT\n" + json.dumps(context, sort_keys=True, ensure_ascii=False)
    context = {"business_state": view}
    if role == "critic":
        context["prior_plan"] = memories.get("planner")
        context["prior_actor_result"] = memories.get("actor")
    elif role == "planner":
        context["critic_judgment"] = flow["pending"].get("critic")
        context["prior_plan"] = memories.get("planner")
    elif role == "actor":
        context["current_plan"] = flow["pending"].get("planner") or memories.get("planner")
        context["prior_actor_memory"] = memories.get("actor", {}).get("memory") if isinstance(memories.get("actor"), dict) else None
    elif role == "control":
        context["prior_memory"] = memories.get("control", {}).get("memory") if isinstance(memories.get("control"), dict) else None
    return prompt + "\n\nCURRENT INPUT\n" + json.dumps(context, sort_keys=True, ensure_ascii=False)


def _critical_event(view: dict[str, Any]) -> bool:
    return any(event.get("severity") == "critical" for event in view.get("recent_events", []))


def _session_key(manifest: dict[str, Any], role: str) -> str:
    safe_run = manifest["run_id"].lower().replace("_", "-")
    return f"agent:{manifest['agent_id']}:bench-{safe_run}-{role}"


def _model_response_hash(result: ModelResult) -> str:
    return stable_hash(result.content if result.content is not None else result.text)


def _record_model_result(
    run_dir: Path,
    manifest: dict[str, Any],
    role: str,
    result: ModelResult,
) -> dict[str, Any]:
    row = {
        "timestamp": utc_now(),
        "run_id": manifest["run_id"],
        "arm": manifest["arm"],
        "seed": manifest["seed"],
        "role": role,
        "gateway_run_id": result.run_id,
        "session_id": result.session_id,
        "provider": result.provider,
        "model": result.model,
        "duration_ms": result.duration_ms,
        "usage": result.usage,
        "response_hash": _model_response_hash(result),
    }
    if result.parse_error is not None:
        row["outcome"] = "invalid_model_json"
        row["parse_error"] = result.parse_error
    append_jsonl(run_dir / "usage.jsonl", row)
    append_jsonl(_usage_ledger_path(manifest), row)
    return row


def _usage_ledger_path(manifest: dict[str, Any]) -> Path:
    configured = manifest.get("usage_ledger_path")
    if isinstance(configured, str) and configured:
        return Path(configured)
    return ROOT / "runs" / "usage-ledger.jsonl"


def _invoke_role(
    run_dir: Path,
    manifest: dict[str, Any],
    role: str,
    view: dict[str, Any],
    flow: dict[str, Any],
    memories: dict[str, Any],
    budget: TokenBudget,
    transport: OpenClawCodexTransport,
) -> dict[str, Any]:
    budget.assert_call_allowed()
    result = transport.invoke(
        _session_key(manifest, role),
        _role_message(run_dir, manifest, role, view, flow, memories),
    )
    if result.provider != "openai" or result.model != manifest["model"].split("/", 1)[-1]:
        raise RuntimeError(f"model drift detected: {result.provider}/{result.model}")
    usage_row = _record_model_result(run_dir, manifest, role, result)
    if result.parse_error is not None or result.content is None:
        append_jsonl(run_dir / "model-failures.jsonl", {
            "timestamp": usage_row["timestamp"],
            "turn_index": flow["turn_index"],
            "role": role,
            "raw_text": result.text,
            "response_hash": usage_row["response_hash"],
            "parse_error": result.parse_error or "model response did not contain a JSON object",
            "gateway_run_id": result.run_id,
            "session_id": result.session_id,
        })
        raise V2ContractError(f"{role}: {result.parse_error or 'invalid model JSON'}")
    append_jsonl(run_dir / "model-decisions.jsonl", {
        "timestamp": utc_now(),
        "turn_index": flow["turn_index"],
        "role": role,
        "content": result.content,
        "response_hash": usage_row["response_hash"],
    })
    return result.content


def _output_tokens(run_dir: Path) -> int:
    path = run_dir / "usage.jsonl"
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(int(json.loads(line)["usage"].get("output", 0)) for line in handle if line.strip())


def _v2_activation_allows(run_dir: Path, manifest: dict[str, Any]) -> bool:
    pair_dir_raw = manifest.get("pair_dir")
    expected_hash = manifest.get("activation_receipt_sha256")
    if not isinstance(pair_dir_raw, str) or not isinstance(expected_hash, str):
        return False
    activation = Path(pair_dir_raw) / "activation.json"
    if not activation.is_file() or hashlib.sha256(activation.read_bytes()).hexdigest() != expected_hash:
        return False
    receipt = read_json(activation)
    return (
        receipt.get("pair_id") == manifest.get("pair_id")
        and receipt.get("artifact_hashes") == manifest.get("artifact_hashes")
        and receipt.get("source_commit") == manifest.get("source_commit")
        and receipt.get("preregistration_sha256") == manifest.get("preregistration_sha256")
        and receipt.get("seed") == manifest.get("seed")
    )


def _v2_schedule(manifest: dict[str, Any], flow: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    cadence = manifest["v2_cadence"]
    turn_index = int(flow["turn_index"])
    strategic_due = (
        turn_index % int(cadence["strategic_review_every_turns"]) == 0
        or _critical_event(view)
    )
    consciousness_due = (
        turn_index == 0
        or turn_index % int(cadence["consciousness_every_turns"]) == 0
    )
    return {
        "turn_index": turn_index,
        "strategic_review_due": strategic_due,
        "consciousness_due": consciousness_due,
        "critical_simulator_event": _critical_event(view),
    }


def _require_valid_role(role: str, value: Any) -> None:
    report = validate_role_output(role, value)
    if report["status"] != "passed":
        raise V2ContractError(f"{role}: " + "; ".join(report["errors"]))


def step_run(run_dir: Path, daily_token_budget: int | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "manifest.json")
    scenario = read_json(run_dir / "scenario.json")
    state = read_json(run_dir / "state.json")
    flow = read_json(run_dir / "flow.json")
    memories = read_json(run_dir / "role-memory.json")
    simulator = VendingSimulator(scenario, manifest["seed"], state=state)
    if simulator.state["terminated"]:
        return finalize_run(run_dir)
    if manifest.get("protocol_version") == "v2" and (
        not manifest.get("inference_enabled") or not _v2_activation_allows(run_dir, manifest)
    ):
        return {
            "status": "blocked_preregistration",
            "reason": "v2 pair is offline-only until the exact published source commit is activated",
            "run_dir": str(run_dir),
        }
    transport = OpenClawCodexTransport(
        agent_id=manifest["agent_id"], model=manifest["model"], thinking=manifest["thinking"]
    )
    budget = TokenBudget(_usage_ledger_path(manifest), daily_token_budget)
    view = simulator.public_view()

    if flow["current_step"] == "prepare_turn":
        if manifest.get("protocol_version") == "v2":
            schedule = _v2_schedule(manifest, flow, view)
            review_required = manifest["arm"] == "theatre" and schedule["strategic_review_due"]
        else:
            schedule = {}
            review_required = manifest["arm"] == "theatre" and (
                flow["turn_index"] % manifest["theatre_review_every_turns"] == 0 or _critical_event(view)
            )
        flow.update({
            "status": "running",
            "phase": "critic" if review_required else ("actor" if manifest["arm"] == "theatre" else "control"),
            "review_required": review_required,
            "schedule": schedule,
            "pending": {},
            "current_step": "model_roles",
            "updated_at": utc_now(),
        })
        atomic_json(run_dir / "flow.json", flow)

    try:
        if flow["phase"] == "critic":
            flow["pending"]["critic"] = _invoke_role(run_dir, manifest, "critic", view, flow, memories, budget, transport)
            if manifest.get("protocol_version") == "v2":
                _require_valid_role("critic", flow["pending"]["critic"])
                flow["schedule"]["consciousness_due"] = bool(
                    flow["schedule"].get("consciousness_due")
                    or flow["pending"]["critic"].get("verdict") == "critical"
                )
            memories["critic"] = flow["pending"]["critic"]
            flow["phase"] = (
                "consciousness"
                if manifest.get("protocol_version") == "v2" and flow["schedule"]["consciousness_due"]
                else "planner"
            )
            flow["updated_at"] = utc_now()
            atomic_json(run_dir / "role-memory.json", memories)
            atomic_json(run_dir / "flow.json", flow)
            return {"status": "running", "completed_role": "critic", "next_role": flow["phase"], "run_dir": str(run_dir)}
        if flow["phase"] == "consciousness":
            flow["pending"]["consciousness"] = _invoke_role(
                run_dir, manifest, "consciousness", view, flow, memories, budget, transport
            )
            _require_valid_role("consciousness", flow["pending"]["consciousness"])
            memories["consciousness"] = flow["pending"]["consciousness"]
            flow["phase"] = "planner"
            flow["updated_at"] = utc_now()
            atomic_json(run_dir / "role-memory.json", memories)
            atomic_json(run_dir / "flow.json", flow)
            return {"status": "running", "completed_role": "consciousness", "next_role": "planner", "run_dir": str(run_dir)}
        if flow["phase"] == "planner":
            flow["pending"]["planner"] = _invoke_role(run_dir, manifest, "planner", view, flow, memories, budget, transport)
            if manifest.get("protocol_version") == "v2":
                _require_valid_role("planner", flow["pending"]["planner"])
            memories["planner"] = flow["pending"]["planner"]
            flow["phase"] = "actor"
            flow["updated_at"] = utc_now()
            atomic_json(run_dir / "role-memory.json", memories)
            atomic_json(run_dir / "flow.json", flow)
            return {"status": "running", "completed_role": "planner", "next_role": "actor", "run_dir": str(run_dir)}
        role = "actor" if manifest["arm"] == "theatre" else "control"
        decision = _invoke_role(run_dir, manifest, role, view, flow, memories, budget, transport)
        memories[role] = decision
        decision_audit = None
        if manifest.get("protocol_version") == "v2":
            if role == "control":
                _require_valid_role("control", decision)
                actions = extract_actions("control", decision)
                decision_audit = {
                    "schema_version": 1,
                    "status": "passed",
                    "arm": "control",
                    "schedule": flow.get("schedule", {}),
                    "action_count": len(actions),
                    "actions": actions,
                }
            else:
                review_required = bool(flow.get("review_required"))
                planner = flow["pending"].get("planner") or memories.get("planner")
                consciousness = flow["pending"].get("consciousness")
                handoff = validate_theatre_handoff(
                    flow["pending"].get("critic"),
                    planner,
                    decision,
                    consciousness,
                    review_required=review_required,
                    consciousness_required=bool(
                        review_required and flow.get("schedule", {}).get("consciousness_due")
                    ),
                )
                if handoff["status"] != "passed":
                    raise V2ContractError("theatre handoff: " + "; ".join(handoff["errors"]))
                actions = extract_actions("actor", decision)
                decision_audit = {
                    "schema_version": 1,
                    "status": "passed",
                    "arm": "theatre",
                    "schedule": flow.get("schedule", {}),
                    **{key: value for key, value in handoff.items() if key not in ("status", "errors")},
                    "action_count": len(actions),
                    "actions": actions,
                }
        else:
            actions = decision.get("actions", [])
            if not isinstance(actions, list):
                actions = []
        applied = simulator.apply_turn(actions)
        turn_row = {
            "timestamp": utc_now(),
            "turn_index": flow["turn_index"],
            "day_before": view["day"],
            "day_after": simulator.state["day"],
            "role": role,
            "accepted": applied.accepted,
            "rejected": applied.rejected,
            "state_hash": applied.state_hash,
        }
        if decision_audit is not None:
            turn_row["decision_audit"] = decision_audit
            memories["cycle_audit"] = {
                **decision_audit,
                "turn_index": flow["turn_index"],
                "accepted_actions": len(applied.accepted),
                "rejected_actions": len(applied.rejected),
                "state_hash": applied.state_hash,
            }
        append_jsonl(run_dir / "turns.jsonl", turn_row)
        atomic_json(run_dir / "state.json", simulator.state)
        atomic_json(run_dir / "role-memory.json", memories)
        flow.update({
            "status": "completed" if simulator.state["terminated"] else "ready",
            "current_step": "finalize" if simulator.state["terminated"] else "prepare_turn",
            "turn_index": flow["turn_index"] + 1,
            "phase": None,
            "review_required": None,
            "schedule": {},
            "pending": {},
            "updated_at": utc_now(),
        })
        atomic_json(run_dir / "flow.json", flow)
        if simulator.state["terminated"]:
            return finalize_run(run_dir)
        return {
            "status": "running",
            "completed_role": role,
            "day": simulator.state["day"],
            "score": simulator.score(_output_tokens(run_dir)),
            "next_role": (
                "critic"
                if manifest["arm"] == "theatre" and (
                    manifest.get("protocol_version") == "v2"
                    and flow["turn_index"] % int(manifest["v2_cadence"]["strategic_review_every_turns"]) == 0
                    or manifest.get("protocol_version") != "v2"
                    and flow["turn_index"] % manifest["theatre_review_every_turns"] == 0
                )
                else role
            ),
            "run_dir": str(run_dir),
        }
    except V2ContractError as exc:
        flow["status"] = "failed_contract"
        flow["updated_at"] = utc_now()
        flow["contract_failure"] = {"phase": flow.get("phase"), "message": str(exc)}
        atomic_json(run_dir / "flow.json", flow)
        return {"status": "failed_contract", "reason": str(exc), "run_dir": str(run_dir)}
    except (QuotaPause, ModelTransportError) as exc:
        if isinstance(exc, ModelTransportError):
            message = str(exc).lower()
            quota_markers = ("quota", "rate limit", "rate_limit", "usage limit", "too many requests", "429")
            if not any(marker in message for marker in quota_markers):
                raise
        flow["status"] = "paused_quota"
        flow["updated_at"] = utc_now()
        flow["pause_reason"] = str(exc)
        atomic_json(run_dir / "flow.json", flow)
        return {"status": "paused_quota", "reason": str(exc), "run_dir": str(run_dir)}


def finalize_run(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json")
    scenario = read_json(run_dir / "scenario.json")
    state = read_json(run_dir / "state.json")
    simulator = VendingSimulator(scenario, manifest["seed"], state=state)
    score = simulator.score(_output_tokens(run_dir))
    report = {
        "run_id": manifest["run_id"],
        "arm": manifest["arm"],
        "seed": manifest["seed"],
        "model": manifest["model"],
        "finished_at": utc_now(),
        "state_hash": stable_hash(state),
        "score": score,
    }
    atomic_json(run_dir / "result.json", report)
    manifest["status"] = "completed"
    manifest["completed_at"] = report["finished_at"]
    atomic_json(run_dir / "manifest.json", manifest)
    return {"status": "completed", **report, "run_dir": str(run_dir)}


def create_pair(
    seed: int,
    days: int = 365,
    model: str = "openai/gpt-5.6-sol",
    agent_id: str = "business-bench",
    thinking: str = "medium",
    run_root: Path | None = None,
    protocol: str = "v1",
    next_arm: str | None = None,
) -> Path:
    if protocol == "v2":
        planned = seed_plan(seed)
        if days != planned["days"] or model != planned["model"] or thinking != planned["thinking"]:
            raise V2ContractError("v2 horizon, model, or thinking differs from preregistration")
        if next_arm is None:
            next_arm = planned["first_arm"]
        elif next_arm != planned["first_arm"]:
            raise V2ContractError("v2 first arm differs from preregistration")
    elif protocol == "v1" and next_arm is None:
        next_arm = "control"
    elif protocol not in ("v1", "v2"):
        raise ValueError("protocol must be v1 or v2")
    if next_arm not in ("control", "theatre"):
        raise ValueError("next_arm must be control or theatre")
    pair_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-pair-s{seed}"
    run_root = run_root or ROOT / "runs"
    pair_dir = run_root / "pairs" / pair_id
    pair_dir.mkdir(parents=True, exist_ok=False)
    preregistration_sha256 = None
    artifact_hashes = None
    if protocol == "v2":
        shutil.copy2(PREREGISTRATION, pair_dir / "preregistration.json")
        preregistration_sha256 = hashlib.sha256((pair_dir / "preregistration.json").read_bytes()).hexdigest()
        artifact_hashes = audit_preregistration()["observed_hashes"]
    control = create_run(
        "control", seed, days, run_root=run_root, model=model, agent_id=agent_id,
        thinking=thinking, protocol=protocol,
    )
    theatre = create_run(
        "theatre", seed, days, run_root=run_root, model=model, agent_id=agent_id,
        thinking=thinking, protocol=protocol,
    )
    pair = {
        "schema_version": 1,
        "pair_id": pair_id,
        "created_at": utc_now(),
        "seed": seed,
        "days": days,
        "model": model,
        "thinking": thinking,
        "control_run": str(control),
        "theatre_run": str(theatre),
        "next_arm": next_arm,
        "first_arm": next_arm,
        "protocol_version": protocol,
        "inference_enabled": protocol != "v2",
        "status": "ready",
        "official": False,
    }
    if protocol == "v2":
        pair["preregistration_sha256"] = preregistration_sha256
        pair["artifact_hashes"] = artifact_hashes
    atomic_json(pair_dir / "pair.json", pair)
    if protocol == "v2":
        for run_dir in (control, theatre):
            manifest = read_json(run_dir / "manifest.json")
            manifest.update({
                "pair_id": pair_id,
                "pair_dir": str(pair_dir.resolve()),
                "preregistration_sha256": preregistration_sha256,
            })
            atomic_json(run_dir / "manifest.json", manifest)
    return pair_dir


def _run_progress(run_dir: Path) -> tuple[int, bool]:
    state = read_json(run_dir / "state.json")
    return int(state["day"]), bool(state["terminated"])


def step_pair(pair_dir: Path, daily_token_budget: int | None = None) -> dict[str, Any]:
    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    control = Path(pair["control_run"])
    theatre = Path(pair["theatre_run"])
    control_day, control_done = _run_progress(control)
    theatre_day, theatre_done = _run_progress(theatre)
    if control_done and theatre_done:
        return finalize_pair(pair_dir)
    if control_done:
        arm = "theatre"
    elif theatre_done:
        arm = "control"
    elif control_day < theatre_day:
        arm = "control"
    elif theatre_day < control_day:
        arm = "theatre"
    else:
        arm = pair.get("next_arm", "control")
    run_dir = control if arm == "control" else theatre
    result = step_run(run_dir, daily_token_budget=daily_token_budget)
    if result["status"] == "blocked_preregistration":
        return {
            "status": "blocked_preregistration",
            "pair_id": pair["pair_id"],
            "arm": arm,
            "pair_status": pair["status"],
            "result": result,
        }
    pair["next_arm"] = "theatre" if arm == "control" else "control"
    stop_statuses = ("paused_quota", "failed_contract", "blocked_preregistration")
    pair["status"] = result["status"] if result["status"] in stop_statuses else "running"
    pair["last_arm"] = arm
    pair["last_result"] = result
    pair["updated_at"] = utc_now()
    atomic_json(pair_path, pair)
    return {"pair_id": pair["pair_id"], "arm": arm, "pair_status": pair["status"], "result": result}


def finalize_pair(pair_dir: Path) -> dict[str, Any]:
    pair_path = pair_dir / "pair.json"
    pair = read_json(pair_path)
    control = read_json(Path(pair["control_run"]) / "result.json")
    theatre = read_json(Path(pair["theatre_run"]) / "result.json")
    difference = round(theatre["score"]["primary_score"] - control["score"]["primary_score"], 2)
    result = {
        "pair_id": pair["pair_id"],
        "seed": pair["seed"],
        "finished_at": utc_now(),
        "control": control,
        "theatre": theatre,
        "paired_difference_theatre_minus_control": difference,
        "winner": "theatre" if difference > 0 else "control" if difference < 0 else "tie",
    }
    atomic_json(pair_dir / "result.json", result)
    pair["status"] = "completed"
    pair["completed_at"] = result["finished_at"]
    atomic_json(pair_path, pair)
    return {"status": "completed", **result, "pair_dir": str(pair_dir)}
