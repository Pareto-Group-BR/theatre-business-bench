from __future__ import annotations

import json
from pathlib import Path

from theatre_business_bench.runner import atomic_json, create_pair, finalize_pair, finalize_run, read_json
from theatre_business_bench.simulator import VendingSimulator, stable_hash


def make_pair_fixture(root: Path, *, days: int = 28, complete: bool = False) -> tuple[Path, Path]:
    pair_dir = create_pair(seed=91, days=days, run_root=root)
    pair = read_json(pair_dir / "pair.json")
    ledger = root / "usage-ledger.jsonl"
    ledger_rows = []
    for arm in ("control", "theatre"):
        run = Path(pair[f"{arm}_run"])
        manifest = read_json(run / "manifest.json")
        scenario = read_json(run / "scenario.json")
        simulator = VendingSimulator(scenario, seed=91)
        roles = ("control",) if arm == "control" else ("critic", "planner", "actor")
        decisions = []
        usages = []
        for role in roles:
            content = (
                {"actions": [{"type": "research_supplier", "supplier": "metro"}]}
                if role in ("control", "actor")
                else {"summary": role}
            )
            decisions.append({
                "timestamp": "2026-08-29T00:00:00+00:00",
                "turn_index": 0,
                "role": role,
                "content": content,
                "response_hash": stable_hash(content),
            })
            usages.append({
                "timestamp": "2026-08-29T00:00:00+00:00",
                "run_id": manifest["run_id"],
                "arm": arm,
                "seed": 91,
                "role": role,
                "gateway_run_id": f"gateway-{arm}-{role}",
                "session_id": f"session-{arm}-{role}",
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "duration_ms": 10,
                "usage": {"input": 100, "cache_read": 20, "cache_write": 0, "output": 10, "total": 130},
                "response_hash": stable_hash(content),
            })
        business_content = decisions[-1]["content"]
        applied = simulator.apply_turn(business_content["actions"])
        turn = {
            "timestamp": "2026-08-29T00:00:00+00:00",
            "turn_index": 0,
            "day_before": 0,
            "day_after": simulator.state["day"],
            "role": roles[-1],
            "accepted": applied.accepted,
            "rejected": applied.rejected,
            "state_hash": applied.state_hash,
        }
        (run / "model-decisions.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in decisions), encoding="utf-8"
        )
        (run / "usage.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in usages), encoding="utf-8"
        )
        (run / "turns.jsonl").write_text(json.dumps(turn) + "\n", encoding="utf-8")
        atomic_json(run / "state.json", simulator.state)
        flow = read_json(run / "flow.json")
        flow["turn_index"] = 1
        if complete:
            if not simulator.state["terminated"]:
                raise AssertionError("completed fixture requires a horizon reached by one turn")
            flow["status"] = "completed"
            flow["current_step"] = "finalize"
        atomic_json(run / "flow.json", flow)
        if complete:
            finalize_run(run)
        ledger_rows.extend(usages)
    ledger.write_text("".join(json.dumps(row) + "\n" for row in ledger_rows), encoding="utf-8")
    if complete:
        finalize_pair(pair_dir)
    return pair_dir, ledger
