from __future__ import annotations

import html
import itertools
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .campaign import (
    _atomic_text,
    _evidence_manifest,
    _normalized_preregistration,
    _normalized_verification,
    _run_path,
)
from .report import ReportGateError
from .runner import read_json
from .simulator import stable_hash
from .v3 import PREREGISTRATION, audit_preregistration
from .verify import verify_pair


def _exact_bootstrap_interval(values: list[float]) -> list[float]:
    """Return the exact 95% paired-bootstrap interval for the five frozen seeds."""

    means = sorted(
        sum(values[index] for index in sample) / len(values)
        for sample in itertools.product(range(len(values)), repeat=len(values))
    )

    def quantile(q: float) -> float:
        position = (len(means) - 1) * q
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return means[lower]
        fraction = position - lower
        return means[lower] + (means[upper] - means[lower]) * fraction

    return [round(quantile(0.025), 2), round(quantile(0.975), 2)]


def build_v3_terminal_campaign(
    run_root: Path,
    *,
    preregistration: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Build verified evidence after every official v3 seed is terminal.

    Economic aggregation is all-or-nothing: it exists only when all five pairs
    completed with final results. Failed pairs remain reliability evidence and
    can never be replaced by partial scores.
    """

    root = run_root.resolve()
    if not root.is_dir():
        raise ReportGateError("campaign run root does not exist")
    pairs_root = root / "pairs"
    if not pairs_root.is_dir():
        raise ReportGateError("campaign run root is missing pairs/")

    audit = audit_preregistration(preregistration or PREREGISTRATION)
    if audit.get("status") != "passed":
        details = "; ".join(audit.get("errors", [])) or "unknown preregistration failure"
        raise ReportGateError(f"v3 preregistration integrity failed: {details}")
    expected_seeds = [int(seed) for seed in audit.get("paired_seeds", [])]
    if not expected_seeds:
        raise ReportGateError("v3 preregistration contains no paired seeds")

    candidates: dict[int, tuple[Path, dict[str, Any]]] = {}
    for pair_dir in sorted(path for path in pairs_root.iterdir() if path.is_dir()):
        pair_path = pair_dir / "pair.json"
        if not pair_path.is_file():
            continue
        pair = read_json(pair_path)
        if pair.get("protocol_version") != "v3" or pair.get("official") is not True:
            continue
        seed = pair.get("seed")
        if not isinstance(seed, int):
            raise ReportGateError(f"official v3 pair {pair_dir.name} has invalid seed")
        if seed in candidates:
            raise ReportGateError(f"campaign has duplicate official pair for seed {seed}")
        candidates[seed] = (pair_dir.resolve(), pair)

    observed_seeds = sorted(candidates)
    if observed_seeds != sorted(expected_seeds):
        raise ReportGateError(
            f"campaign seed set mismatch: expected {sorted(expected_seeds)}, observed {observed_seeds}"
        )

    ledger = ledger_path.resolve() if ledger_path is not None else root / "usage-ledger.jsonl"
    usage_totals = {
        arm: {
            "model_calls": 0,
            "provider_total_tokens": 0,
            "output_tokens": 0,
            "first_pass_contract_failures": 0,
            "successful_repairs": 0,
            "terminal_repair_failures": 0,
            "repair_calls": 0,
            "repair_tokens": 0,
        }
        for arm in ("control", "theatre")
    }
    failures_by_arm: Counter[str] = Counter()
    failures_by_phase: Counter[str] = Counter()
    pairs: list[dict[str, Any]] = []
    paired_differences: list[float] = []

    for seed in expected_seeds:
        pair_dir, pair = candidates[seed]
        status = pair.get("status")
        if status not in ("completed", "failed_contract"):
            raise ReportGateError(f"seed {seed} is not terminal: status={status!r}")

        verification = verify_pair(pair_dir, ledger)
        if verification.get("status") != "passed":
            details = "; ".join(verification.get("errors", [])) or "unknown integrity failure"
            raise ReportGateError(f"seed {seed} pair integrity failed: {details}")
        if verification.get("pair_status") != status:
            raise ReportGateError(f"seed {seed} verification status differs from pair")

        pair_result_path = pair_dir / "result.json"
        if status == "completed" and not pair_result_path.is_file():
            raise ReportGateError(f"seed {seed} completed pair is missing result.json")
        if status == "failed_contract" and pair_result_path.exists():
            raise ReportGateError(f"seed {seed} failed_contract pair unexpectedly has result.json")

        arms: dict[str, dict[str, Any]] = {}
        failing: list[tuple[str, dict[str, Any]]] = []
        for arm in ("control", "theatre"):
            run_dir = _run_path(root, pair_dir, pair, arm)
            manifest = read_json(run_dir / "manifest.json")
            flow = read_json(run_dir / "flow.json")
            if manifest.get("official") is not True or manifest.get("protocol_version") != "v3":
                raise ReportGateError(f"seed {seed} {arm} run is not official v3 evidence")
            if manifest.get("seed") != seed or manifest.get("arm") != arm:
                raise ReportGateError(f"seed {seed} {arm} manifest identity mismatch")
            if status == "completed" and not (run_dir / "result.json").is_file():
                raise ReportGateError(f"seed {seed} completed {arm} run is missing result.json")
            if status == "failed_contract" and (run_dir / "result.json").exists():
                raise ReportGateError(f"seed {seed} failed {arm} run unexpectedly has result.json")

            verified = verification.get("runs", {}).get(arm)
            if not isinstance(verified, dict):
                raise ReportGateError(f"seed {seed} verification is missing {arm}")
            arm_evidence = {
                "run_id": manifest.get("run_id"),
                "day": verified.get("day"),
                "turns": verified.get("turns"),
                "model_calls": verified.get("model_calls"),
                "model_failures": verified.get("model_failures"),
                "provider_total_tokens": verified.get("provider_total_tokens"),
                "output_tokens": verified.get("output_tokens"),
                "first_pass_contract_failures": verified.get("first_pass_contract_failures"),
                "successful_repairs": verified.get("successful_repairs"),
                "terminal_repair_failures": verified.get("terminal_repair_failures"),
                "repair_calls": verified.get("repair_calls"),
                "repair_tokens": verified.get("repair_tokens"),
                "replay_state_hash": verified.get("replay_state_hash"),
                "flow_status": flow.get("status"),
            }
            arms[arm] = arm_evidence
            for key in usage_totals[arm]:
                value = arm_evidence[key]
                if not isinstance(value, int) or value < 0:
                    raise ReportGateError(f"seed {seed} {arm} has invalid verified {key}")
                usage_totals[arm][key] += value
            if flow.get("status") == "failed_contract":
                contract_failure = flow.get("contract_failure")
                if not isinstance(contract_failure, dict):
                    raise ReportGateError(f"seed {seed} {arm} lacks contract_failure evidence")
                failing.append((arm, contract_failure))

        pair_evidence: dict[str, Any] = {
            "seed": seed,
            "pair_id": pair.get("pair_id"),
            "status": status,
            "arms": arms,
            "verification_digest": stable_hash(_normalized_verification(verification)),
        }
        if status == "failed_contract":
            if len(failing) != 1:
                raise ReportGateError(
                    f"seed {seed} must identify exactly one failing arm; observed {len(failing)}"
                )
            failing_arm, contract_failure = failing[0]
            phase = contract_failure.get("phase")
            message = contract_failure.get("message")
            if not isinstance(phase, str) or not phase or not isinstance(message, str) or not message:
                raise ReportGateError(f"seed {seed} contract failure lacks phase/message")
            failures_by_arm[failing_arm] += 1
            failures_by_phase[phase] += 1
            pair_evidence.update({
                "failing_arm": failing_arm,
                "failure_phase": phase,
                "failure_message": message,
                "economic_result": None,
            })
        else:
            if failing:
                raise ReportGateError(f"seed {seed} completed pair contains failed_contract flow")
            result = read_json(pair_result_path)
            difference = result.get("paired_difference_theatre_minus_control")
            winner = result.get("winner")
            if not isinstance(difference, (int, float)) or winner not in ("control", "theatre", "tie"):
                raise ReportGateError(f"seed {seed} completed result lacks paired difference/winner")
            normalized_difference = round(float(difference), 2)
            paired_differences.append(normalized_difference)
            pair_evidence.update({
                "failing_arm": None,
                "failure_phase": None,
                "failure_message": None,
                "economic_result": {
                    "paired_difference_theatre_minus_control": normalized_difference,
                    "winner": winner,
                    "control_primary_score": result.get("control", {}).get("score", {}).get("primary_score"),
                    "theatre_primary_score": result.get("theatre", {}).get("score", {}).get("primary_score"),
                },
            })
        pairs.append(pair_evidence)

    completed = len(paired_differences)
    all_completed = completed == len(expected_seeds)
    if all_completed:
        mean = round(statistics.fmean(paired_differences), 2)
        median = round(statistics.median(paired_differences), 2)
        interval = _exact_bootstrap_interval(paired_differences)
        winner = "theatre" if interval[0] > 0 else "control" if interval[1] < 0 else None
        estimand_status = "conclusive" if winner is not None else "inconclusive"
        reason = (
            "o intervalo bootstrap pareado exclui zero"
            if winner is not None
            else "o intervalo bootstrap pareado inclui zero"
        )
    else:
        mean = median = interval = winner = None
        estimand_status = "not_observed" if completed == 0 else "not_identified"
        reason = (
            "nenhuma seed produziu result.json; placares parciais não são resultados"
            if completed == 0
            else "nem todas as cinco seeds produziram resultado final; falhas terminais não recebem score substituto"
        )

    evidence_manifest = _evidence_manifest(root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "official_v3_terminal_campaign",
        "claim": (
            f"campanha oficial concluída; resultado econômico {estimand_status}"
            if all_completed
            else "campanha oficial terminal; efeito econômico não identificável"
        ),
        "campaign": {
            "protocol": "v3",
            "expected_pairs": len(expected_seeds),
            "verified_pairs": len(pairs),
            "completed_pairs": completed,
            "failed_contract_pairs": len(pairs) - completed,
            "pairs_with_economic_result": completed,
            "status": "terminal",
        },
        "economic_outcome": {
            "estimand_status": estimand_status,
            "winner": winner,
            "paired_differences": paired_differences if all_completed else None,
            "mean_paired_difference": mean,
            "median_paired_difference": median,
            "theatre_seed_wins": sum(value > 0 for value in paired_differences) if all_completed else None,
            "control_seed_wins": sum(value < 0 for value in paired_differences) if all_completed else None,
            "ties": sum(value == 0 for value in paired_differences) if all_completed else None,
            "bootstrap_interval_95": interval,
            "bootstrap_method": "exact paired resampling over all 5^5 samples" if all_completed else None,
            "reason": reason,
        },
        "reliability": {
            "failed_pairs_by_arm": {
                "control": failures_by_arm["control"],
                "theatre": failures_by_arm["theatre"],
            },
            "failed_pairs_by_phase": dict(sorted(failures_by_phase.items())),
            "usage_and_repairs_by_arm": usage_totals,
        },
        "pairs": pairs,
        "integrity": {
            "status": "passed",
            "preregistration_digest": stable_hash(_normalized_preregistration(audit)),
            "evidence_file_count": len(evidence_manifest),
            "evidence_manifest_sha256": stable_hash(evidence_manifest),
        },
        "next_boundary": {
            "official_v3_evidence": "immutable",
            "resume_or_recreate_seeds_2301_2305": "forbidden",
            "economic_claim": "allowed only from five completed pair results" if all_completed else "not available from this campaign",
        },
        "caveats": [
            "Falhas terminais são evidência de confiabilidade do tratamento e nunca recebem o score parcial do ponto onde pararam.",
            "Dias, chamadas e tokens de pares falhos não são comparados como se os horizontes fossem iguais.",
            "Reparos mostram robustez contratual e custo; não substituem o resultado econômico anual.",
            "O benchmark econômico complementa, mas não substitui, o júri humano cego do épico Theatre.",
        ],
    }
    report["integrity"]["report_digest"] = stable_hash(report)
    return report


def render_v3_campaign_markdown(report: dict[str, Any]) -> str:
    outcome = report["economic_outcome"]
    reliability = report["reliability"]
    rows = []
    for pair in report["pairs"]:
        terminal = (
            f"{pair['failing_arm']}/{pair['failure_phase']}"
            if pair["status"] == "failed_contract"
            else f"completed · Δ {pair['economic_result']['paired_difference_theatre_minus_control']:+.2f}"
        )
        rows.append(
            f"| {pair['seed']} | `{terminal}` | "
            f"{pair['arms']['control']['day']} / {pair['arms']['theatre']['day']} | "
            f"{pair['arms']['control']['model_calls']} / {pair['arms']['theatre']['model_calls']} | "
            f"{pair['arms']['control']['provider_total_tokens']} / {pair['arms']['theatre']['provider_total_tokens']} |"
        )
    failures = "\n".join(
        f"- **Seed {pair['seed']} · {pair['failing_arm']}/{pair['failure_phase']}:** {pair['failure_message']}"
        for pair in report["pairs"]
        if pair["status"] == "failed_contract"
    ) or "- Nenhuma falha terminal."
    caveats = "\n".join(f"- {item}" for item in report["caveats"])
    aggregate = (
        f"- **Diferenças pareadas:** {outcome['paired_differences']}\n"
        f"- **Média / mediana:** {outcome['mean_paired_difference']:+.2f} / {outcome['median_paired_difference']:+.2f}\n"
        f"- **Intervalo bootstrap 95%:** {outcome['bootstrap_interval_95']}\n"
        f"- **Conclusão:** {outcome['winner'] or 'inconclusiva'}"
        if outcome["paired_differences"] is not None
        else f"- **Agregado econômico:** não calculável\n- **Motivo:** {outcome['reason']}"
    )
    return f"""# Theatre Business Bench v3 — campanha oficial terminal

> **Estado da alegação:** {report['claim']}.

## Resultado correto

- **Seeds verificadas:** {report['campaign']['verified_pairs']}/{report['campaign']['expected_pairs']}
- **Seeds concluídas com resultado:** {report['campaign']['completed_pairs']}
- **Seeds `failed_contract`:** {report['campaign']['failed_contract_pairs']}
- **Vencedor agregado:** {outcome['winner'] or 'nenhum / não calculável'}
{aggregate}

## Confiabilidade e custo observados

- Gate terminal no controle: {reliability['failed_pairs_by_arm']['control']}
- Gate terminal no Theatre: {reliability['failed_pairs_by_arm']['theatre']}
- Chamadas: controle {reliability['usage_and_repairs_by_arm']['control']['model_calls']} · Theatre {reliability['usage_and_repairs_by_arm']['theatre']['model_calls']}
- Reparos aceitos: controle {reliability['usage_and_repairs_by_arm']['control']['successful_repairs']} · Theatre {reliability['usage_and_repairs_by_arm']['theatre']['successful_repairs']}

| Seed | Estado terminal | Dia controle / Theatre | Calls controle / Theatre | Tokens controle / Theatre |
|---:|---|---:|---:|---:|
{chr(10).join(rows)}

## Causas terminais preservadas

{failures}

## Integridade

- Replay: `passed` nas cinco seeds
- Arquivos confrontados: {report['integrity']['evidence_file_count']}
- Manifesto da evidência: `{report['integrity']['evidence_manifest_sha256']}`
- Digest do relatório: `{report['integrity']['report_digest']}`

## Limites honestos

{caveats}
"""


def render_v3_campaign_html(report: dict[str, Any]) -> str:
    outcome = report["economic_outcome"]
    rows = "".join(
        "<tr>"
        f"<td>{pair['seed']}</td>"
        f"<td><code>{html.escape(pair['status'])}</code></td>"
        f"<td>{pair['arms']['control']['day']} / {pair['arms']['theatre']['day']}</td>"
        f"<td>{pair['arms']['control']['model_calls']} / {pair['arms']['theatre']['model_calls']}</td>"
        f"<td>{pair['arms']['control']['provider_total_tokens']:,} / {pair['arms']['theatre']['provider_total_tokens']:,}</td>"
        "</tr>"
        for pair in report["pairs"]
    )
    caveats = "".join(f"<li>{html.escape(item)}</li>" for item in report["caveats"])
    headline = (
        f"Conclusão econômica: {outcome['winner'] or 'inconclusiva'}."
        if outcome["paired_differences"] is not None
        else "A campanha não identifica um vencedor econômico."
    )
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Campanha v3 terminal — Theatre Business Bench</title>
<style>body{{margin:0;background:#f4f0e7;color:#151a22;font:16px/1.5 system-ui,sans-serif}}main{{width:min(980px,calc(100% - 32px));margin:auto;padding:48px 0}}.eyebrow{{color:#7d3fb2;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}h1{{font-size:clamp(36px,7vw,66px);line-height:1;margin:.2em 0}}.warning{{padding:18px;border-left:5px solid #7d3fb2;background:#eee0f6}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:28px 0}}article{{padding:20px;border:1px solid #d8d1c4;border-radius:16px;background:#fffdf8}}.number{{font-size:38px;font-weight:900}}table{{width:100%;border-collapse:collapse;background:#fffdf8}}th,td{{padding:11px;border-bottom:1px solid #d8d1c4;text-align:right}}th:first-child,td:first-child{{text-align:left}}code{{overflow-wrap:anywhere}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}table{{font-size:12px}}}}</style></head>
<body><main><div class="eyebrow">Rodada oficial · evidência terminal verificada</div><h1>{html.escape(headline)}</h1>
<p class="warning"><strong>Leitura correta:</strong> {html.escape(outcome['reason'])}. Falhas terminais nunca recebem score parcial substituto.</p>
<div class="grid"><article><div class="number">5/5</div><strong>replays verdes</strong></article><article><div class="number">{report['campaign']['completed_pairs']}/5</div><strong>resultados anuais</strong></article><article><div class="number">{report['campaign']['failed_contract_pairs']}/5</div><strong>falhas terminais</strong></article></div>
<h2>Seed por seed</h2><table><tr><th>Seed</th><th>Status</th><th>Dia C/T</th><th>Calls C/T</th><th>Tokens C/T</th></tr>{rows}</table>
<h2>Integridade</h2><p>Manifesto da evidência: <code>{report['integrity']['evidence_manifest_sha256']}</code><br>Digest do relatório: <code>{report['integrity']['report_digest']}</code></p>
<h2>Limites honestos</h2><ul>{caveats}</ul></main></body></html>
"""


def write_v3_campaign_bundle(
    run_root: Path,
    report: dict[str, Any],
    *,
    json_out: Path | None = None,
    markdown_out: Path | None = None,
    html_out: Path | None = None,
) -> None:
    root = run_root.resolve()
    outputs = [path.resolve() for path in (json_out, markdown_out, html_out) if path is not None]
    if len(outputs) != len(set(outputs)):
        raise ReportGateError("campaign outputs must use distinct paths")
    for output in outputs:
        if output == root or root in output.parents:
            raise ReportGateError("campaign output must be outside the immutable run root")
    if json_out is not None:
        _atomic_text(json_out, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    if markdown_out is not None:
        _atomic_text(markdown_out, render_v3_campaign_markdown(report))
    if html_out is not None:
        _atomic_text(html_out, render_v3_campaign_html(report))
