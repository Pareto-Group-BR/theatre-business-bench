#!/bin/sh
set -eu

pair_dir="runs/pairs/20260829T021739Z-pair-s1201"
marker="$pair_dir/.executive-report-published"
report_json="pilot-result.json"
report_markdown="docs/PILOT_RESULT_1201.md"
report_html="pilot-result.html"

if [ ! -f "$pair_dir/result.json" ] || [ -f "$marker" ]; then
  exit 0
fi

# Publication is fail-closed: replay and confront the completed evidence before
# starting the reporting agent or creating the exactly-once marker.
PYTHONPATH=src python3 -m theatre_business_bench.cli verify-pair --pair "$pair_dir" >/dev/null
PYTHONPATH=src python3 -m theatre_business_bench.cli render-report \
  --pair "$pair_dir" \
  --json-out "$report_json" \
  --markdown-out "$report_markdown" \
  --html-out "$report_html" >/dev/null

openclaw agent \
  --agent main \
  --session-key agent:main:theatre-business-bench-pilot-publisher \
  --model openai/gpt-5.6-sol \
  --thinking high \
  --timeout 3600 \
  --json \
  --message 'O piloto anual do Theatre Business Bench terminou. Trabalhe em /data/.openclaw/workspace/projects/theatre-business-bench. Leia completamente AGENTS.md e os artefatos do par runs/pairs/20260829T021739Z-pair-s1201. O gate determinístico já produziu pilot-result.json, docs/PILOT_RESULT_1201.md e pilot-result.html a partir do replay verificado; trate esses arquivos como fonte canônica e não recalcule números narrativamente. Execute Crítico: reconfirme integridade, tokens, hashes, paridade e replay; não altere o protocolo nem esconda falhas. Ligue pilot-result.html ao index.html, atualize issue #1 e Project #18; configure git Vilfredo <vilfredo@pareto.io>, teste, commit, push e valide CI/Pages. Marque claramente como piloto não oficial. Envie uma única prestação executiva em português ao Ramon por DM com a URL pública do resultado e o roadmap. Não crie API spend. SOMENTE depois de push, CI/Pages verdes, GitHub atualizado e DM enviada, crie runs/pairs/20260829T021739Z-pair-s1201/.executive-report-published. Se qualquer etapa falhar, não crie o marcador para permitir nova tentativa.'

if [ ! -f "$marker" ]; then
  echo "publication agent did not confirm the complete publication receipt" >&2
  exit 1
fi
