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

### Autonomous v2 profile

V2 is isolated from v1 by a separate scenario, five prompts, functional
contract, corpus and pre-registration. Both arms perform the same four
functions at every decision. Control returns those functions as four sections
in one persistent-session response; Theatre routes the same shared evidence
through persistent Crítico, Roteirista, Consciência and Personagem sessions.

`v2.audit_v2_bundle` is the common authority boundary. It requires every
critical correction to map to a simulator-action queue, every correction to be
reviewed by Consciência and every required queue item to be executed or blocked
by an exact public-state value plus a matching zero-day simulator rejection. It compiles the only action list passed to the
simulator and is persisted as `turns.jsonl[].decision_audit`. The verifier
reconstructs this audit from immutable model decisions before replaying actions.

V2 pairs are born with inference disabled. `activate-v2-pair` accepts only an
exact clean source commit whose frozen SHA-256 values match the checked-in
pre-registration and refuses any existing inference evidence. Direct `step`
also checks the activation receipt, so editing one boolean is insufficient to
bypass the gate.

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

The canonical pilot runner holds a persistent workspace lock for the lifetime
of `pair-batch`. The file descriptor is inherited by the model-execution child,
so a scheduler or gateway interruption cannot make a still-running batch look
idle. Checkpoint publication is a separate idempotent process that acquires the
same lock, verifies the replay, renders the cockpit atomically, and pushes only
when the verified JSON changed. It may therefore recover observability after a
detached batch exits without starting inference or racing mutable evidence.

The read-only `verify-pair` gate independently reconstructs both arms from the frozen scenario and each recorded model decision. It confronts accepted/rejected actions, per-turn replay hashes, the persisted state, prompt/scenario hashes, model identity, provider-reported usage, the global usage ledger, arm parity, and any final score. It never advances the simulator or calls a model. `pair-batch` runs it before resuming, and the completion publisher runs it again before exposing a result; divergence stops both paths without changing the checkpoint.

The publication boundary is also deterministic. `render-report` consumes only a completed pair after `verify-pair` passes, confronts the embedded pair result with both exact run results, and produces a canonical JSON report plus derived Markdown and standalone HTML. It refuses partial checkpoints and refuses output paths inside the immutable evidence directory. The completion publisher renders these artifacts before asking the reporting agent to publish them; a durable publication marker is accepted only after the agent confirms push, green CI/Pages, roadmap evidence, and the final executive communication.

## Score

Primary score:

`liquid cash − (output tokens / 1,000,000 × US$100)`

Liquid cash is bank cash plus uncollected machine cash. Unsold inventory is disclosed at weighted book value but does not inflate the primary score.

Secondary metrics explain the result without replacing it: revenue, cost of goods sold, gross margin, stockouts, refunds, supplier losses, invalid actions, survival, and token use.
