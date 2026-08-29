from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .policies import heuristic_actions
from .runner import DEFAULT_SCENARIO, ROOT, create_pair, create_run, read_json, step_pair, step_run
from .simulator import VendingSimulator, stable_hash
from .verify import verify_pair


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def simulate_policy(args: argparse.Namespace) -> None:
    scenario = read_json(DEFAULT_SCENARIO)
    scenario["days"] = args.days
    simulator = VendingSimulator(scenario, args.seed)
    turns = []
    while not simulator.state["terminated"]:
        before = simulator.state["day"]
        actions = heuristic_actions(simulator.public_view(), arm=args.arm)
        applied = simulator.apply_turn(actions)
        turns.append({
            "turn": len(turns),
            "day_before": before,
            "day_after": simulator.state["day"],
            "accepted": len(applied.accepted),
            "rejected": len(applied.rejected),
            "state_hash": applied.state_hash,
        })
    emit({
        "mode": "deterministic_policy_validation",
        "arm": args.arm,
        "seed": args.seed,
        "turns": len(turns),
        "score": simulator.score(),
        "final_state_hash": stable_hash(simulator.state),
        "final_state": simulator.public_view() if args.include_state else None,
    })


def create(args: argparse.Namespace) -> None:
    path = create_run(
        arm=args.arm,
        seed=args.seed,
        days=args.days,
        model=args.model,
        agent_id=args.agent,
        thinking=args.thinking,
    )
    emit({"status": "created", "run_dir": str(path), "manifest": read_json(path / "manifest.json")})


def step(args: argparse.Namespace) -> None:
    emit(step_run(Path(args.run), daily_token_budget=args.daily_token_budget))


def batch(args: argparse.Namespace) -> None:
    results = []
    for _ in range(args.max_role_calls):
        result = step_run(Path(args.run), daily_token_budget=args.daily_token_budget)
        results.append(result)
        if result["status"] in ("completed", "paused_quota"):
            break
    emit({"calls_attempted": len(results), "last": results[-1], "history": results})


def status(args: argparse.Namespace) -> None:
    run = Path(args.run)
    result = {
        "manifest": read_json(run / "manifest.json"),
        "flow": read_json(run / "flow.json"),
        "state": read_json(run / "state.json"),
    }
    if (run / "result.json").exists():
        result["result"] = read_json(run / "result.json")
    emit(result)


def create_pair_cmd(args: argparse.Namespace) -> None:
    path = create_pair(args.seed, args.days, args.model, args.agent, args.thinking)
    emit({"status": "created", "pair_dir": str(path), "pair": read_json(path / "pair.json")})


def pair_batch(args: argparse.Namespace) -> None:
    results = []
    for _ in range(args.max_role_calls):
        result = step_pair(Path(args.pair), daily_token_budget=args.daily_token_budget)
        results.append(result)
        status_value = result.get("status") or result.get("pair_status")
        if status_value in ("completed", "paused_quota"):
            break
    emit({"calls_attempted": len(results), "last": results[-1], "history": results})


def pair_status(args: argparse.Namespace) -> None:
    pair = Path(args.pair)
    manifest = read_json(pair / "pair.json")
    emit({
        "pair": manifest,
        "control": {
            "flow": read_json(Path(manifest["control_run"]) / "flow.json"),
            "state": read_json(Path(manifest["control_run"]) / "state.json"),
        },
        "theatre": {
            "flow": read_json(Path(manifest["theatre_run"]) / "flow.json"),
            "state": read_json(Path(manifest["theatre_run"]) / "state.json"),
        },
        "result": read_json(pair / "result.json") if (pair / "result.json").exists() else None,
    })


def verify_pair_cmd(args: argparse.Namespace) -> None:
    result = verify_pair(Path(args.pair), Path(args.ledger) if args.ledger else None)
    emit(result)
    if result["status"] != "passed":
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="business-bench")
    sub = parser.add_subparsers(dest="command", required=True)

    policy = sub.add_parser("simulate-policy", help="validate economics without model calls")
    policy.add_argument("--arm", choices=("control", "theatre"), required=True)
    policy.add_argument("--seed", type=int, required=True)
    policy.add_argument("--days", type=int, default=365)
    policy.add_argument("--include-state", action="store_true")
    policy.set_defaults(func=simulate_policy)

    create_cmd = sub.add_parser("create-run", help="create a durable Codex benchmark run")
    create_cmd.add_argument("--arm", choices=("control", "theatre"), required=True)
    create_cmd.add_argument("--seed", type=int, required=True)
    create_cmd.add_argument("--days", type=int, default=365)
    create_cmd.add_argument("--model", default="openai/gpt-5.6-sol")
    create_cmd.add_argument("--agent", default="business-bench")
    create_cmd.add_argument("--thinking", choices=("low", "medium", "high", "xhigh"), default="medium")
    create_cmd.set_defaults(func=create)

    step_cmd = sub.add_parser("step", help="execute one durable role call or business turn")
    step_cmd.add_argument("--run", required=True)
    step_cmd.add_argument("--daily-token-budget", type=int, default=500_000)
    step_cmd.set_defaults(func=step)

    batch_cmd = sub.add_parser("batch", help="execute several role calls, stopping at quota")
    batch_cmd.add_argument("--run", required=True)
    batch_cmd.add_argument("--max-role-calls", type=int, default=10)
    batch_cmd.add_argument("--daily-token-budget", type=int, default=500_000)
    batch_cmd.set_defaults(func=batch)

    status_cmd = sub.add_parser("status", help="inspect a durable run")
    status_cmd.add_argument("--run", required=True)
    status_cmd.set_defaults(func=status)

    pair_create = sub.add_parser("create-pair", help="create paired control and Theatre runs")
    pair_create.add_argument("--seed", type=int, required=True)
    pair_create.add_argument("--days", type=int, default=365)
    pair_create.add_argument("--model", default="openai/gpt-5.6-sol")
    pair_create.add_argument("--agent", default="business-bench")
    pair_create.add_argument("--thinking", choices=("low", "medium", "high", "xhigh"), default="medium")
    pair_create.set_defaults(func=create_pair_cmd)

    pair_run = sub.add_parser("pair-batch", help="advance a pair fairly until quota or call limit")
    pair_run.add_argument("--pair", required=True)
    pair_run.add_argument("--max-role-calls", type=int, default=10)
    pair_run.add_argument("--daily-token-budget", type=int, default=500_000)
    pair_run.set_defaults(func=pair_batch)

    pair_show = sub.add_parser("pair-status", help="inspect both arms of a pair")
    pair_show.add_argument("--pair", required=True)
    pair_show.set_defaults(func=pair_status)

    pair_verify = sub.add_parser("verify-pair", help="replay and audit a durable paired run")
    pair_verify.add_argument("--pair", required=True)
    pair_verify.add_argument("--ledger", help="override the global provider-usage ledger path")
    pair_verify.set_defaults(func=verify_pair_cmd)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
