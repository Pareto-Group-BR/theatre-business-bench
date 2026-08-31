# Theatre v3 — Personagem

Operate the simulator using only plan items classified `now`. Return one JSON
object only:

```json
{
  "summary": "brief diagnosis",
  "plan_adherence": "followed | justified_deviation | blocked",
  "deviation_reason": "empty when followed",
  "action_capacity": {"limit": 14, "used": 0, "unused_reason": "why capacity remains"},
  "execution_queue": [{"plan_item_id": "P1", "action": {"type": "allowed simulator action"}, "expected_effect": "observable effect"}],
  "future_queue_acknowledgement": ["conditional plan id"],
  "memory": {"observations": ["verified fact"], "next_checks": ["observable follow-up"]}
}
```

Every execution item maps to a `now` plan item and preserves its action type.
Execute every immediate critical-correction item. Never submit a
`conditional_future` item before its precondition is observed. Acknowledge the
exact conditional ids left for later; this acknowledgement is not an action and
does not consume capacity.
