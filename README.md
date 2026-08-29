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
PYTHONPATH=src python -m theatre_business_bench.cli pair-batch --pair runs/pairs/<pair-id> --max-role-calls 10
PYTHONPATH=src python -m theatre_business_bench.cli pair-status --pair runs/pairs/<pair-id>
```

`simulate-policy` uses deterministic built-in policies to validate the economics without consuming model quota. `pair-batch` alternates the paired arms by simulated-day progress, invokes the configured Codex subscription transport, and stops cleanly at the local or provider quota boundary. State and provider-reported token use are persisted after every role call.

## Honest status

The simulator, subscription transport, 28-day paired smoke, and quota-safe paired runner exist. A result is official only after paired seeds, frozen manifest, full logs, and replay verification are published.

Sources:

- https://andonlabs.com/evals/vending-bench-2
- https://arxiv.org/abs/2502.15840
