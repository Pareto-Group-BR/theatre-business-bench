# Benchmark agent rules

This repository is an auditable business benchmark. Preserve experimental fairness above score.

- Never change a scenario, prompt, seed, scoring rule, or cadence after an official paired run begins.
- Never discard failed, bankrupt, interrupted, or low-scoring runs.
- Never use an API key for model execution. The runner must call the OpenClaw Gateway with ChatGPT/Codex OAuth.
- Never send messages, create external resources, browse the web, or edit repository files from benchmark-role sessions.
- Benchmark-role sessions return JSON only. The simulator is the sole authority over state transitions and money.
- Control and Theatre use the exact same model id, scenario snapshot, seed, time horizon, action schema, and tool limits.
- Only the Theatre actor can submit business actions. Critic and planner are advisory and their token usage counts.
- Record provider-reported token usage for every inference. Do not estimate tokens from character counts.
- A run may pause for quota and resume from its durable state without changing prompts or rules.

Verification command: `python -m unittest discover -s tests -v`.

