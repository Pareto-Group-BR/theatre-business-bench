# Causal follow-up lane

This lane exists to learn from a live pilot without rewriting that pilot.
It is deliberately separate from the paired-result pipeline.

## Why it exists

At the verified day-189 checkpoint of seed 1201, the Theatre roles repeatedly
acted as if only three actions were available per turn. The frozen scenario
allows fourteen. The Crítico detected inventory and availability failures, but
the false constraint propagated through the Roteirista and reduced execution
throughput. The control arm did not share the same operational behavior.

That is a causal hypothesis, not a license to repair the arm in place. Changing
the current prompt, role memory, checkpoint, action contract, scenario or score
would invalidate the v1 pilot.

## The honest sequence

1. **Preserve v1.** The source pair must pass `verify-pair` at a clean
   `prepare_turn` checkpoint. The fork creator never writes to it.
2. **Create an assisted exploration.** `create-causal-fork` stores two copies:
   an immutable source checkpoint and an active clone. The clone keeps the
   replayed history, receives a new role-session namespace, binds the exact
   operator-supplied Consciousness bytes and is marked
   `assisted_exploratory_non_scoring` / `scoring_eligible: false`.
3. **Run only under the shared lock.** `causal-batch` first replays and audits
   both copies, requires an explicit non-scoring acknowledgement and acquires
   the same lock used to serialize the official runner. It cannot race the
   source pilot. Its token use remains provider-reported and real.
4. **Treat the outcome as diagnostic.** The fork has no `pair.json`; the paired
   verifier rejects any non-scoring arm if someone tries to introduce one. It
   cannot enter the dashboard, official aggregate or v1 result.
5. **Pre-register v2 later.** `preregister-v2` remains fail-closed until the
   exploratory run is completed and replay-verified. It then requires exactly
   five unused positive seeds plus explicit v2 scenario, prompt and protocol
   bytes. The source seed and exploratory checkpoint stay excluded.

## Offline creation and verification

These commands do not invoke a model:

```bash
PYTHONPATH=src python -m theatre_business_bench.cli create-causal-fork \
  --source-pair runs/pairs/<v1-pair> \
  --shared-lock .runtime/pilot-s1201.lock \
  --will "<operator-supplied Consciousness directive>" \
  --hypothesis "<causal hypothesis>"

PYTHONPATH=src python -m theatre_business_bench.cli verify-causal-fork \
  --fork runs/exploratory/<fork-id>
```

The intervention file binds the supplied bytes to source pair, run and day. It
does not independently prove who typed them; preserve the human-channel
evidence separately and never describe the fork as autonomous.

## Later, explicitly non-scoring execution

```bash
PYTHONPATH=src python -m theatre_business_bench.cli causal-batch \
  --fork runs/exploratory/<fork-id> \
  --shared-lock .runtime/pilot-s1201.lock \
  --confirm-non-scoring \
  --max-role-calls <bounded-call-count> \
  --daily-token-budget <explicit-cadence-budget>
```

Do not run this beside the official batch or merely because quota exists. The
operator chooses a later serialized window. The shared lock is a collision
guard, not permission to spend quota.

## v2 pre-registration gate

```bash
PYTHONPATH=src python -m theatre_business_bench.cli preregister-v2 \
  --fork runs/exploratory/<completed-fork-id> \
  --seeds <s1> <s2> <s3> <s4> <s5> \
  --scenario <v2-scenario.json> \
  --prompt-dir <v2-prompts/> \
  --protocol <v2-protocol.md> \
  --output <preregistration-v2.json>
```

The command refuses an unfinished fork, reused seed, unchanged v1 design or an
existing output path. A generated registration means “frozen and not started,”
not “approved,” “executed” or “won.”

## What remains invariant

- seed 1201 files, prompts, scenario, state, score and schedule are untouched;
- the official runner keeps its original session keys;
- an exploratory role cannot resume an official role session;
- frozen prompt snapshots, rather than mutable repository prompts, are used by
  every durable role call;
- no causal fork can cross the official paired-result verifier.
