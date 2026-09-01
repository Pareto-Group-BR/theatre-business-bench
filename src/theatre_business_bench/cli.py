from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path
from typing import Any

from .campaign import build_v2_terminal_campaign, write_v2_campaign_bundle
from .evidence import (
    reconcile_openclaw_failures,
    reconcile_openclaw_v3_gateway_restart,
    reconcile_openclaw_v3_undispatched_attempt,
)
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
from .v2 import V2ContractError, activate_v2_pair, audit_preregistration
from .v3 import audit_preregistration as audit_v3_preregistration
from .v3 import V3ContractError, activate_v3_pair
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
    path = create_pair(
        args.seed,
        args.days,
        args.model,
        args.agent,
        args.thinking,
        run_root=Path(args.run_root) if args.run_root else None,
        protocol=args.protocol,
    )
    emit({"status": "created", "pair_dir": str(path), "pair": read_json(path / "pair.json")})


def pair_batch(args: argparse.Namespace) -> None:
    pair_dir = Path(args.pair)
    integrity = verify_pair(pair_dir)
    if integrity["status"] != "passed":
        emit({"status": "integrity_failed", "verification": integrity})
        raise SystemExit(1)
    pair = read_json(pair_dir / "pair.json")
    if pair.get("official") is True and pair.get("inference_enabled") is True:
        lock_fd = os.environ.get("THEATRE_OFFICIAL_LOCK_FD")
        try:
            if lock_fd is None:
                raise ValueError("missing")
            os.fstat(int(lock_fd))
        except (ValueError, OSError):
            emit({
                "status": "lock_required",
                "reason": "official inference must run through the canonical global-lock wrapper",
            })
            raise SystemExit(1)
    results = []
    calls = range(args.max_role_calls) if args.max_role_calls is not None else itertools.count()
    for _ in calls:
        result = step_pair(pair_dir, daily_token_budget=args.daily_token_budget)
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


def audit_v2_cmd(args: argparse.Namespace) -> None:
    result = (
        audit_preregistration(Path(args.preregistration))
        if args.preregistration
        else audit_preregistration()
    )
    emit(result)
    if result["status"] != "passed":
        raise SystemExit(1)


def audit_v3_cmd(args: argparse.Namespace) -> None:
    result = (
        audit_v3_preregistration(Path(args.preregistration))
        if args.preregistration
        else audit_v3_preregistration()
    )
    emit(result)
    if result["status"] != "passed":
        raise SystemExit(1)


def activate_v2_cmd(args: argparse.Namespace) -> None:
    try:
        receipt = activate_v2_pair(Path(args.pair), args.source_commit)
    except V2ContractError as exc:
        emit({"status": "activation_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit({"status": "activated", "pair_dir": str(Path(args.pair).resolve()), "receipt": receipt})


def activate_v3_cmd(args: argparse.Namespace) -> None:
    try:
        receipt = activate_v3_pair(Path(args.pair), args.source_commit)
    except V3ContractError as exc:
        emit({"status": "activation_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit({"status": "activated", "pair_dir": str(Path(args.pair).resolve()), "receipt": receipt})


def reconcile_failures_cmd(args: argparse.Namespace) -> None:
    try:
        receipt = reconcile_openclaw_failures(
            Path(args.pair), args.arm, Path(args.trajectory), args.gateway_run_id
        )
    except V2ContractError as exc:
        emit({"status": "reconciliation_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit(receipt)


def reconcile_v3_restart_cmd(args: argparse.Namespace) -> None:
    try:
        receipt = reconcile_openclaw_v3_gateway_restart(
            Path(args.pair),
            args.arm,
            Path(args.trajectory),
            Path(args.session_log),
            args.interrupted_gateway_run_id,
            args.completed_gateway_run_id,
        )
    except (V2ContractError, V3ContractError) as exc:
        emit({"status": "reconciliation_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit(receipt)


def reconcile_v3_undispatched_cmd(args: argparse.Namespace) -> None:
    try:
        receipt = reconcile_openclaw_v3_undispatched_attempt(
            Path(args.pair),
            args.arm,
            Path(args.trajectory),
            Path(args.session_log),
        )
    except (V2ContractError, V3ContractError) as exc:
        emit({"status": "reconciliation_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit(receipt)


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


def render_v2_campaign_cmd(args: argparse.Namespace) -> None:
    try:
        report = build_v2_terminal_campaign(
            Path(args.run_root),
            preregistration=Path(args.preregistration) if args.preregistration else None,
            ledger_path=Path(args.ledger) if args.ledger else None,
        )
        write_v2_campaign_bundle(
            Path(args.run_root),
            report,
            json_out=Path(args.json_out) if args.json_out else None,
            markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            html_out=Path(args.html_out) if args.html_out else None,
        )
    except ReportGateError as exc:
        emit({"status": "campaign_gate_failed", "error": str(exc)})
        raise SystemExit(1) from exc
    emit(report)


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
    pair_create.add_argument("--protocol", choices=("v1", "v2", "v3"), default="v1")
    pair_create.add_argument("--run-root", help="store pair, runs, and provider ledger under this root")
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

    v2_audit = sub.add_parser(
        "audit-v2-preregistration",
        help="verify frozen v2 parity, exclusions, seeds, and artifact hashes without inference",
    )
    v2_audit.add_argument("--preregistration", help="override preregistration/v2.json for audit testing")
    v2_audit.set_defaults(func=audit_v2_cmd)

    v3_audit = sub.add_parser(
        "audit-v3-preregistration",
        help="verify frozen v3 parity, timing classes, bounded repair, seeds, and artifact hashes",
    )
    v3_audit.add_argument("--preregistration", help="override preregistration/v3.json for audit testing")
    v3_audit.set_defaults(func=audit_v3_cmd)

    v2_activate = sub.add_parser(
        "activate-v2-pair",
        help="enable one untouched v2 pair from the exact clean source published at origin/main",
    )
    v2_activate.add_argument("--pair", required=True)
    v2_activate.add_argument("--source-commit", required=True)
    v2_activate.set_defaults(func=activate_v2_cmd)

    v3_activate = sub.add_parser(
        "activate-v3-pair",
        help="enable one untouched v3 pair from the exact clean source published at origin/main",
    )
    v3_activate.add_argument("--pair", required=True)
    v3_activate.add_argument("--source-commit", required=True)
    v3_activate.set_defaults(func=activate_v3_cmd)

    reconcile = sub.add_parser(
        "reconcile-openclaw-failures",
        help="import pre-fix invalid provider calls from an auditable OpenClaw trajectory",
    )
    reconcile.add_argument("--pair", required=True)
    reconcile.add_argument("--arm", choices=("control", "theatre"), required=True)
    reconcile.add_argument("--trajectory", required=True)
    reconcile.add_argument("--gateway-run-id", action="append", required=True)
    reconcile.set_defaults(func=reconcile_failures_cmd)

    reconcile_v3 = sub.add_parser(
        "reconcile-openclaw-v3-gateway-restart",
        help="terminally preserve and charge one v3 repair auto-continued after a gateway restart",
    )
    reconcile_v3.add_argument("--pair", required=True)
    reconcile_v3.add_argument("--arm", choices=("control", "theatre"), required=True)
    reconcile_v3.add_argument("--trajectory", required=True)
    reconcile_v3.add_argument("--session-log", required=True)
    reconcile_v3.add_argument("--interrupted-gateway-run-id", required=True)
    reconcile_v3.add_argument("--completed-gateway-run-id", required=True)
    reconcile_v3.set_defaults(func=reconcile_v3_restart_cmd)

    reconcile_undispatched = sub.add_parser(
        "reconcile-openclaw-v3-undispatched-attempt",
        help="terminalize one v3 write-ahead attempt with no observed OpenClaw dispatch",
    )
    reconcile_undispatched.add_argument("--pair", required=True)
    reconcile_undispatched.add_argument(
        "--arm", choices=("control", "theatre"), required=True
    )
    reconcile_undispatched.add_argument("--trajectory", required=True)
    reconcile_undispatched.add_argument("--session-log", required=True)
    reconcile_undispatched.set_defaults(func=reconcile_v3_undispatched_cmd)

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

    campaign_report = sub.add_parser(
        "render-v2-campaign",
        help="render reliability evidence for a verified terminal v2 campaign",
    )
    campaign_report.add_argument("--run-root", required=True)
    campaign_report.add_argument("--preregistration", help="override preregistration/v2.json")
    campaign_report.add_argument("--ledger", help="override the global provider-usage ledger path")
    campaign_report.add_argument("--json-out", help="write canonical campaign evidence JSON")
    campaign_report.add_argument("--markdown-out", help="write campaign evidence Markdown")
    campaign_report.add_argument("--html-out", help="write standalone campaign evidence HTML")
    campaign_report.set_defaults(func=render_v2_campaign_cmd)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
