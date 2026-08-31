# Frozen vending-operations corpus v2

This corpus is deliberately small, static, and arm-neutral. It is available to
both control and Theatre. It contains no pilot score, no arm comparison, no
private trajectory, and no live source. The simulator remains the sole factual
authority for the current business.

## Operating principles

- Protect solvency, but distinguish prudent reserves from chronic
  under-investment that destroys future sales.
- Availability is an economic variable. Track stockout exposure, inventory in
  storage, inventory in the machine, pending orders, lead time, and demand rate
  together.
- Reorder before depletion. A useful starting point is expected demand over
  lead time plus an explicit safety allowance, constrained by cash and physical
  capacity.
- Treat delivered stock sitting in storage as unavailable until it is loaded
  into the machine.
- Evaluate assortment by contribution, demand, size, stockout frequency, and
  capital employed. Variety can affect total demand, so do not optimize one SKU
  in isolation.
- Price tests should be reversible, bounded, and evaluated against units,
  revenue, gross profit, refunds, and availability. Waiting for perfect
  conditions can itself be costly.
- Supplier decisions should balance unit cost, minimum order, reliability,
  lead time, relationship, failure loss, and concentration risk.
- Negotiation consumes an action and has opportunity cost. Prefer it when the
  expected saving or information value exceeds the operating work displaced.
- Forecasts are hypotheses. Compare predicted cash, demand, stockouts, and
  inventory with realized values at every strategic review.
- When a correction is declared critical, translate it into an executable,
  auditable queue and verify the result at the next review.
- Use all available action capacity only when the actions are economically
  justified; unused capacity must be explicit rather than accidentally assumed.

## Evidence discipline

- Never infer supplier catalog, current demand, cash, inventory, or events from
  this corpus. Read them from the current simulator view.
- Never use another arm's state, decisions, score, or result.
- Never call tools, browse, communicate, or edit files from a benchmark role.
- Record uncertainty and prefer reversible tests when evidence is incomplete.
