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

V2 preserves the v1 pilot as immutable history and freezes its own five seeds,
arm order, scenario, corpus, protocol, five prompts, cadence, model, thinking,
and action budget in `preregistration/v2.json`. Control and Theatre see the
same public state and schedule flags. Control performs all four responsibilities
in one response; Theatre performs strategic reviews in the causal order Crítico
→ Consciência when due → Roteirista → Personagem, with only Personagem allowed
to submit actions.

Pairs are created offline with frozen copies and a run-root provider ledger.
Activation accepts only an untouched pair and the exact clean commit currently
published at `origin/main`; it atomically marks the pair, both manifests and
the hashed receipt as official. The verifier requires that official identity at
every activated step and before reporting. `scripts/run-v2-batch.sh` adds one inherited global lock across all
official seeds, while `step_pair` balances and serializes the two arms within a
pair.

The runtime validates each role response before simulator execution. It
confronts correction requirements, plan ids and action types, execution
capacity, scheduled Consciência presence, and the final Personagem handoff. A
contract failure is preserved and stops loud. Each successful turn persists a
deterministic `decision_audit`; the verifier reconstructs cadence, handoffs,
actions, state, activation, frozen artifact hashes, and provider-ledger parity
without invoking a model.

### V3 lifecycle and contract boundary

V3 pair creation remains offline and inference-disabled. Its frozen contract
separates plan items into `now` and `conditional_future`. Immediate critical
items must execute in the same handoff; future items require an observable
precondition, are acknowledged by exact id, and are rejected if submitted
early. The same pure gate validates the combined control response and the
Theatre Roteirista→Personagem handoff.

V3 also freezes one structured repair per role invocation. The repair keeps
role, turn, and state identity, receives deterministic validation errors, and
must return a complete valid replacement. Both attempts are chargeable
evidence and no simulator transition may occur between them. The executor
freezes the exact preregistration, protocol, corpus, and six prompts into each
run; rejects any seed or runtime setting outside the frozen plan; and enables
inference only after an untouched pair is bound to the exact clean source
commit already published at `origin/main`.

Before each v3 provider attempt, `call-journal.jsonl` durably records its exact
role, turn, state, attempt kind, and serial. A completed attempt must have one
provider-usage row and one immutable decision or failure row. Structural
failure creates a `pending_invocation` bound to the original response and may
consume exactly one repair call. A quota pause between calls resumes only that
repair; an accepted response is recorded in `role-invocations.jsonl` before the
flow advances. The verifier reconstructs these bindings, rejects dangling or
duplicate attempts, and deterministically replays only accepted responses.
Parse failures, transport or provider-quota failures, model drift, and a second
structural failure become loud terminal evidence without a simulator turn.

If a gateway restart occurs between the journal `started` row and the runner's
completion write, an OpenClaw session can contain a second, automatic
continuation even though the benchmark process is gone. The verifier rejects
that incomplete journal by default. The only recovery path is the forensic
`reconcile-openclaw-v3-gateway-restart` transition: it requires both immutable
trajectory events and the full session log, proves the exact frozen repair
message immediately precedes the restart marker and returned bytes, charges the
completed continuation, and terminally records a transport-recovery failure.
No returned action is applied and the repair cannot be retried. The receipt
binds both gateway run ids, both source-file hashes, the completed event, the
three exact session messages, provider usage and the zero-turn/zero-decision
claim; `verify-pair` confronts those bindings offline.

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
- `model-failures.jsonl`: raw invalid model outputs that consumed provider quota but could not become decisions;
- `call-journal.jsonl`: write-ahead started/completed/transport-failed provider-attempt identity (v3);
- `role-invocations.jsonl`: accepted-first-pass, accepted-repair, or terminal-repair binding (v3);
- `turns.jsonl`: accepted/rejected actions and state hashes;
- `result.json`: final score and evidence hash.

If quota stops a cycle after Crítico but before Roteirista, `flow.json` resumes from Roteirista. Completed phases are never silently rerun.
If a provider call succeeds but its response is not valid JSON, the runner records
the provider-reported usage and exact raw response, marks the flow
`failed_contract`, and stops. Such a call is evidence and is never retried as if
it had not happened.

Calls made before that fail-closed recorder existed are never reconstructed by
hand. `reconcile-openclaw-failures` selects their immutable `model.completed`
events by gateway run id from one OpenClaw trajectory, verifies run/session,
provider, model, raw response, parse failure, and provider usage, then writes an
idempotent reconciliation receipt. The affected pair becomes terminal
`failed_contract`; the command adds no model decision, simulator turn, or
economic state transition. Repeated attempts at one phase are valid evidence
only when the verifier can bind all of them to that forensic receipt.

The canonical pilot runner holds a persistent workspace lock for the lifetime
of `pair-batch`. Official v2 and v3 runners share the same global lock, and the
CLI refuses an activated official pair without its inherited descriptor. The
file descriptor is inherited by the model-execution child,
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
