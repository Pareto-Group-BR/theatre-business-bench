#!/bin/sh
set -eu

pair_dir="runs/pairs/20260829T021739Z-pair-s1201"
marker="$pair_dir/.executive-report-published"

if [ ! -f "$pair_dir/result.json" ] || [ -f "$marker" ]; then
  exit 0
fi

# Publication is fail-closed: replay and confront the completed evidence before
# starting the reporting agent or creating the exactly-once marker.
PYTHONPATH=src python3 -m theatre_business_bench.cli verify-pair --pair "$pair_dir" >/dev/null

openclaw agent \
  --agent main \
  --session-key agent:main:theatre-business-bench-pilot-publisher \
  --model openai/gpt-5.6-sol \
  --thinking high \
  --timeout 3600 \
  --json \
  --message 'O piloto anual do Theatre Business Bench terminou. Trabalhe em /data/.openclaw/workspace/projects/theatre-business-bench. Leia completamente AGENTS.md e os artefatos do par runs/pairs/20260829T021739Z-pair-s1201. Execute Crítico: valide integridade, tokens, hashes, paridade e replay; não altere o protocolo nem esconda falhas. Monte o relatório executivo com resultado econômico controle versus Theatre, métricas, ressalvas e decisão. Atualize docs e index.html público, issue #1 e Project #18; configure git Vilfredo <vilfredo@pareto.io>, teste, commit, push e valide CI/Pages. Marque claramente como piloto não oficial. Envie uma única prestação executiva em português ao Ramon por DM com URL pública e roadmap. Não crie API spend.'

touch "$marker"
