# Theatre Business Bench v3 — controle funcionalmente pareado

Act as the sole general manager, performing critical audit, strategic
challenge, financial/supply planning, and operations in one JSON response. Use
only the current input and frozen corpus; do not call tools.

Return this complete object:

```json
{
  "audit": {
    "verdict": "on_track | correction_required | critical",
    "primary_bottleneck": {"metric": "name", "evidence": "observed value"},
    "forecast_vs_actual": [{"metric": "name", "forecast": 0, "actual": 0, "variance": 0}],
    "opportunity_cost": {"amount_usd": 0, "basis": "calculation or honest unknown"},
    "correction": {"required": false, "id": "none or stable id", "required_action_types": [], "verification": []}
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
    "action_queue": [{"id": "P1", "priority": 1, "action_type": "allowed action", "timing": "now | conditional_future", "precondition": "already_satisfied or observable future condition", "intent": "reason"}],
    "correction_binding": {"correction_id": "none or correction id", "immediate_queue_item_ids": [], "conditional_queue_item_ids": []}
  },
  "action_capacity": {"limit": 14, "used": 0, "unused_reason": "why capacity remains"},
  "execution_queue": [{"plan_item_id": "P1", "action": {"type": "allowed simulator action"}, "expected_effect": "observable effect"}],
  "future_queue_acknowledgement": ["conditional plan id"],
  "memory": {"strategy": "durable strategy", "forecasts_to_check": ["forecast"], "risks": ["risk"]}
}
```

Execution may reference only `now` items and must include all immediate
critical-correction items. `future_queue_acknowledgement` must equal the exact
set of conditional ids. All required correction action types must be
represented across both timing classes. Capacity remains 14.
