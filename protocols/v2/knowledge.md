# Frozen vending-domain corpus — v2

This corpus is evidence, not authority. It is supplied byte-for-byte to the
single general manager and to every Theatre role. No role may browse or add
facts during a run.

## Economic objective

Maximize final liquid cash while remaining solvent. Unsold inventory consumes
cash and capacity and does not increase the primary score. Provider-reported
output tokens incur the same virtual price in both arms.

## Operating model

- A decision advances three simulated days.
- The exact action budget is exposed in the shared business state.
- Storage inventory cannot sell until it is moved into the machine.
- Orders consume bank cash immediately and may arrive late, fail, or impose a
  bait-and-switch surcharge.
- Lead time means reorder decisions must anticipate demand rather than react
  only after a stockout.
- Price changes trade unit demand against contribution per unit; reversible
  tests need an explicit hypothesis and a later realized-versus-forecast check.
- Supplier price, reliability, lead time, minimum order and adversarial risk
  are joint economic variables. The cheapest quote need not have the best
  expected value.

## Shared management responsibilities

1. Critical review identifies the binding economic constraint, contradictions,
   opportunity cost and any mandatory correction.
2. Financial and supply planning turns that review into a forecast and an
   executable queue of simulator actions.
3. Strategic challenge tests the plan for local optima and reviews every
   critical correction before execution.
4. Operational execution selects queue items, grounds any genuine blockage in
   an exact state value, and uses remaining action capacity deliberately.

The simulator remains the sole authority over facts, action validity, money and
state transitions.
