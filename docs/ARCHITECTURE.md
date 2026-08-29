# Architecture

## Business process

Each run starts with the same scenario snapshot and one deterministic seed. At every decision boundary, the model receives the observable business state and returns structured actions. The simulator validates the actions, advances the economy, records evidence, and persists a replay hash.

The world includes:

- price-elastic customer demand;
- weekday, season, weather, and assortment effects;
- bank cash separated from cash inside the machine;
- inventory capacity and weighted cost basis;
- supplier discovery, negotiation, lead times, reliability, partial refunds, and bait-and-switch behavior;
- customer complaints and refunds;
- daily operating fees and a ten-day bankruptcy rule.

## Experimental arms

### Control

One persistent GPT-5.6 Sol session receives the state and combines diagnosis, planning, memory, and execution in one response.

### Theatre

- Crítico: judges evidence, contradictions, drift, and economics.
- Roteirista: turns criticism into a four-week plan with thresholds and triggers.
- Personagem: the only role allowed to submit business actions.
- Ponte: deterministic runner that routes state and persists each phase. It has no business judgment.

The Personagem runs at every three-day decision boundary. Crítico and Roteirista run at the start, every four simulated weeks, and after critical events.

## Subscription transport

The runner invokes `openclaw agent` through the Gateway with:

- dedicated isolated agent `business-bench`;
- OAuth profile `openai:ramon@pareto.io`;
- model `openai/gpt-5.6-sol`;
- explicit role-specific session keys;
- JSON envelope and provider-reported usage.

No API key is passed to a model invocation. Each call records input, cached input, output, total tokens, provider, model, duration, role, run, seed, and response hash.

## Durable execution

Every run owns:

- `manifest.json`: frozen scenario/prompt/model hashes;
- `scenario.json`: exact economic world;
- `state.json`: authoritative simulator state;
- `flow.json`: resumable phase (`critic`, `planner`, `actor`, or `control`);
- `role-memory.json`: last accepted role outputs;
- `usage.jsonl`: token ledger;
- `model-decisions.jsonl`: immutable model outputs;
- `turns.jsonl`: accepted/rejected actions and state hashes;
- `result.json`: final score and evidence hash.

If quota stops a cycle after Crítico but before Roteirista, `flow.json` resumes from Roteirista. Completed phases are never silently rerun.

The read-only `verify-pair` gate independently reconstructs both arms from the frozen scenario and each recorded model decision. It confronts accepted/rejected actions, per-turn replay hashes, the persisted state, prompt/scenario hashes, model identity, provider-reported usage, the global usage ledger, arm parity, and any final score. It never advances the simulator or calls a model.

## Score

Primary score:

`liquid cash − (output tokens / 1,000,000 × US$100)`

Liquid cash is bank cash plus uncollected machine cash. Unsold inventory is disclosed at weighted book value but does not inflate the primary score.

Secondary metrics explain the result without replacing it: revenue, cost of goods sold, gross margin, stockouts, refunds, supplier losses, invalid actions, survival, and token use.
