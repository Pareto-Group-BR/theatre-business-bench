# Theatre Business Bench

A deterministic, long-horizon business simulator for testing one question:

> Does the same frontier model produce better economic results when it operates as one generalist agent or through persistent Theatre roles?

The first scenario is a vending-machine business inspired by the public design of Andon Labs' Vending-Bench and Vending-Bench 2. This is an independent benchmark, not a reproduction of their private simulator.

## What is measured

- Primary: final liquid cash after virtual compute charge.
- Secondary: revenue, gross margin, stockouts, refunds, survival, supplier resilience, negotiation savings, and model-token use.
- Reliability: complete runs, bankruptcies, invalid actions, quota pauses, and replay hashes.

## Experimental arms

- `control`: GPT-5.6 Sol makes one weekly operating decision as a single agent.
- `theatre`: the same GPT-5.6 Sol acts as Critic, Planner, and Actor. Only Actor changes the business. Critic and Planner run every four weeks and after critical events.

All model calls go through the OpenClaw Gateway authenticated with ChatGPT/Codex OAuth. API keys are not used. Every call records provider-reported token usage.

## Quick start

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python -m theatre_business_bench.cli simulate-policy --arm control --seed 101 --days 365
PYTHONPATH=src python -m theatre_business_bench.cli create-pair --seed 101 --days 365
PYTHONPATH=src python -m theatre_business_bench.cli pair-batch --pair runs/pairs/<pair-id>
PYTHONPATH=src python -m theatre_business_bench.cli pair-status --pair runs/pairs/<pair-id>
PYTHONPATH=src python -m theatre_business_bench.cli verify-pair --pair runs/pairs/<pair-id>
PYTHONPATH=src python -m theatre_business_bench.cli render-report --pair runs/pairs/<pair-id> \
  --json-out pilot-result.json --markdown-out docs/PILOT_RESULT.md --html-out pilot-result.html
```

`simulate-policy` uses deterministic built-in policies to validate the economics without consuming model quota. `pair-batch` alternates the paired arms by simulated-day progress, invokes the configured Codex subscription transport, and runs until the pair completes or the provider reports the real subscription quota. There is no local token ceiling by default; `--daily-token-budget` and `--max-role-calls` are explicit diagnostic overrides. State and provider-reported token use are persisted after every role call.

`verify-pair` is read-only. It replays every submitted business action from the frozen scenario, confronts turn and final-state hashes, validates provider usage against model decisions and the global ledger, recalculates the economic score, and checks control/Theatre parity. It exits non-zero on any divergence and is safe to run while a pair is paused. The same gate runs automatically before every `pair-batch` resume and before final publication.

`render-report` is the fail-closed handoff from experimental evidence to executive evidence. It accepts only a completed pair that passes `verify-pair`, renders deterministic JSON, Markdown, and standalone HTML, refuses to write inside the immutable pair directory, and never calls a model or advances the simulation. A paused checkpoint is intentionally not reportable as a result.

## Live executive cockpit

The public page loads `live-cockpit.json`, generated only from a checkpoint
whose replay and provider-usage ledger pass verification. While a pair is not
complete, the cockpit is explicitly provisional: it exposes cash, revenue,
gross profit and margin, costs, purchases, inventory, losses, stockouts,
supplier outcomes, sales, and AI usage without projecting the year or naming a
final winner. The same verified replay also reconstructs an exact daily series
for liquid cash, cumulative revenue, cumulative gross profit, and provider
tokens. Token usage is posted on the first simulated day of each three-day
decision cycle; days without a new model call remain zero.

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli render-cockpit \
  --pair runs/pairs/20260829T021739Z-pair-s1201 \
  --json-out live-cockpit.json
```

The canonical durable runner is `scripts/run-pilot-batch.sh`. It owns a
workspace-persistent lock while `pair-batch` is alive, including when its
parent scheduler is interrupted. `scripts/publish-live-cockpit-if-idle.sh`
uses the same lock to recover and publish the latest verified checkpoint only
after the runner has released it. This keeps inference serialized while making
cockpit publication independently retryable. Neither script communicates a
partial result; human communication remains a separate, exactly-once boundary.

## Honest status

The simulator, subscription transport, 28-day paired smoke, and quota-safe paired runner exist. A result is official only after paired seeds, frozen manifest, full logs, and replay verification are published.

Sources:

- https://andonlabs.com/evals/vending-bench-2
- https://arxiv.org/abs/2502.15840
