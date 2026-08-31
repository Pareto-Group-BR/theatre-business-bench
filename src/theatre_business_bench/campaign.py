from __future__ import annotations

import hashlib
import html
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .report import ReportGateError
from .runner import read_json
from .simulator import stable_hash
from .v2 import PREREGISTRATION, audit_preregistration
from .verify import verify_pair


def _run_path(run_root: Path, pair_dir: Path, pair: dict[str, Any], arm: str) -> Path:
    value = pair.get(f"{arm}_run")
    if not isinstance(value, str):
        raise ReportGateError(f"pair is missing {arm}_run")
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (pair_dir / candidate).resolve()
    if resolved != run_root and run_root not in resolved.parents:
        raise ReportGateError(f"{arm} run escapes the declared campaign root")
    return resolved


def _evidence_manifest(run_root: Path) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for path in sorted(run_root.rglob("*")):
        if path.is_symlink():
            raise ReportGateError(f"campaign evidence contains symlink: {path.relative_to(run_root)}")
        if not path.is_file():
            continue
        data = path.read_bytes()
        manifest.append({
            "path": path.relative_to(run_root).as_posix(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return manifest


def _normalized_verification(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "pair_id": verification.get("pair_id"),
        "pair_status": verification.get("pair_status"),
        "runs": verification.get("runs"),
        "errors": verification.get("errors"),
    }


def _normalized_preregistration(audit: dict[str, Any]) -> dict[str, Any]:
    """Remove the checkout-local path from an otherwise deterministic audit."""

    return {key: value for key, value in audit.items() if key != "preregistration"}


def build_v2_terminal_campaign(
    run_root: Path,
    *,
    preregistration: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Build read-only evidence for a v2 campaign where every pair failed contract.

    This is intentionally not an economic result renderer. It requires the exact
    five pre-registered seeds, green replay for every pair, terminal
    ``failed_contract`` state, and complete absence of ``result.json``. It then
    reports operational reliability and observed usage without calculating a
    paired score, winner, or economic aggregate.
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
        raise ReportGateError(f"v2 preregistration integrity failed: {details}")
    expected_seeds = [int(seed) for seed in audit.get("paired_seeds", [])]
    if not expected_seeds:
        raise ReportGateError("v2 preregistration contains no paired seeds")

    candidates: dict[int, tuple[Path, dict[str, Any]]] = {}
    for pair_dir in sorted(path for path in pairs_root.iterdir() if path.is_dir()):
        pair_path = pair_dir / "pair.json"
        if not pair_path.is_file():
            continue
        pair = read_json(pair_path)
        if pair.get("protocol_version") != "v2" or pair.get("official") is not True:
            continue
        seed = pair.get("seed")
        if not isinstance(seed, int):
            raise ReportGateError(f"official v2 pair {pair_dir.name} has invalid seed")
        if seed in candidates:
            raise ReportGateError(f"campaign has duplicate official pair for seed {seed}")
        candidates[seed] = (pair_dir.resolve(), pair)

    observed_seeds = sorted(candidates)
    if observed_seeds != sorted(expected_seeds):
        raise ReportGateError(
            f"campaign seed set mismatch: expected {sorted(expected_seeds)}, observed {observed_seeds}"
        )

    ledger = ledger_path.resolve() if ledger_path is not None else (root / "usage-ledger.jsonl")
    pairs: list[dict[str, Any]] = []
    failures_by_arm: Counter[str] = Counter()
    failures_by_phase: Counter[str] = Counter()
    usage_totals = {
        "control": {"model_calls": 0, "provider_total_tokens": 0, "output_tokens": 0},
        "theatre": {"model_calls": 0, "provider_total_tokens": 0, "output_tokens": 0},
    }

    for seed in expected_seeds:
        pair_dir, pair = candidates[seed]
        if pair.get("status") != "failed_contract":
            raise ReportGateError(
                f"seed {seed} is not terminal failed_contract: status={pair.get('status')!r}"
            )
        if (pair_dir / "result.json").exists():
            raise ReportGateError(f"seed {seed} failed_contract pair unexpectedly has result.json")

        verification = verify_pair(pair_dir, ledger)
        if verification.get("status") != "passed":
            details = "; ".join(verification.get("errors", [])) or "unknown integrity failure"
            raise ReportGateError(f"seed {seed} pair integrity failed: {details}")
        if verification.get("pair_status") != "failed_contract":
            raise ReportGateError(f"seed {seed} verification does not preserve failed_contract")

        arms: dict[str, dict[str, Any]] = {}
        failing: list[tuple[str, dict[str, Any]]] = []
        for arm in ("control", "theatre"):
            run_dir = _run_path(root, pair_dir, pair, arm)
            manifest = read_json(run_dir / "manifest.json")
            flow = read_json(run_dir / "flow.json")
            if manifest.get("official") is not True or manifest.get("protocol_version") != "v2":
                raise ReportGateError(f"seed {seed} {arm} run is not official v2 evidence")
            if manifest.get("seed") != seed or manifest.get("arm") != arm:
                raise ReportGateError(f"seed {seed} {arm} manifest identity mismatch")
            if (run_dir / "result.json").exists():
                raise ReportGateError(f"seed {seed} {arm} unexpectedly has result.json")

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

        normalized = _normalized_verification(verification)
        pairs.append({
            "seed": seed,
            "pair_id": pair.get("pair_id"),
            "status": "failed_contract",
            "failing_arm": failing_arm,
            "failure_phase": phase,
            "failure_message": message,
            "arms": arms,
            "verification_digest": stable_hash(normalized),
        })

    evidence_manifest = _evidence_manifest(root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "official_v2_terminal_campaign",
        "claim": "campanha oficial encerrada em falhas de contrato; resultado econômico não observado",
        "campaign": {
            "protocol": "v2",
            "expected_pairs": len(expected_seeds),
            "verified_pairs": len(pairs),
            "failed_contract_pairs": len(pairs),
            "pairs_with_economic_result": 0,
            "status": "terminal_failed_contract",
        },
        "economic_outcome": {
            "estimand_status": "not_observed",
            "winner": None,
            "paired_differences": None,
            "mean_paired_difference": None,
            "median_paired_difference": None,
            "bootstrap_interval": None,
            "reason": "nenhuma das cinco seeds produziu result.json; placares parciais não são resultados",
        },
        "reliability": {
            "failed_pairs_by_arm": {
                "control": failures_by_arm["control"],
                "theatre": failures_by_arm["theatre"],
            },
            "failed_pairs_by_phase": dict(sorted(failures_by_phase.items())),
            "usage_by_arm": usage_totals,
        },
        "pairs": pairs,
        "integrity": {
            "status": "passed",
            "preregistration_digest": stable_hash(_normalized_preregistration(audit)),
            "evidence_file_count": len(evidence_manifest),
            "evidence_manifest_sha256": stable_hash(evidence_manifest),
        },
        "next_boundary": {
            "official_v2_evidence": "immutable",
            "resume_or_recreate_seeds_2201_2205": "forbidden",
            "economic_proof": "requires a separately pre-registered future protocol and new seeds",
        },
        "caveats": [
            "Este relatório mede integridade e confiabilidade operacional da campanha, não desempenho econômico comparável.",
            "Dias, chamadas e tokens descrevem onde cada execução parou; não são scores finais nem podem ser agregados como se os horizontes fossem iguais.",
            "Atribuição do braço que disparou o gate descreve o contrato observado; não estabelece superioridade do outro braço.",
            "Qualquer protocolo futuro deve usar nova versão e novas seeds, preservando esta rodada sem edição, retomada ou descarte.",
        ],
    }
    report["integrity"]["report_digest"] = stable_hash(report)
    return report


def render_v2_campaign_markdown(report: dict[str, Any]) -> str:
    reliability = report["reliability"]
    rows = []
    for pair in report["pairs"]:
        control = pair["arms"]["control"]
        theatre = pair["arms"]["theatre"]
        rows.append(
            f"| {pair['seed']} | `{pair['failing_arm']}` / `{pair['failure_phase']}` | "
            f"{control['day']} / {theatre['day']} | "
            f"{control['model_calls']} / {theatre['model_calls']} | "
            f"{control['provider_total_tokens']} / {theatre['provider_total_tokens']} |"
        )
    causes = "\n".join(
        f"- **Seed {pair['seed']} · {pair['failing_arm']}/{pair['failure_phase']}:** "
        f"{pair['failure_message']}"
        for pair in report["pairs"]
    )
    caveats = "\n".join(f"- {item}" for item in report["caveats"])
    return f"""# Theatre Business Bench v2 — campanha oficial terminal

> **Estado da alegação:** {report['claim']}.

## Resultado correto

- **Resultado econômico:** não observado
- **Vencedor:** nenhum / não calculável
- **Seeds verificadas:** {report['campaign']['verified_pairs']}/{report['campaign']['expected_pairs']}
- **Seeds terminais `failed_contract`:** {report['campaign']['failed_contract_pairs']}
- **Seeds com `result.json`:** {report['campaign']['pairs_with_economic_result']}

Os placares parciais têm horizontes diferentes e não são agregados. Média,
mediana, vitórias por seed e bootstrap permanecem ausentes por desenho.

## Confiabilidade observada

- Gate disparado no controle: {reliability['failed_pairs_by_arm']['control']}/5 seeds
- Gate disparado no Theatre: {reliability['failed_pairs_by_arm']['theatre']}/5 seeds
- Chamadas: controle {reliability['usage_by_arm']['control']['model_calls']} · Theatre {reliability['usage_by_arm']['theatre']['model_calls']}
- Tokens reportados: controle {reliability['usage_by_arm']['control']['provider_total_tokens']} · Theatre {reliability['usage_by_arm']['theatre']['provider_total_tokens']}

| Seed | Braço/fase terminal | Dia controle / Theatre | Calls controle / Theatre | Tokens controle / Theatre |
|---:|---|---:|---:|---:|
{chr(10).join(rows)}

## Causas preservadas

{causes}

## Integridade

- Replay: `passed` nas cinco seeds
- Arquivos confrontados: {report['integrity']['evidence_file_count']}
- Manifesto da evidência: `{report['integrity']['evidence_manifest_sha256']}`
- Digest do relatório: `{report['integrity']['report_digest']}`

## Próxima fronteira

As seeds 2201–2205 permanecem imutáveis e não podem ser retomadas ou recriadas.
Fechar a prova econômica exige outro protocolo pré-registrado e novas seeds; essa
decisão não altera nem transforma esta campanha terminal em resultado.

## Limites honestos

{caveats}
"""


def render_v2_campaign_html(report: dict[str, Any]) -> str:
    reliability = report["reliability"]
    rows = "".join(
        "<tr>"
        f"<td>{pair['seed']}</td>"
        f"<td><code>{html.escape(pair['failing_arm'])}/{html.escape(pair['failure_phase'])}</code></td>"
        f"<td>{pair['arms']['control']['day']} / {pair['arms']['theatre']['day']}</td>"
        f"<td>{pair['arms']['control']['model_calls']} / {pair['arms']['theatre']['model_calls']}</td>"
        f"<td>{pair['arms']['control']['provider_total_tokens']:,} / {pair['arms']['theatre']['provider_total_tokens']:,}</td>"
        "</tr>"
        for pair in report["pairs"]
    )
    causes = "".join(
        f"<li><strong>Seed {pair['seed']} · {html.escape(pair['failing_arm'])}/{html.escape(pair['failure_phase'])}:</strong> "
        f"{html.escape(pair['failure_message'])}</li>"
        for pair in report["pairs"]
    )
    caveats = "".join(f"<li>{html.escape(item)}</li>" for item in report["caveats"])
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Campanha v2 terminal — Theatre Business Bench</title>
<style>body{{margin:0;background:#f4f0e7;color:#151a22;font:16px/1.5 system-ui,sans-serif}}main{{width:min(980px,calc(100% - 32px));margin:auto;padding:48px 0}}.eyebrow{{color:#a63725;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}h1{{font-size:clamp(38px,7vw,68px);line-height:1;margin:.2em 0}}.warning{{padding:18px;border-left:5px solid #a63725;background:#f4d9cf}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:28px 0}}article{{padding:20px;border:1px solid #d8d1c4;border-radius:16px;background:#fffdf8}}.number{{font-size:38px;font-weight:900}}table{{width:100%;border-collapse:collapse;background:#fffdf8}}th,td{{padding:11px;border-bottom:1px solid #d8d1c4;text-align:right}}th:first-child,td:first-child{{text-align:left}}code{{overflow-wrap:anywhere}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}table{{font-size:12px}}}}</style></head>
<body><main><div class="eyebrow">Rodada oficial · evidência terminal verificada</div><h1>Não houve resultado econômico v2.</h1>
<p class="warning"><strong>Leitura correta:</strong> cinco de cinco seeds terminaram em <code>failed_contract</code>. Nenhum <code>result.json</code> existe; vencedor e agregado permanecem nulos.</p>
<div class="grid"><article><div class="number">5/5</div><strong>replays verdes</strong></article><article><div class="number">{reliability['failed_pairs_by_arm']['control']}/5</div><strong>gate no controle</strong></article><article><div class="number">{reliability['failed_pairs_by_arm']['theatre']}/5</div><strong>gate no Theatre</strong></article></div>
<h2>Checkpoint por seed</h2><table><tr><th>Seed</th><th>Braço/fase</th><th>Dia C/T</th><th>Calls C/T</th><th>Tokens C/T</th></tr>{rows}</table>
<h2>Causas preservadas</h2><ul>{causes}</ul>
<h2>Próxima fronteira</h2><p>As seeds 2201–2205 são imutáveis. Uma nova tentativa econômica exige protocolo separado, pré-registro e seeds novas; não existe retomada desta rodada.</p>
<h2>Integridade</h2><p>Manifesto da evidência: <code>{report['integrity']['evidence_manifest_sha256']}</code><br>Digest do relatório: <code>{report['integrity']['report_digest']}</code></p>
<h2>Limites honestos</h2><ul>{caveats}</ul></main></body></html>
"""


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_v2_campaign_bundle(
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
        _atomic_text(markdown_out, render_v2_campaign_markdown(report))
    if html_out is not None:
        _atomic_text(html_out, render_v2_campaign_html(report))
