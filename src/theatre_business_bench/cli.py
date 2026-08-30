from __future__ import annotations

import argparse
import fcntl
import itertools
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .policies import heuristic_actions
from .causal import (
    CausalGateError,
    create_causal_fork,
    create_v2_preregistration,
    verify_causal_fork,
)
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


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


@contextmanager
def exclusive_runner_lock(path: Path):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CausalGateError(f"shared runner lock is busy: {path}") from exc
        yield path


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
    calls = range(args.max_role_calls) if args.max_role_calls is not None else itertools.count()
    for _ in calls:
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


def create_causal_fork_cmd(args: argparse.Namespace) -> None:
    try:
        with exclusive_runner_lock(Path(args.shared_lock)):
            path = create_causal_fork(
                Path(args.source_pair),
                human_will=args.will,
                hypothesis=args.hypothesis,
                output_root=Path(args.output_root) if args.output_root else None,
                ledger_path=Path(args.ledger) if args.ledger else None,
            )
            report = verify_causal_fork(path, Path(args.ledger) if args.ledger else None)
    except CausalGateError as exc:
        emit({"status": "causal_gate_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit({"status": "created_non_scoring", "fork_dir": str(path), "verification": report})


def verify_causal_fork_cmd(args: argparse.Namespace) -> None:
    report = verify_causal_fork(Path(args.fork), Path(args.ledger) if args.ledger else None)
    emit(report)
    if report["status"] != "passed":
        raise SystemExit(1)


def causal_batch_cmd(args: argparse.Namespace) -> None:
    if not args.confirm_non_scoring:
        emit({"status": "causal_gate_failed", "error": "--confirm-non-scoring is required"})
        raise SystemExit(1)
    fork = Path(args.fork).resolve()
    if args.max_role_calls is not None and args.max_role_calls < 1:
        emit({"status": "causal_gate_failed", "error": "--max-role-calls must be positive"})
        raise SystemExit(1)
    try:
        with exclusive_runner_lock(Path(args.shared_lock)):
            report = verify_causal_fork(fork, Path(args.ledger) if args.ledger else None)
            if report["status"] != "passed":
                emit({"status": "causal_gate_failed", "verification": report})
                raise SystemExit(1)
            results = []
            calls = range(args.max_role_calls) if args.max_role_calls is not None else itertools.count()
            for _ in calls:
                result = step_run(
                    Path(report["active_run"]),
                    daily_token_budget=args.daily_token_budget,
                    allow_non_scoring=True,
                )
                results.append(result)
                if result["status"] in ("completed", "paused_quota"):
                    break
    except CausalGateError as exc:
        emit({"status": "shared_runner_busy", "error": str(exc)})
        raise SystemExit(2) from exc
    emit({
        "status": "non_scoring_exploration",
        "scoring_eligible": False,
        "calls_attempted": len(results),
        "last": results[-1],
        "history": results,
    })


def preregister_v2_cmd(args: argparse.Namespace) -> None:
    try:
        path = create_v2_preregistration(
            Path(args.fork),
            seeds=args.seeds,
            scenario_path=Path(args.scenario),
            prompt_dir=Path(args.prompt_dir),
            protocol_path=Path(args.protocol),
            output_path=Path(args.output),
            runs_root=Path(args.runs_root) if args.runs_root else None,
            ledger_path=Path(args.ledger) if args.ledger else None,
        )
    except CausalGateError as exc:
        emit({"status": "preregistration_gate_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit({"status": "preregistered_not_started", "path": str(path), "registration": read_json(path)})


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

    fork_create = sub.add_parser(
        "create-causal-fork",
        help="clone a verified Theatre checkpoint into an isolated non-scoring lane",
    )
    fork_create.add_argument("--source-pair", required=True)
    fork_create.add_argument("--shared-lock", required=True)
    fork_create.add_argument("--will", required=True, help="operator-supplied Consciousness directive")
    fork_create.add_argument("--hypothesis", required=True)
    fork_create.add_argument("--output-root")
    fork_create.add_argument("--ledger")
    fork_create.set_defaults(func=create_causal_fork_cmd)

    fork_verify = sub.add_parser("verify-causal-fork", help="audit provenance, replay and isolation")
    fork_verify.add_argument("--fork", required=True)
    fork_verify.add_argument("--ledger")
    fork_verify.set_defaults(func=verify_causal_fork_cmd)

    fork_run = sub.add_parser(
        "causal-batch",
        help="advance a verified exploratory fork under the shared runner lock",
    )
    fork_run.add_argument("--fork", required=True)
    fork_run.add_argument("--shared-lock", required=True)
    fork_run.add_argument("--confirm-non-scoring", action="store_true")
    fork_run.add_argument("--max-role-calls", type=int)
    fork_run.add_argument("--daily-token-budget", type=int)
    fork_run.add_argument("--ledger")
    fork_run.set_defaults(func=causal_batch_cmd)

    preregister = sub.add_parser(
        "preregister-v2",
        help="freeze a five-seed v2 only after a completed exploratory fork",
    )
    preregister.add_argument("--fork", required=True)
    preregister.add_argument("--seeds", nargs=5, type=int, required=True)
    preregister.add_argument("--scenario", required=True)
    preregister.add_argument("--prompt-dir", required=True)
    preregister.add_argument("--protocol", required=True)
    preregister.add_argument("--output", required=True)
    preregister.add_argument("--runs-root")
    preregister.add_argument("--ledger")
    preregister.set_defaults(func=preregister_v2_cmd)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
