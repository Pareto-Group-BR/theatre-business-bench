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

The canonical pilot runner holds a persistent workspace lock for the lifetime
of `pair-batch`. The file descriptor is inherited by the model-execution child,
so a scheduler or gateway interruption cannot make a still-running batch look
idle. Checkpoint publication is a separate idempotent process that acquires the
same lock, verifies the replay, renders the cockpit atomically, and pushes only
when the verified JSON changed. It may therefore recover observability after a
detached batch exits without starting inference or racing mutable evidence.

The read-only `verify-pair` gate independently reconstructs both arms from the frozen scenario and each recorded model decision. It confronts accepted/rejected actions, per-turn replay hashes, the persisted state, prompt/scenario hashes, model identity, provider-reported usage, the global usage ledger, arm parity, and any final score. It never advances the simulator or calls a model. `pair-batch` runs it before resuming, and the completion publisher runs it again before exposing a result; divergence stops both paths without changing the checkpoint.

Durable role calls read `prompt-<role>.md` from the run directory, never the
mutable repository prompt. The manifest hash and the bytes actually sent to a
role therefore cannot diverge after a prompt merge.

### Causal follow-up lane

An in-flight paired arm is immutable even when its behavior reveals a plausible
cause of underperformance. `create-causal-fork` first requires a replay-green,
clean `prepare_turn` checkpoint, then atomically creates:

- an immutable byte-exact source checkpoint;
- an active Theatre clone with the entire replay prefix;
- a new role-session namespace;
- a hash-bound operator-supplied Consciousness intervention;
- an outer manifest marked `assisted_exploratory_non_scoring` and
  `scoring_eligible: false`.

`verify-causal-fork` audits both replays, source digests, append-only history
prefixes, frozen prompt/scenario parity, session isolation and intervention
binding. The fork contains no paired-result manifest, and `verify-pair`
explicitly rejects a non-scoring arm. `causal-batch` is the only supported
execution boundary: it re-runs the audit, requires an explicit non-scoring
acknowledgement and acquires a caller-supplied shared runner lock before a model
call. This prevents concurrency; quota permission remains an operator decision.

The v2 registration boundary is later and separate. It accepts only a completed
replay-green fork, exactly five unused seeds, a changed scenario or prompt set,
and explicit protocol bytes. The resulting artifact is
`preregistered_not_started`; it cannot retroactively score the exploration or
change v1. See [`CAUSAL_FOLLOWUP.md`](CAUSAL_FOLLOWUP.md).

The publication boundary is also deterministic. `render-report` consumes only a completed pair after `verify-pair` passes, confronts the embedded pair result with both exact run results, and produces a canonical JSON report plus derived Markdown and standalone HTML. It refuses partial checkpoints and refuses output paths inside the immutable evidence directory. The completion publisher renders these artifacts before asking the reporting agent to publish them; a durable publication marker is accepted only after the agent confirms push, green CI/Pages, roadmap evidence, and the final executive communication.

## Score

Primary score:

`liquid cash − (output tokens / 1,000,000 × US$100)`

Liquid cash is bank cash plus uncollected machine cash. Unsold inventory is disclosed at weighted book value but does not inflate the primary score.

Secondary metrics explain the result without replacing it: revenue, cost of goods sold, gross margin, stockouts, refunds, supplier losses, invalid actions, survival, and token use.
