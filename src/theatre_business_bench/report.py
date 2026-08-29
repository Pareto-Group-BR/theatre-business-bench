from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path
from typing import Any

from .runner import read_json
from .simulator import stable_hash
from .verify import verify_pair


class ReportGateError(RuntimeError):
    """Raised when evidence is not eligible for executive publication."""


def _run_path(pair_dir: Path, pair: dict[str, Any], arm: str) -> Path:
    value = pair.get(f"{arm}_run")
    if not isinstance(value, str):
        raise ReportGateError(f"pair is missing {arm}_run")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (pair_dir / path).resolve()


def build_executive_report(pair_dir: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    """Build a deterministic report from a completed, replay-verified pair.

    This function is deliberately read-only: it never advances a run, invokes a
    model, or writes inside the evidence tree.
    """

    pair_dir = pair_dir.resolve()
    verification = verify_pair(pair_dir, ledger_path)
    if verification["status"] != "passed":
        details = "; ".join(verification.get("errors", [])) or "unknown integrity failure"
        raise ReportGateError(f"pair integrity failed: {details}")

    pair_path = pair_dir / "pair.json"
    result_path = pair_dir / "result.json"
    if not result_path.is_file():
        raise ReportGateError("pair is not complete: missing result.json")

    pair = read_json(pair_path)
    result = read_json(result_path)
    if pair.get("status") != "completed":
        raise ReportGateError(f"pair is not complete: status={pair.get('status')!r}")

    arms: dict[str, dict[str, Any]] = {}
    for arm in ("control", "theatre"):
        run_dir = _run_path(pair_dir, pair, arm)
        manifest = read_json(run_dir / "manifest.json")
        state = read_json(run_dir / "state.json")
        run_result = read_json(run_dir / "result.json")
        if manifest.get("status") != "completed" or not state.get("terminated"):
            raise ReportGateError(f"{arm} run is not durably completed")
        if result.get(arm) != run_result:
            raise ReportGateError(f"pair result does not embed the exact {arm} result")

        score = run_result["score"]
        verified = verification["runs"][arm]
        arms[arm] = {
            "run_id": run_result["run_id"],
            "state_hash": run_result["state_hash"],
            "replay_state_hash": verified["replay_state_hash"],
            "primary_score": score["primary_score"],
            "liquid_cash": score["liquid_cash"],
            "virtual_compute_cost": score["virtual_compute_cost"],
            "revenue": score["revenue"],
            "gross_margin_pct": score["gross_margin_pct"],
            "units_sold": score["units_sold"],
            "refunds": score["refunds"],
            "stockout_product_days": score["stockout_product_days"],
            "invalid_actions": score["invalid_actions"],
            "days_survived": score["days_survived"],
            "termination_reason": score["termination_reason"],
            "model_calls": verified["model_calls"],
            "provider_total_tokens": verified["provider_total_tokens"],
            "output_tokens": verified["output_tokens"],
        }

    official = bool(pair.get("official"))
    normalized_verification = {
        "status": verification["status"],
        "pair_id": verification["pair_id"],
        "pair_status": verification["pair_status"],
        "runs": verification["runs"],
        "errors": verification["errors"],
    }
    report = {
        "schema_version": 1,
        "report_type": "official_pair" if official else "pilot_non_official",
        "claim": "oficial" if official else "piloto; não é um resultado oficial do benchmark",
        "pair": {
            "pair_id": pair["pair_id"],
            "seed": pair["seed"],
            "days": pair["days"],
            "model": pair["model"],
            "thinking": pair["thinking"],
            "finished_at": result["finished_at"],
            "official": official,
        },
        "outcome": {
            "winner": result["winner"],
            "paired_difference_theatre_minus_control": result[
                "paired_difference_theatre_minus_control"
            ],
        },
        "arms": arms,
        "integrity": {
            "status": "passed",
            "verification_digest": stable_hash(normalized_verification),
            "pair_result_digest": stable_hash(result),
        },
        "caveats": [
            "Este par é um piloto e não substitui o experimento oficial pré-registrado de cinco seeds."
            if not official
            else "Este par é uma observação do experimento oficial pré-registrado.",
            "Um resultado pareado permite comparar esta seed; sozinho, não estabelece superioridade geral.",
            "O uso de tokens reportado pelo provedor é cobrado do braço que o consumiu; todo output dos papéis Theatre conta.",
            "A integridade do replay prova consistência interna da evidência preservada, não validade externa de negócio.",
        ],
    }
    report["integrity"]["report_digest"] = stable_hash(report)
    return report


def _money(value: Any) -> str:
    amount = float(value)
    return f"{'-' if amount < 0 else ''}US${abs(amount):,.2f}"


def render_markdown(report: dict[str, Any]) -> str:
    pair = report["pair"]
    outcome = report["outcome"]
    control = report["arms"]["control"]
    theatre = report["arms"]["theatre"]
    winner = {"control": "Agente único", "theatre": "Theatre", "tie": "Empate"}[outcome["winner"]]
    result_label = "resultado oficial" if pair["official"] else "resultado do piloto"
    caveats = "\n".join(f"- {item}" for item in report["caveats"])
    return f"""# Theatre Business Bench — {result_label}

> **Estado da alegação:** {report['claim']}.

## Resultado econômico

- **Vencedor desta seed:** {winner}
- **Diferença Theatre − agente único:** {_money(outcome['paired_difference_theatre_minus_control'])}
- **Agente único:** {_money(control['primary_score'])} após custo virtual
- **Theatre:** {_money(theatre['primary_score'])} após custo virtual

## Par confrontado

- Pair: `{pair['pair_id']}`
- Seed: `{pair['seed']}` · horizonte: `{pair['days']}` dias
- Modelo: `{pair['model']}` · thinking: `{pair['thinking']}`
- Encerrado em: `{pair['finished_at']}`
- Integridade: `passed` · report digest: `{report['integrity']['report_digest']}`

## Evidência por braço

| Métrica | Agente único | Theatre |
|---|---:|---:|
| Score primário | {_money(control['primary_score'])} | {_money(theatre['primary_score'])} |
| Caixa líquido | {_money(control['liquid_cash'])} | {_money(theatre['liquid_cash'])} |
| Receita | {_money(control['revenue'])} | {_money(theatre['revenue'])} |
| Margem bruta | {control['gross_margin_pct']:.2f}% | {theatre['gross_margin_pct']:.2f}% |
| Unidades vendidas | {control['units_sold']} | {theatre['units_sold']} |
| Chamadas de modelo | {control['model_calls']} | {theatre['model_calls']} |
| Tokens reportados | {control['provider_total_tokens']} | {theatre['provider_total_tokens']} |
| Tokens de saída cobrados | {control['output_tokens']} | {theatre['output_tokens']} |
| Ações inválidas | {control['invalid_actions']} | {theatre['invalid_actions']} |

## Limites honestos

{caveats}
"""


def render_html(report: dict[str, Any]) -> str:
    pair = report["pair"]
    outcome = report["outcome"]
    control = report["arms"]["control"]
    theatre = report["arms"]["theatre"]
    winner = {"control": "Agente único", "theatre": "Theatre", "tie": "Empate"}[outcome["winner"]]
    result_label = "Resultado oficial" if pair["official"] else "Resultado do piloto"
    eyebrow = "Rodada oficial" if pair["official"] else "Piloto anual"
    caveats = "".join(f"<li>{html.escape(str(item))}</li>" for item in report["caveats"])
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{result_label} — Theatre Business Bench</title>
<style>body{{margin:0;background:#f4f0e7;color:#151a22;font:16px/1.5 system-ui,sans-serif}}main{{width:min(920px,calc(100% - 32px));margin:auto;padding:48px 0}}.eyebrow{{color:#bd3a25;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}h1{{font-size:clamp(38px,7vw,68px);line-height:1;margin:.2em 0}}.warning{{padding:16px;border-left:5px solid #8b5b10;background:#f6e7c3}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:28px 0}}article{{padding:22px;border:1px solid #d8d1c4;border-radius:18px;background:#fffdf8}}.money{{font-size:40px;font-weight:900}}table{{width:100%;border-collapse:collapse;background:#fffdf8}}th,td{{padding:12px;border-bottom:1px solid #d8d1c4;text-align:right}}th:first-child,td:first-child{{text-align:left}}code{{overflow-wrap:anywhere}}@media(max-width:650px){{.grid{{grid-template-columns:1fr}}table{{font-size:13px}}}}</style></head>
<body><main><div class="eyebrow">{eyebrow} · evidência verificada</div><h1>{html.escape(winner)} venceu esta seed.</h1>
<p class="warning"><strong>Leitura correta:</strong> {html.escape(report['claim'])}. Diferença Theatre − agente único: {_money(outcome['paired_difference_theatre_minus_control'])}.</p>
<div class="grid"><article><strong>Agente único</strong><div class="money">{_money(control['primary_score'])}</div><p>{control['provider_total_tokens']:,} tokens reportados · {control['model_calls']} chamadas</p></article><article><strong>Theatre</strong><div class="money">{_money(theatre['primary_score'])}</div><p>{theatre['provider_total_tokens']:,} tokens reportados · {theatre['model_calls']} chamadas</p></article></div>
<h2>Evidência econômica</h2><table><tr><th>Métrica</th><th>Agente único</th><th>Theatre</th></tr><tr><td>Caixa líquido</td><td>{_money(control['liquid_cash'])}</td><td>{_money(theatre['liquid_cash'])}</td></tr><tr><td>Receita</td><td>{_money(control['revenue'])}</td><td>{_money(theatre['revenue'])}</td></tr><tr><td>Margem bruta</td><td>{control['gross_margin_pct']:.2f}%</td><td>{theatre['gross_margin_pct']:.2f}%</td></tr><tr><td>Tokens de saída cobrados</td><td>{control['output_tokens']:,}</td><td>{theatre['output_tokens']:,}</td></tr><tr><td>Ações inválidas</td><td>{control['invalid_actions']}</td><td>{theatre['invalid_actions']}</td></tr></table>
<h2>Integridade</h2><p>Pair <code>{html.escape(str(pair['pair_id']))}</code> · seed {pair['seed']} · {pair['days']} dias · replay <strong>passed</strong>.</p><p>Digest do relatório: <code>{report['integrity']['report_digest']}</code></p>
<h2>Limites honestos</h2><ul>{caveats}</ul></main></body></html>
"""


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_report_bundle(
    pair_dir: Path,
    report: dict[str, Any],
    *,
    json_out: Path | None = None,
    markdown_out: Path | None = None,
    html_out: Path | None = None,
) -> None:
    pair_root = pair_dir.resolve()
    outputs = [path.resolve() for path in (json_out, markdown_out, html_out) if path is not None]
    if len(outputs) != len(set(outputs)):
        raise ReportGateError("report outputs must use distinct paths")
    for output in outputs:
        if output == pair_root or pair_root in output.parents:
            raise ReportGateError("report output must be outside the immutable pair evidence directory")
    if json_out is not None:
        _atomic_text(json_out, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    if markdown_out is not None:
        _atomic_text(markdown_out, render_markdown(report))
    if html_out is not None:
        _atomic_text(html_out, render_html(report))
