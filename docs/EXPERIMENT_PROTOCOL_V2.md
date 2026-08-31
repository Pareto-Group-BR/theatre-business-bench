# Experiment protocol v2 — autonomous functional-parity Theatre

Status: **prepared technically and pre-registered; zero v2 model inference**.

This protocol belongs to improvement
[theatre-business-bench#7](https://github.com/Pareto-Group-BR/theatre-business-bench/issues/7)
inside economic-proof epic #1. It does not replace, continue, or rewrite pilot
seed 1201. Live internet belongs to future experiment #8 and is forbidden here.

## Research question

Does the same model produce a better final economic result when one persistent
general manager performs every necessary management function, or when those
same functions are separated into persistent specialist roles?

The intended treatment is cognitive organization, not additional knowledge,
authority, tools or business capacity.

## Functional parity

Both arms must perform exactly four functions at every three-day decision:

1. critical business review;
2. financial control and supply planning;
3. autonomous strategic challenge;
4. operational execution.

### Control

One persistent general-manager session produces four explicit sections in one
response: `critical_review`, `operating_plan`, `strategic_review` and
`execution`. Only `execution` may change the simulator.

### Theatre

Four persistent sessions execute in this exact order:

```text
Crítico → Roteirista CFO/controller/supply planner
        → Consciência autônoma strategist → Personagem operational manager
        → deterministic audit → simulator
```

Only the Personagem response may change the simulator. The Ponte is code: it
routes, validates and persists but contributes no business judgment.

## Same inputs, tools, budget and authority

Every function in both arms receives the same `shared_evidence`:

- complete public state from its own arm;
- complete action contract and explicit 14-action limit;
- byte-identical frozen domain corpus;
- byte-identical functional contract;
- the prior structured cycle audit from the same arm.

Theatre specialists additionally receive only the upstream work products from
their own current cycle. The control creates those same work products inside
one response. Neither arm sees the other arm.

Both arms use `openai/gpt-5.6-sol`, thinking `medium`, the same OAuth route,
same scenario cash/capacity/suppliers, no local token ceiling, no tools and no
internet. Provider-reported consumption is fully recorded. The same virtual
output-token price is applied, so extra Theatre computation is a cost rather
than a hidden advantage.

## Critical feedback reaches execution

The v1 label `critical` was advisory text. In v2 it creates an auditable state
transition:

1. every non-on-track verdict has one or more unique correction IDs;
2. the Roteirista maps every correction ID to at least one exact simulator
   action in an execution queue;
3. Consciência reviews every correction and names the queue items required at
   execution;
4. Personagem executes each required queue ID or grounds a blockage in an
   exact value at a real public-state path plus the exact rejection from a
   zero-day simulator preflight; an executable action cannot be called blocked;
5. the deterministic audit compiles the final action list, records correction
   coverage, executed/blocked IDs and remaining action capacity;
6. missing, invented or self-declared-only adherence stops the run loudly as
   `failed_contract`; it is never silently retried or discarded.

The same audit validates the four-section control response. It does not award
Theatre semantic leniency or manufacture a win.

## Frozen artifacts and pre-registration

The machine-readable source of truth is
[`protocols/v2/preregistration.json`](../protocols/v2/preregistration.json).
It freezes:

- scenario v2 and its exposed action budget;
- functional contract;
- knowledge corpus;
- all five prompts;
- model, thinking, horizon, cadence, metrics and stopping rules;
- five new paired seeds: 2101, 2203, 2309, 2411 and 2521;
- alternating first arm: control, Theatre, control, Theatre, control.

Every artifact is bound by SHA-256. Seed 1201 and smoke seed 1101 are forbidden
for v2. No model call may occur while a pair is offline-only.

## Reproducible journey

### 1. Verify the published design without inference

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli verify-v2-preregistration
python3 -m unittest discover -s tests -v
```

### 2. Materialize an offline pair

```bash
trial_root="$(mktemp -d)"
PYTHONPATH=src python3 -m theatre_business_bench.cli create-pair \
  --protocol v2 --seed 2101 --run-root "$trial_root"
```

This freezes both runs but sets `inference_enabled=false`. Calling `step` or
`pair-batch` returns `blocked_preregistration` before constructing a model call.
`verify-pair` can already confront world, prompts, corpus and arm parity.

### 3. Activate only a clean published source commit

After the PR is reviewed, merged and checked out cleanly, record the full commit
that contains the published pre-registration:

```bash
source_commit="$(git rev-parse HEAD)"
PYTHONPATH=src python3 -m theatre_business_bench.cli activate-v2-pair \
  --pair "$trial_root/pairs/<pair-id>" --source-commit "$source_commit"
```

Activation refuses a dirty checkout, a different HEAD, a reused/non-registered
seed, pre-existing inference evidence or any changed frozen hash. It writes one
activation receipt and binds both run manifests to those exact bytes.

### 4. Execute serially and verify

```bash
PYTHONPATH=src python3 -m theatre_business_bench.cli pair-batch \
  --pair "$trial_root/pairs/<pair-id>"
PYTHONPATH=src python3 -m theatre_business_bench.cli verify-pair \
  --pair "$trial_root/pairs/<pair-id>" \
  --ledger "$trial_root/usage-ledger.jsonl"
```

The canonical production run must use one shared lock across all five pairs.
This protocol intentionally does not launch that run and does not create a
schedule before source review/merge.

## Metrics and stopping

Primary metric remains final liquid cash minus the identical virtual
output-token charge. Show all five pairs, mean and median Theatre-minus-control
difference, seed wins, reliability outcomes and a paired bootstrap interval.
An interval including zero is inconclusive.

Runs end at day 365 or bankruptcy. Provider quota pauses preserve the exact
phase. Contract failures, bankruptcies, interruptions and low scores remain in
evidence. No prompt, scenario, corpus, seed, cadence, score or gate may change
after activation.

## Honest boundary

This makes the v2 journey reproducible and testable offline. It does not yet
produce an economic result. The first v2 inference is a later serialized
operation after the source commit has passed review and activation gates.
