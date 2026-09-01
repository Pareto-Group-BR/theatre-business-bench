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

The simulator, subscription transport, 28-day paired smoke, and the first
365-day paired pilot exist. Seed 1201 completed with replay and provider-usage
verification green; the single agent scored US$7,016.88 and Theatre scored
US$4,437.91 after the virtual compute charge. This is an honest pilot result,
not the official five-seed benchmark verdict.

- [Standalone pilot result](pilot-result.html)
- [Executive Markdown evidence](docs/PILOT_RESULT_1201.md)
- [Canonical machine-readable report](pilot-result.json)

An official result still requires the frozen manifest, five pre-registered
paired seeds, full logs, replay verification, and the planned paired analysis.

## Theatre v2 autonomous experiment

The autonomous v2 treatment was pre-registered and all five official seeds were
executed. Every pair terminated `failed_contract` before producing
`result.json`: the failing gate was in control for two seeds and in Theatre for
three. All five replays and the frozen-artifact audit pass, but there is **no
economic winner or aggregate**. Seeds 2201–2205 are immutable and must never be
resumed, recreated, deleted, or edited.

- [Verified terminal campaign evidence](v2-terminal-campaign.html)
- [Executive Markdown](docs/V2_TERMINAL_CAMPAIGN.md)
- [Canonical machine-readable evidence](v2-terminal-campaign.json)

The renderer is deliberately separate from `render-report`: it publishes
reliability, failure causes, days, calls, tokens, replay hashes, and evidence
digests while forcing every economic outcome field to remain null.

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli render-v2-campaign \
  --run-root /absolute/durable/path/theatre-business-bench-v2 \
  --json-out /tmp/v2-terminal-campaign.json \
  --markdown-out /tmp/V2_TERMINAL_CAMPAIGN.md \
  --html-out /tmp/v2-terminal-campaign.html
```

## V3 pre-registration and executor

V3 is a new, inference-free pre-registration motivated by the terminal v2
evidence. It never resumes or edits seeds 2201–2205. Five new seeds 2301–2305
freeze the same model, world, score, responsibilities, cadence, 14-action
limit, and arm information. The prospective contract adds two symmetric
mechanisms:

- plan items are classified as executable `now` or
  `conditional_future` with an observable precondition; all immediate critical
  work must execute in the current handoff, while future-dependent work is
  acknowledged and forbidden from early execution;
- each role invocation may receive exactly one paid, preserved structured
  repair after JSON-parseable contract failure. Parse/transport/quota failures
  and a second structural failure remain terminal.

The pre-registration, lifecycle executor, deterministic replay, and contract
gates are implemented and auditable without model execution:

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli audit-v3-preregistration
python3 -m unittest tests.test_v3 tests.test_v3_executor -v
```

State: `EXECUTOR_PUBLISHED / OFFICIAL_CAMPAIGN_RUNNING / NO_AGGREGATE`. Pair
creation freezes the exact v3 bytes and remains offline. Activation is a
separate audited transition that accepts only an untouched pair from seeds
2301–2305 and the exact clean commit published at `origin/main`. Seed 2301 was
created and activated from the published executor; partial checkpoints are not
results or winners.
See [`docs/EXPERIMENT_PROTOCOL_V3.md`](docs/EXPERIMENT_PROTOCOL_V3.md) and
[`preregistration/v3.json`](preregistration/v3.json).

After this executor is reviewed, merged, published on `main`, and a complete
official seed can be run as one indivisible unit, its lifecycle is:

```bash
run_root=/absolute/durable/path/theatre-business-bench-v3
PYTHONPATH=src python3 -m theatre_business_bench.cli create-pair \
  --protocol v3 --seed 2301 --run-root "$run_root"
source_commit=$(git rev-parse HEAD)
PYTHONPATH=src python3 -m theatre_business_bench.cli activate-v3-pair \
  --pair "$run_root/pairs/<pair-id>" --source-commit "$source_commit"
THEATRE_PAIR_DIR="$run_root/pairs/<pair-id>" ./scripts/run-v3-batch.sh
PYTHONPATH=src python3 -m theatre_business_bench.cli verify-pair \
  --pair "$run_root/pairs/<pair-id>"
```

The v2 and v3 wrappers share one workspace-global official-inference lock. The
CLI independently requires the inherited lock descriptor for every activated
official pair, preventing direct `pair-batch` use from bypassing serialization.
Each provider attempt has a write-ahead journal; every completed attempt is
bound to exactly one provider-usage row and one decision or failure row. V3
preserves the original response and permits one paid symmetric structural
repair with the same role, turn, state, and original-response identity. A
second failure, parse failure, transport failure, model drift, incomplete call
journal, or replay divergence stops loud before any simulator transition.

A gateway restart may kill the runner after the write-ahead `started` row while
OpenClaw later auto-continues the same role session under a second gateway run
id. That is not silently accepted as the frozen repair. The forensic command
below requires the exact trajectory **and** full session log, binds the original
repair prompt, the explicit restart message, the returned bytes and provider
usage, then terminally preserves the pair without applying a business action:

```bash
THEATRE_PAIR_DIR=/absolute/run-root/pairs/<pair-id> \
THEATRE_ARM=control \
THEATRE_TRAJECTORY=/absolute/openclaw/session.trajectory.jsonl \
THEATRE_SESSION_LOG=/absolute/openclaw/session.jsonl \
THEATRE_INTERRUPTED_RUN_ID=<started-without-completion> \
THEATRE_COMPLETED_RUN_ID=<auto-continuation-completion> \
  ./scripts/reconcile-v3-gateway-restart.sh
```

The wrapper holds the same global lock as official inference. The command first
persists a prepared receipt, resumes safely after interruption at any write
boundary, and is byte-idempotent for the same source bytes and ids. It charges the
completed continuation, records the interrupted id, adds zero accepted model
decisions and zero simulator turns, and leaves the seed `failed_contract` for
campaign reliability analysis. It never authorizes retrying that repair.

The frozen design gave the single-agent control and Theatre the same four
functional responsibilities, frozen evidence, and visible 14-action contract;
only cognitive organization differed. Theatre routed Crítico → optional
Consciência → Roteirista → Personagem on scheduled reviews, while control
carried the same responsibilities and schedule flags in one response.

The historical v2 lifecycle below is retained only as executor documentation.
Seeds 2201–2205 are terminal immutable evidence and these commands must never be
used to resume, recreate, activate, delete, or edit them.

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli audit-v2-preregistration

# Run only from a clean main checkout after this executor is published.
run_root=/absolute/durable/path/theatre-business-bench-v2
PYTHONPATH=src python3 -m theatre_business_bench.cli create-pair \
  --protocol v2 --seed 2201 --run-root "$run_root"
source_commit=$(git rev-parse HEAD)
PYTHONPATH=src python3 -m theatre_business_bench.cli activate-v2-pair \
  --pair "$run_root/pairs/<pair-id>" --source-commit "$source_commit"
THEATRE_PAIR_DIR="$run_root/pairs/<pair-id>" ./scripts/run-v2-batch.sh
```

Creation is offline-only: `pair-batch` cannot make a model call until activation
binds an untouched pair to the exact clean commit currently published at
`origin/main`. Activation also marks the pair, both run manifests, and its
receipt as official in one audited transition; `verify-pair` refuses any drift
before inference or publication. The shared official shell lock serializes
provider calls across all v2/v3 seeds. Every model response is contract-checked before simulator
execution; failed structure pauses loud as evidence. `verify-pair` independently
reconstructs role order, handoffs, decision audits, actions, replay hashes,
provider usage, and the run-root ledger before any resume. See
[`docs/EXPERIMENT_PROTOCOL_V2.md`](docs/EXPERIMENT_PROTOCOL_V2.md) and
[`preregistration/v2.json`](preregistration/v2.json).

For a provider call that predates the persisted-failure recorder, use the
narrow forensic command below instead of retrying or editing ledgers. It derives
the exact failed evidence from OpenClaw's trajectory and terminally marks the
official pair; it cannot apply actions or advance the simulator.

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli reconcile-openclaw-failures \
  --pair /absolute/run-root/pairs/<pair-id> --arm control \
  --trajectory /absolute/openclaw/session.trajectory.jsonl \
  --gateway-run-id <provider-run-id>
```

Sources:

- https://andonlabs.com/evals/vending-bench-2
- https://arxiv.org/abs/2502.15840

AI handoff:

- [`docs/AI_HANDOFF_VENDING_MACHINE.md`](docs/AI_HANDOFF_VENDING_MACHINE.md) — contexto autocontido, checkpoint, diagnóstico causal e protocolo seguro para outra IA.
