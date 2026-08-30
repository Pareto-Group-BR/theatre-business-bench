from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .runner import PROMPT_FILES, ROOT, atomic_json, read_json, utc_now
from .simulator import stable_hash
from .verify import verify_pair, verify_run


FORK_SCHEMA = "theatre-business-bench-causal-fork/v1"
INTERVENTION_SCHEMA = "theatre-business-bench-consciousness-intervention/v1"
PREREGISTRATION_SCHEMA = "theatre-business-bench-v2-preregistration/v1"
NON_SCORING_CLASSIFICATION = "assisted_exploratory_non_scoring"


class CausalGateError(ValueError):
    """Raised before a causal artifact can cross an honesty boundary."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _files_digest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CausalGateError(f"source checkpoint contains symlink: {path.relative_to(root)}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = _sha256_file(path)
        elif not path.is_dir():
            raise CausalGateError(f"source checkpoint contains unsupported entry: {path.relative_to(root)}")
    return result


def _member(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise CausalGateError("fork member path must be relative")
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CausalGateError("fork member escapes its root") from exc
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise CausalGateError(f"fork member traverses symlink: {relative}")
    return candidate


def _source_run(pair_dir: Path, arm: str = "theatre") -> tuple[dict[str, Any], Path]:
    pair = read_json(pair_dir / "pair.json")
    value = pair.get(f"{arm}_run")
    if not isinstance(value, str):
        raise CausalGateError(f"source pair is missing {arm}_run")
    run_dir = Path(value)
    if not run_dir.is_absolute():
        run_dir = (pair_dir / run_dir).resolve()
    return pair, run_dir


def _clean_checkpoint(flow: dict[str, Any]) -> bool:
    return (
        flow.get("current_step") == "prepare_turn"
        and flow.get("phase") is None
        and flow.get("pending") in ({}, None)
    )


def create_causal_fork(
    source_pair_dir: Path,
    *,
    human_will: str,
    hypothesis: str,
    output_root: Path | None = None,
    ledger_path: Path | None = None,
) -> Path:
    """Create an isolated, explicitly non-scoring Theatre checkpoint fork.

    This function performs no model call and never writes to the source pair.
    It preserves a byte-exact source snapshot and gives the active clone a new
    role-session namespace before binding the operator-supplied will.
    """
    source_pair_dir = source_pair_dir.resolve()
    human_will = human_will.strip()
    hypothesis = hypothesis.strip()
    if not human_will or not hypothesis:
        raise CausalGateError("human will and causal hypothesis must be non-empty")
    ledger_path = (ledger_path or source_pair_dir.parents[1] / "usage-ledger.jsonl").resolve()
    integrity = verify_pair(source_pair_dir, ledger_path)
    if integrity["status"] != "passed":
        raise CausalGateError("source pair failed replay verification: " + "; ".join(integrity["errors"]))
    pair, source_run = _source_run(source_pair_dir)
    source_manifest = read_json(source_run / "manifest.json")
    source_state = read_json(source_run / "state.json")
    source_flow = read_json(source_run / "flow.json")
    if not _clean_checkpoint(source_flow):
        raise CausalGateError("causal fork requires a clean prepare_turn checkpoint")

    source_files = _files_digest(source_run)
    pair_digest = _sha256_file(source_pair_dir / "pair.json")
    output_root = (output_root or ROOT / "runs" / "exploratory").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    fork_id = f"{stamp}-causal-s{source_manifest['seed']}-d{source_state['day']}"
    final_dir = output_root / fork_id
    temporary = Path(tempfile.mkdtemp(prefix=f".{fork_id}-", dir=output_root))
    try:
        snapshot_run = temporary / "source-checkpoint" / source_run.name
        active_run = temporary / "active" / source_run.name
        shutil.copytree(source_run, snapshot_run)
        shutil.copytree(source_run, active_run)
        if _files_digest(snapshot_run) != source_files or _files_digest(active_run) != source_files:
            raise CausalGateError("checkpoint changed while it was being copied")
        if _files_digest(source_run) != source_files or _sha256_file(source_pair_dir / "pair.json") != pair_digest:
            raise CausalGateError("source pair changed while the fork was being created")

        intervention = {
            "schema": INTERVENTION_SCHEMA,
            "kind": "human_consciousness_intervention",
            "human_will": human_will,
            "causal_hypothesis": hypothesis,
            "source_pair_id": pair["pair_id"],
            "source_run_id": source_manifest["run_id"],
            "applies_from_day": int(source_state["day"]),
            "authority_scope": "operator-supplied bytes for this exploratory fork only",
            "limitations": [
                "the file binds the supplied bytes but does not independently prove human identity",
                "the fork is assisted and cannot enter the official paired result",
            ],
        }
        intervention_hash = stable_hash(intervention)
        atomic_json(active_run / "consciousness.json", intervention)
        active_manifest = read_json(active_run / "manifest.json")
        active_manifest.update({
            "official": False,
            "scoring_eligible": False,
            "classification": NON_SCORING_CLASSIFICATION,
            "session_namespace": fork_id,
            "source_pair_id": pair["pair_id"],
            "source_run_id": source_manifest["run_id"],
            "source_checkpoint_day": int(source_state["day"]),
            "source_checkpoint_state_hash": stable_hash(source_state),
            "consciousness_intervention_hash": intervention_hash,
            "fork_created_at": utc_now(),
        })
        atomic_json(active_run / "manifest.json", active_manifest)
        fork_manifest = {
            "schema": FORK_SCHEMA,
            "fork_id": fork_id,
            "created_at": utc_now(),
            "classification": NON_SCORING_CLASSIFICATION,
            "scoring_eligible": False,
            "official_pair_unchanged": True,
            "source_pair_dir": str(source_pair_dir),
            "source_pair_id": pair["pair_id"],
            "source_pair_json_sha256": pair_digest,
            "source_run_id": source_manifest["run_id"],
            "source_seed": source_manifest["seed"],
            "source_day": source_state["day"],
            "source_state_hash": stable_hash(source_state),
            "source_file_hashes": source_files,
            "source_snapshot_run": f"source-checkpoint/{source_run.name}",
            "active_run": f"active/{source_run.name}",
            "usage_ledger_path": str(ledger_path),
            "intervention_hash": intervention_hash,
            "publication_policy": "forbidden; diagnostic evidence only",
            "next_gate": "run separately, then verify completion before v2 preregistration",
        }
        atomic_json(temporary / "fork.json", fork_manifest)
        temporary.replace(final_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final_dir


def _prefix_matches(snapshot: Path, active: Path) -> bool:
    if not snapshot.is_file() or not active.is_file():
        return False
    return active.read_bytes().startswith(snapshot.read_bytes())


def verify_causal_fork(fork_dir: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    fork_dir = fork_dir.resolve()
    errors: list[str] = []
    try:
        manifest = read_json(fork_dir / "fork.json")
        if manifest.get("schema") != FORK_SCHEMA:
            errors.append("fork schema mismatch")
        if manifest.get("classification") != NON_SCORING_CLASSIFICATION:
            errors.append("fork classification is not non-scoring")
        if manifest.get("scoring_eligible") is not False:
            errors.append("fork must be explicitly ineligible for scoring")
        snapshot_run = _member(fork_dir, str(manifest.get("source_snapshot_run", "")))
        active_run = _member(fork_dir, str(manifest.get("active_run", "")))
        if not snapshot_run.is_dir() or not active_run.is_dir():
            errors.append("fork run directories are missing")
            raise CausalGateError("missing fork run directory")
        expected_source = manifest.get("source_file_hashes")
        if not isinstance(expected_source, dict) or _files_digest(snapshot_run) != expected_source:
            errors.append("immutable source checkpoint digest mismatch")
        ledger = (ledger_path or Path(str(manifest.get("usage_ledger_path", "")))).resolve()
        source_report = verify_run(snapshot_run, "theatre", ledger)
        active_report = verify_run(active_run, "theatre", ledger)
        if source_report["status"] != "passed":
            errors.extend(f"source snapshot: {item}" for item in source_report["errors"])
        if active_report["status"] != "passed":
            errors.extend(f"active fork: {item}" for item in active_report["errors"])

        source_manifest = read_json(snapshot_run / "manifest.json")
        active_manifest = read_json(active_run / "manifest.json")
        intervention = read_json(active_run / "consciousness.json")
        if active_manifest.get("run_id") != source_manifest.get("run_id"):
            errors.append("active fork changed the replayed source run id")
        if active_manifest.get("classification") != NON_SCORING_CLASSIFICATION:
            errors.append("active run classification mismatch")
        if active_manifest.get("official") is not False or active_manifest.get("scoring_eligible") is not False:
            errors.append("active run is not fail-closed against official scoring")
        if active_manifest.get("session_namespace") != manifest.get("fork_id"):
            errors.append("active run does not have an isolated session namespace")
        if intervention.get("schema") != INTERVENTION_SCHEMA:
            errors.append("consciousness intervention schema mismatch")
        intervention_hash = stable_hash(intervention)
        if intervention_hash != manifest.get("intervention_hash") or intervention_hash != active_manifest.get("consciousness_intervention_hash"):
            errors.append("consciousness intervention binding mismatch")

        for name in ("scenario.json", "prompt-control.md", "prompt-critic.md", "prompt-planner.md", "prompt-actor.md"):
            if _sha256_file(snapshot_run / name) != _sha256_file(active_run / name):
                errors.append(f"active fork changed frozen source bytes: {name}")
        for name in ("usage.jsonl", "model-decisions.jsonl", "turns.jsonl"):
            source_path = snapshot_run / name
            if source_path.exists() and not _prefix_matches(source_path, active_run / name):
                errors.append(f"active fork lost source history prefix: {name}")
        if (fork_dir / "pair.json").exists() or any(fork_dir.glob("**/pair.json")):
            errors.append("exploratory fork must not contain a paired-result manifest")
    except (FileNotFoundError, json.JSONDecodeError, CausalGateError, OSError, ValueError) as exc:
        errors.append(str(exc))
        source_report = {"status": "failed", "errors": []}
        active_report = {"status": "failed", "errors": []}
        manifest = {}
        active_run = fork_dir
    return {
        "schema": FORK_SCHEMA,
        "status": "passed" if not errors else "failed",
        "fork_id": manifest.get("fork_id"),
        "classification": manifest.get("classification"),
        "scoring_eligible": manifest.get("scoring_eligible"),
        "active_run": str(active_run),
        "source_verification": source_report.get("status"),
        "active_verification": active_report.get("status"),
        "errors": errors,
    }


def _new_seeds(seeds: Iterable[int], *, source_seed: int, runs_root: Path) -> list[int]:
    values = [int(value) for value in seeds]
    if len(values) != 5 or len(set(values)) != 5 or any(value <= 0 for value in values):
        raise CausalGateError("v2 preregistration requires exactly five unique positive seeds")
    used = {source_seed}
    if runs_root.exists():
        for path in runs_root.rglob("manifest.json"):
            try:
                seed = read_json(path).get("seed")
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(seed, int):
                used.add(seed)
    overlap = sorted(set(values) & used)
    if overlap:
        raise CausalGateError(f"v2 seeds are not new: {overlap}")
    return values


def create_v2_preregistration(
    fork_dir: Path,
    *,
    seeds: Iterable[int],
    scenario_path: Path,
    prompt_dir: Path,
    protocol_path: Path,
    output_path: Path,
    runs_root: Path | None = None,
    ledger_path: Path | None = None,
) -> Path:
    """Freeze a v2 design only after a completed, verified exploratory fork."""
    report = verify_causal_fork(fork_dir, ledger_path)
    if report["status"] != "passed":
        raise CausalGateError("exploratory fork failed verification: " + "; ".join(report["errors"]))
    fork_manifest = read_json(fork_dir / "fork.json")
    active_run = _member(fork_dir, fork_manifest["active_run"])
    active_manifest = read_json(active_run / "manifest.json")
    state = read_json(active_run / "state.json")
    if active_manifest.get("status") != "completed" or not state.get("terminated") or not (active_run / "result.json").is_file():
        raise CausalGateError("v2 preregistration requires a completed exploratory fork")

    prompt_paths = {role: prompt_dir / f"{role}.md" for role in PROMPT_FILES}
    required = [scenario_path, protocol_path, *prompt_paths.values()]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise CausalGateError("missing v2 design files: " + ", ".join(missing))
    scenario = read_json(scenario_path)
    prompt_hashes = {role: stable_hash(path.read_text(encoding="utf-8")) for role, path in prompt_paths.items()}
    source_prompt_hashes = read_json(active_run / "manifest.json").get("prompt_hashes", {})
    source_scenario_hash = read_json(active_run / "manifest.json").get("scenario_hash")
    prospective_scenario_hash = stable_hash(scenario)
    if prompt_hashes == source_prompt_hashes and prospective_scenario_hash == source_scenario_hash:
        raise CausalGateError("v2 design must differ from the frozen v1 scenario or prompts")
    values = _new_seeds(
        seeds,
        source_seed=int(fork_manifest["source_seed"]),
        runs_root=(runs_root or ROOT / "runs").resolve(),
    )
    output_path = output_path.resolve()
    if output_path.exists():
        raise CausalGateError("preregistration output already exists")
    result = read_json(active_run / "result.json")
    registration = {
        "schema": PREREGISTRATION_SCHEMA,
        "created_at": utc_now(),
        "status": "preregistered_not_started",
        "source_exploratory_fork": fork_manifest["fork_id"],
        "source_exploratory_result_hash": stable_hash(result),
        "model": active_manifest["model"],
        "thinking": active_manifest["thinking"],
        "days": int(scenario["days"]),
        "seeds": [
            {"seed": seed, "first_arm": "control" if index % 2 == 0 else "theatre"}
            for index, seed in enumerate(values)
        ],
        "scenario_hash": prospective_scenario_hash,
        "prompt_hashes": prompt_hashes,
        "protocol_sha256": _sha256_file(protocol_path),
        "fairness": {
            "same_model": True,
            "same_scenario_within_pair": True,
            "same_action_contract_within_pair": True,
            "theatre_advisory_role_costs_charged": True,
            "failed_paused_bankrupt_and_low_scoring_runs_retained": True,
        },
        "exclusions": [
            f"seed {fork_manifest['source_seed']} and its checkpoint fork are exploratory evidence only",
            "the v1 pilot remains a separate frozen result",
        ],
    }
    atomic_json(output_path, registration)
    return output_path
