from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any

from .policies import heuristic_actions
from .report import (
    ReportGateError,
    build_executive_report,
    build_live_cockpit,
    write_live_cockpit,
    write_report_bundle,
)
from .runner import DEFAULT_SCENARIO, ROOT, create_pair, create_run, read_json, step_pair, step_run
from .simulator import VendingSimulator, stable_hash
from .verify import verify_pair
from .v2 import V2ContractError, activate_v2_pair, verify_v2_preregistration


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
        protocol=args.protocol,
        run_root=Path(args.run_root) if args.run_root else None,
    )
    emit({"status": "created", "run_dir": str(path), "manifest": read_json(path / "manifest.json")})


def step(args: argparse.Namespace) -> None:
    emit(step_run(Path(args.run), daily_token_budget=args.daily_token_budget))


def batch(args: argparse.Namespace) -> None:
    results = []
    calls = range(args.max_role_calls) if args.max_role_calls is not None else itertools.count()
    for _ in calls:
        result = step_run(Path(args.run), daily_token_budget=args.daily_token_budget)
        results.append(result)
        if result["status"] in ("completed", "paused_quota", "failed_contract", "blocked_preregistration"):
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
    try:
        path = create_pair(
            args.seed,
            args.days,
            args.model,
            args.agent,
            args.thinking,
            run_root=Path(args.run_root) if args.run_root else None,
            protocol=args.protocol,
        )
    except V2ContractError as exc:
        emit({"status": "v2_creation_refused", "error": str(exc)})
        raise SystemExit(1) from exc
    emit({"status": "created", "pair_dir": str(path), "pair": read_json(path / "pair.json")})


def pair_batch(args: argparse.Namespace) -> None:
    integrity = verify_pair(Path(args.pair))
    if integrity["status"] != "passed":
        emit({"status": "integrity_failed", "verification": integrity})
        raise SystemExit(1)
    results = []
    calls = range(args.max_role_calls) if args.max_role_calls is not None else itertools.count()
    for _ in calls:
        result = step_pair(Path(args.pair), daily_token_budget=args.daily_token_budget)
        results.append(result)
        status_value = result.get("status") or result.get("pair_status")
        if status_value in ("completed", "paused_quota", "failed_contract", "blocked_preregistration"):
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


def verify_v2_preregistration_cmd(args: argparse.Namespace) -> None:
    result = verify_v2_preregistration()
    emit(result)
    if result["status"] != "passed":
        raise SystemExit(1)


def activate_v2_pair_cmd(args: argparse.Namespace) -> None:
    try:
        receipt = activate_v2_pair(Path(args.pair), args.source_commit)
    except V2ContractError as exc:
        emit({"status": "v2_activation_refused", "error": str(exc)})
        raise SystemExit(1) from exc
    emit({"status": "activated", "pair": args.pair, "receipt": receipt})


def render_report_cmd(args: argparse.Namespace) -> None:
    try:
        report = build_executive_report(
            Path(args.pair),
            Path(args.ledger) if args.ledger else None,
        )
        write_report_bundle(
            Path(args.pair),
            report,
            json_out=Path(args.json_out) if args.json_out else None,
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            html_out=Path(args.html_out) if args.html_out else None,
        )
    except ReportGateError as exc:
        emit({"status": "report_gate_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit(report)


def render_cockpit_cmd(args: argparse.Namespace) -> None:
    try:
        cockpit = build_live_cockpit(
            Path(args.pair),
            Path(args.ledger) if args.ledger else None,
        )
        if args.json_out:
            write_live_cockpit(Path(args.pair), cockpit, Path(args.json_out))
    except ReportGateError as exc:
        emit({"status": "cockpit_gate_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit(cockpit)


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
    create_cmd.add_argument("--protocol", choices=("v1", "v2"), default="v1")
    create_cmd.add_argument("--run-root", help="override the durable runs root")
    create_cmd.set_defaults(func=create)

    step_cmd = sub.add_parser("step", help="execute one durable role call or business turn")
    step_cmd.add_argument("--run", required=True)
    step_cmd.add_argument(
        "--daily-token-budget",
        type=int,
        help="optional local safety ceiling; omitted means use the provider's real quota",
    )
    step_cmd.set_defaults(func=step)

    batch_cmd = sub.add_parser("batch", help="advance a run until completion or provider quota")
    batch_cmd.add_argument("--run", required=True)
    batch_cmd.add_argument("--max-role-calls", type=int, help="optional diagnostic call cap")
    batch_cmd.add_argument(
        "--daily-token-budget",
        type=int,
        help="optional local safety ceiling; omitted means use the provider's real quota",
    )
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
    pair_create.add_argument("--protocol", choices=("v1", "v2"), default="v1")
    pair_create.add_argument("--run-root", help="override the durable runs root")
    pair_create.set_defaults(func=create_pair_cmd)

    pair_run = sub.add_parser("pair-batch", help="advance a pair fairly until completion or provider quota")
    pair_run.add_argument("--pair", required=True)
    pair_run.add_argument("--max-role-calls", type=int, help="optional diagnostic call cap")
    pair_run.add_argument(
        "--daily-token-budget",
        type=int,
        help="optional local safety ceiling; omitted means use the provider's real quota",
    )
    pair_run.set_defaults(func=pair_batch)

    pair_show = sub.add_parser("pair-status", help="inspect both arms of a pair")
    pair_show.add_argument("--pair", required=True)
    pair_show.set_defaults(func=pair_status)

    pair_verify = sub.add_parser("verify-pair", help="replay and audit a durable paired run")
    pair_verify.add_argument("--pair", required=True)
    pair_verify.add_argument("--ledger", help="override the global provider-usage ledger path")
    pair_verify.set_defaults(func=verify_pair_cmd)

    v2_preregister = sub.add_parser(
        "verify-v2-preregistration",
        help="verify the frozen autonomous v2 design without model calls",
    )
    v2_preregister.set_defaults(func=verify_v2_preregistration_cmd)

    v2_activate = sub.add_parser(
        "activate-v2-pair",
        help="bind a ready offline v2 pair to the exact clean published source commit",
    )
    v2_activate.add_argument("--pair", required=True)
    v2_activate.add_argument("--source-commit", required=True)
    v2_activate.set_defaults(func=activate_v2_pair_cmd)

    pair_report = sub.add_parser(
        "render-report",
        help="render deterministic executive evidence from a completed verified pair",
    )
    pair_report.add_argument("--pair", required=True)
    pair_report.add_argument("--ledger", help="override the global provider-usage ledger path")
    pair_report.add_argument("--json-out", help="write the canonical machine-readable report")
    pair_report.add_argument("--markdown-out", help="write the executive Markdown report")
    pair_report.add_argument("--html-out", help="write the standalone executive HTML report")
    pair_report.set_defaults(func=render_report_cmd)

    pair_cockpit = sub.add_parser(
        "render-cockpit",
        help="render a verified live financial and business cockpit",
    )
    pair_cockpit.add_argument("--pair", required=True)
    pair_cockpit.add_argument("--ledger", help="override the global provider-usage ledger path")
    pair_cockpit.add_argument("--json-out", help="write the live cockpit JSON")
    pair_cockpit.set_defaults(func=render_cockpit_cmd)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
