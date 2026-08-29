from __future__ import annotations

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


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "scenarios" / "vending_v1.json"
PROMPT_FILES = {
    "control": ROOT / "prompts" / "control.md",
    "critic": ROOT / "prompts" / "critic.md",
    "planner": ROOT / "prompts" / "planner.md",
    "actor": ROOT / "prompts" / "actor.md",
}


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


def prompt_hashes() -> dict[str, str]:
    return {role: stable_hash(path.read_text(encoding="utf-8")) for role, path in PROMPT_FILES.items()}


def make_run_id(arm: str, seed: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{arm}-s{seed}"


@dataclass
class TokenBudget:
    ledger_path: Path
    daily_limit: int
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
) -> Path:
    if arm not in ("control", "theatre"):
        raise ValueError("arm must be control or theatre")
    scenario = read_json(DEFAULT_SCENARIO)
    if days is not None:
        scenario["days"] = int(days)
    run_root = run_root or ROOT / "runs"
    run_dir = run_root / make_run_id(arm, seed)
    run_dir.mkdir(parents=True, exist_ok=False)
    atomic_json(run_dir / "scenario.json", scenario)
    for role, prompt_path in PROMPT_FILES.items():
        shutil.copy2(prompt_path, run_dir / f"prompt-{role}.md")
    simulator = VendingSimulator(scenario, seed)
    atomic_json(run_dir / "state.json", simulator.state)
    manifest = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "arm": arm,
        "seed": int(seed),
        "model": model,
        "agent_id": agent_id,
        "thinking": thinking,
        "scenario_hash": stable_hash(scenario),
        "prompt_hashes": prompt_hashes(),
        "decision_period_days": scenario["decision_period_days"],
        "theatre_review_every_turns": max(1, round(28 / scenario["decision_period_days"])),
        "virtual_output_cost_per_million_tokens": scenario["virtual_output_cost_per_million_tokens"],
        "official": False,
        "status": "ready",
    }
    atomic_json(run_dir / "manifest.json", manifest)
    atomic_json(run_dir / "role-memory.json", {"critic": None, "planner": None, "actor": None, "control": None})
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


def _role_message(role: str, view: dict[str, Any], flow: dict[str, Any], memories: dict[str, Any]) -> str:
    prompt = (ROOT / "prompts" / f"{role}.md").read_text(encoding="utf-8")
    context: dict[str, Any] = {"business_state": view}
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


def _record_model_result(run_dir: Path, manifest: dict[str, Any], role: str, result: ModelResult) -> None:
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
        "response_hash": stable_hash(result.content),
    }
    append_jsonl(run_dir / "usage.jsonl", row)
    append_jsonl(ROOT / "runs" / "usage-ledger.jsonl", row)


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
    result = transport.invoke(_session_key(manifest, role), _role_message(role, view, flow, memories))
    if result.provider != "openai" or result.model != manifest["model"].split("/", 1)[-1]:
        raise RuntimeError(f"model drift detected: {result.provider}/{result.model}")
    _record_model_result(run_dir, manifest, role, result)
    append_jsonl(run_dir / "model-decisions.jsonl", {
        "timestamp": utc_now(),
        "turn_index": flow["turn_index"],
        "role": role,
        "content": result.content,
        "response_hash": stable_hash(result.content),
    })
    return result.content


def _output_tokens(run_dir: Path) -> int:
    path = run_dir / "usage.jsonl"
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(int(json.loads(line)["usage"].get("output", 0)) for line in handle if line.strip())


def step_run(run_dir: Path, daily_token_budget: int = 500_000) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "manifest.json")
    scenario = read_json(run_dir / "scenario.json")
    state = read_json(run_dir / "state.json")
    flow = read_json(run_dir / "flow.json")
    memories = read_json(run_dir / "role-memory.json")
    simulator = VendingSimulator(scenario, manifest["seed"], state=state)
    if simulator.state["terminated"]:
        return finalize_run(run_dir)
    transport = OpenClawCodexTransport(
        agent_id=manifest["agent_id"], model=manifest["model"], thinking=manifest["thinking"]
    )
    budget = TokenBudget(ROOT / "runs" / "usage-ledger.jsonl", daily_token_budget)
    view = simulator.public_view()

    if flow["current_step"] == "prepare_turn":
        review_required = manifest["arm"] == "theatre" and (
            flow["turn_index"] % manifest["theatre_review_every_turns"] == 0 or _critical_event(view)
        )
        flow.update({
            "status": "running",
            "phase": "critic" if review_required else ("actor" if manifest["arm"] == "theatre" else "control"),
            "review_required": review_required,
            "pending": {},
            "current_step": "model_roles",
            "updated_at": utc_now(),
        })
        atomic_json(run_dir / "flow.json", flow)

    try:
        if flow["phase"] == "critic":
            flow["pending"]["critic"] = _invoke_role(run_dir, manifest, "critic", view, flow, memories, budget, transport)
            memories["critic"] = flow["pending"]["critic"]
            flow["phase"] = "planner"
            flow["updated_at"] = utc_now()
            atomic_json(run_dir / "role-memory.json", memories)
            atomic_json(run_dir / "flow.json", flow)
            return {"status": "running", "completed_role": "critic", "next_role": "planner", "run_dir": str(run_dir)}
        if flow["phase"] == "planner":
            flow["pending"]["planner"] = _invoke_role(run_dir, manifest, "planner", view, flow, memories, budget, transport)
            memories["planner"] = flow["pending"]["planner"]
            flow["phase"] = "actor"
            flow["updated_at"] = utc_now()
            atomic_json(run_dir / "role-memory.json", memories)
            atomic_json(run_dir / "flow.json", flow)
            return {"status": "running", "completed_role": "planner", "next_role": "actor", "run_dir": str(run_dir)}
        role = "actor" if manifest["arm"] == "theatre" else "control"
        decision = _invoke_role(run_dir, manifest, role, view, flow, memories, budget, transport)
        memories[role] = decision
        actions = decision.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        applied = simulator.apply_turn(actions)
        append_jsonl(run_dir / "turns.jsonl", {
            "timestamp": utc_now(),
            "turn_index": flow["turn_index"],
            "day_before": view["day"],
            "day_after": simulator.state["day"],
            "role": role,
            "accepted": applied.accepted,
            "rejected": applied.rejected,
            "state_hash": applied.state_hash,
        })
        atomic_json(run_dir / "state.json", simulator.state)
        atomic_json(run_dir / "role-memory.json", memories)
        flow.update({
            "status": "completed" if simulator.state["terminated"] else "ready",
            "current_step": "finalize" if simulator.state["terminated"] else "prepare_turn",
            "turn_index": flow["turn_index"] + 1,
            "phase": None,
            "review_required": None,
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
            "next_role": "critic" if manifest["arm"] == "theatre" and flow["turn_index"] % manifest["theatre_review_every_turns"] == 0 else role,
            "run_dir": str(run_dir),
        }
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
) -> Path:
    pair_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-pair-s{seed}"
    run_root = run_root or ROOT / "runs"
    pair_dir = run_root / "pairs" / pair_id
    pair_dir.mkdir(parents=True, exist_ok=False)
    control = create_run("control", seed, days, run_root=run_root, model=model, agent_id=agent_id, thinking=thinking)
    theatre = create_run("theatre", seed, days, run_root=run_root, model=model, agent_id=agent_id, thinking=thinking)
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
        "next_arm": "control",
        "status": "ready",
        "official": False,
    }
    atomic_json(pair_dir / "pair.json", pair)
    return pair_dir


def _run_progress(run_dir: Path) -> tuple[int, bool]:
    state = read_json(run_dir / "state.json")
    return int(state["day"]), bool(state["terminated"])


def step_pair(pair_dir: Path, daily_token_budget: int = 500_000) -> dict[str, Any]:
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
    pair["next_arm"] = "theatre" if arm == "control" else "control"
    pair["status"] = result["status"] if result["status"] == "paused_quota" else "running"
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
