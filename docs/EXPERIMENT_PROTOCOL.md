# Experiment protocol v1

Status: first 365-day pilot completed and verified; official manifest is not frozen yet.

## Question

Does persistent separation of criticism, planning, and execution improve the final economic result of the same frontier model in a year-long business simulation after charging the Theatre for its additional output?

## Model

GPT-5.6 Sol through ChatGPT/Codex OAuth in both arms. No API execution and no fallback model.

## Design

- Five paired seeds.
- Each seed runs once as control and once as Theatre.
- 365 simulated days or bankruptcy.
- Three-day operating cadence.
- Same scenario, model, thinking level, action contract, capacity, demand world, events, and supplier world within each pair.
- Serialized calls; Theatre receives no parallelism advantage.
- Only Personagem can alter the Theatre business.
- Crítico and Roteirista output is charged to Theatre.
- No prompt, cadence, scenario, scoring, or seed changes after the official manifest is frozen.
- Failed, paused, bankrupt, and low-scoring runs remain in the evidence.

## Primary metric

Final liquid cash after a virtual charge of US$100 per million output tokens.

## Analysis

- Show every pair before aggregation.
- Report mean and median paired difference.
- Report Theatre seed wins and reliability outcomes.
- Bootstrap the paired difference when five official pairs exist.
- Treat the result as inconclusive when the interval includes zero.
- Never replace the economic winner with a secondary or narrative winner.

## Quota policy

- Model inference uses only the included ChatGPT/Codex subscription route.
- Every inference records provider-reported tokens.
- The default runner has no invented token ceiling: it continues until the provider reports the real subscription quota or the pair completes.
- A local UTC-day safety budget remains available only as an explicit diagnostic override; it is disabled by default.
- Provider rate/quota rejection also pauses the run without changing its state.
- Work resumes on the next quota window from the persisted phase.
- Subscription remaining percentage is captured at batch boundaries because the provider does not expose a documented token-equivalent quota denominator.

## Gates before official runs

- economics and replay tests green;
- one paired 28-day smoke complete;
- one paired 365-day pilot complete (seed 1201; control won this pilot by US$2,578.97);
- prompts and scenario reviewed and frozen;
- public dashboard displays exact hashes and caveats;
- completed-pair report renderer passes without mutating evidence and publishes exact verified numbers;
- seeds and arm order pre-registered.
