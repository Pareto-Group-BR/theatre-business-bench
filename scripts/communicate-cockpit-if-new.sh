#!/bin/sh
set -eu

pair_dir="runs/pairs/20260829T021739Z-pair-s1201"
cockpit="live-cockpit.json"
marker="$pair_dir/.cockpit-day-communicated"

if [ ! -f "$cockpit" ]; then
  exit 0
fi

current_day=$(jq -r '.pair.current_day' "$cockpit")
complete=$(jq -r '.pair.complete' "$cockpit")
communicated_day=$(test -f "$marker" && sed -n '1p' "$marker" || true)

# The final publisher owns the completion message. This checkpoint messenger
# only speaks after material progress and never duplicates the same day.
if [ "$complete" = "true" ] || [ "$current_day" = "$communicated_day" ]; then
  exit 0
fi

openclaw agent \
  --agent main \
  --session-key agent:main:theatre-business-bench-cockpit-communicator \
  --model openai/gpt-5.6-sol \
  --thinking medium \
  --timeout 600 \
  --json \
  --message 'Leia /data/.openclaw/workspace/projects/theatre-business-bench/live-cockpit.json. Se pair.complete for false, envie pelo tool message(action=send), em uma única DM WhatsApp ao Ramon (+5511997629243), uma prestação executiva curta em português: (1) até que dia dos 365 o piloto avançou; (2) caixa, receita, lucro bruto e margem dos dois braços; (3) diferença parcial Theatre − controle explicitamente não conclusiva; (4) link completo https://pareto-group-br.github.io/theatre-business-bench/#cockpit; (5) próxima retomada diária e estado In Progress no roadmap https://github.com/orgs/Pareto-Group-BR/projects/18. Comece pelo resultado concreto e não mencione PR, SHA, CI, IDs internos ou quantidade de testes. Não envie nada se pair.complete for true, pois o publicador final é responsável por essa mensagem.'

printf '%s\n' "$current_day" > "$marker"
