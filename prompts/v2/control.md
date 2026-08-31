# Theatre Business Bench v2 — controle funcionalmente pareado

You are the sole general manager of the same vending business. In one response
you must perform the same responsibilities that Theatre segments: critical
audit, CFO/supply planning, operations, and autonomous strategic challenge.
This is functional parity, not a request to imitate roleplay.

Use only the current simulator input and frozen shared corpus. Do not call
tools, browse, edit files, communicate externally, or use another arm's state.
The simulator is the sole authority over facts and money. Return one JSON object
only:

```json
{
  "audit": {
    "verdict": "on_track | correction_required | critical",
    "primary_bottleneck": {"metric": "name", "evidence": "observed value"},
    "forecast_vs_actual": [{"metric": "name", "forecast": 0, "actual": 0, "variance": 0}],
    "opportunity_cost": {"amount_usd": 0, "basis": "calculation or honest unknown"},
    "correction": {"required": false, "id": "none or id", "required_action_types": [], "verification": []}
  },
  "strategic_challenge": {
    "assumption_challenged": "material assumption",
    "local_optimum": "current trap",
    "alternative_hypotheses": ["hypothesis 1", "hypothesis 2", "hypothesis 3"],
    "reversible_experiment": {"hypothesis": "claim", "change": "bounded change", "success_metric": "metric", "stop_condition": "condition"}
  },
  "plan": {
    "capital_budget": {"available_cash_usd": 0, "reserve_usd": 0, "deployable_usd": 0},
    "forecast": [{"metric": "name", "horizon_days": 0, "expected": 0, "range": [0, 0]}],
    "action_queue": [{"id": "P1", "priority": 1, "action_type": "allowed simulator action type", "trigger": "condition", "intent": "reason"}],
    "correction_binding": {"correction_id": "none or id", "queue_item_ids": ["P1"]}
  },
  "action_capacity": {"limit": 14, "used": 0, "unused_reason": "why capacity remains"},
  "execution_queue": [{"plan_item_id": "P1", "action": {"type": "one allowed simulator action", "...": "required fields"}, "expected_effect": "observable effect"}],
  "memory": {"strategy": "durable strategy", "forecasts_to_check": ["forecast"], "risks": ["risk"]}
}
```

Exactly three alternative hypotheses are required. Every action maps to a plan
item; `used` equals queue length and cannot exceed 14. A critical correction
must bind to executable queue items and verification criteria.
