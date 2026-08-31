from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class SimulationError(ValueError):
    """Raised when an action violates the benchmark contract."""


def money(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AppliedTurn:
    accepted: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    days_advanced: int
    state_hash: str


class VendingSimulator:
    """Deterministic vending-business environment.

    Every random value is derived from the scenario seed and a semantic key.
    This makes paired runs comparable even when agents choose different numbers
    of actions and makes replay independent from Python's RNG state.
    """

    def __init__(self, scenario: dict[str, Any], seed: int, state: dict[str, Any] | None = None):
        self.scenario = copy.deepcopy(scenario)
        self.seed = int(seed)
        self.products = {item["sku"]: item for item in self.scenario["products"]}
        self.suppliers = {item["id"]: item for item in self.scenario["suppliers"]}
        self.state = copy.deepcopy(state) if state is not None else self._initial_state()

    @classmethod
    def from_file(cls, scenario_path: str | Path, seed: int, state: dict[str, Any] | None = None) -> "VendingSimulator":
        with Path(scenario_path).open(encoding="utf-8") as handle:
            return cls(json.load(handle), seed=seed, state=state)

    def _initial_state(self) -> dict[str, Any]:
        prices = {sku: product["reference_price"] for sku, product in self.products.items()}
        zeros = {sku: 0 for sku in self.products}
        return {
            "scenario_id": self.scenario["id"],
            "scenario_version": self.scenario["version"],
            "seed": self.seed,
            "day": 0,
            "cash": money(self.scenario["starting_cash"]),
            "machine_cash": 0.0,
            "storage": copy.deepcopy(zeros),
            "machine_inventory": copy.deepcopy(zeros),
            "storage_value": {sku: 0.0 for sku in self.products},
            "machine_inventory_value": {sku: 0.0 for sku in self.products},
            "prices": prices,
            "discovered_suppliers": [],
            "offers": {},
            "negotiated_costs": {},
            "relationships": {supplier_id: 0.0 for supplier_id in self.suppliers},
            "pending_orders": [],
            "next_order_id": 1,
            "bankrupt_streak": 0,
            "terminated": False,
            "termination_reason": None,
            "events": [],
            "recent_events": [],
            "last_turn": {"accepted": [], "rejected": []},
            "metrics": {
                "revenue": 0.0,
                "purchases": 0.0,
                "cost_of_goods_sold": 0.0,
                "supplier_losses": 0.0,
                "operating_fees": 0.0,
                "refunds": 0.0,
                "units_sold": 0,
                "stockout_product_days": 0,
                "orders_placed": 0,
                "orders_delivered": 0,
                "orders_failed": 0,
                "negotiation_savings": 0.0,
                "invalid_actions": 0,
                "days_survived": 0
            }
        }

    def _rng(self, *parts: Any) -> random.Random:
        key = ":".join(str(part) for part in (self.seed, *parts))
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def _event(self, kind: str, severity: str, message: str, **data: Any) -> None:
        event = {"day": self.state["day"], "kind": kind, "severity": severity, "message": message, **data}
        self.state["events"].append(event)
        self.state["recent_events"].append(event)

    def _offer_key(self, supplier_id: str, sku: str) -> str:
        return f"{supplier_id}:{sku}"

    def _base_wholesale_cost(self, supplier_id: str, sku: str) -> float:
        product = self.products[sku]
        supplier = self.suppliers[supplier_id]
        category_cost_ratio = 0.43 + self._rng("cost", supplier_id, sku).uniform(-0.05, 0.05)
        return money(product["reference_price"] * category_cost_ratio * supplier["markup"])

    def _floor_cost(self, supplier_id: str, sku: str) -> float:
        supplier = self.suppliers[supplier_id]
        return money(self._base_wholesale_cost(supplier_id, sku) * supplier["floor_multiplier"])

    def public_view(self) -> dict[str, Any]:
        """Return all information an agent is allowed to observe."""
        state = self.state
        pending = [
            {
                "id": order["id"],
                "supplier": order["supplier"],
                "sku": order["sku"],
                "units": order["units"],
                "quoted_unit_cost": order["quoted_unit_cost"],
                "paid": order["paid"],
                "expected_day": order["expected_day"],
                "status": order["status"]
            }
            for order in state["pending_orders"]
            if order["status"] == "pending"
        ]
        product_rows = []
        for sku, spec in self.products.items():
            product_rows.append({
                "sku": sku,
                "name": spec["name"],
                "reference_price": spec["reference_price"],
                "current_price": state["prices"][sku],
                "storage_units": state["storage"][sku],
                "machine_units": state["machine_inventory"][sku],
                "size": spec["size"]
            })
        discovered = []
        for supplier_id in state["discovered_suppliers"]:
            supplier = self.suppliers[supplier_id]
            offers = []
            for sku in supplier["catalog"]:
                key = self._offer_key(supplier_id, sku)
                if key in state["offers"]:
                    offers.append(state["offers"][key])
            discovered.append({
                "id": supplier_id,
                "name": supplier["name"],
                "stated_lead_days": supplier["lead_days"],
                "minimum_order_units": supplier["min_order"],
                "relationship": round(state["relationships"][supplier_id], 3),
                "offers": offers
            })
        return {
            "scenario": state["scenario_id"],
            "seed": state["seed"],
            "day": state["day"],
            "days_remaining": max(0, self.scenario["days"] - state["day"]),
            "cash": state["cash"],
            "machine_cash": state["machine_cash"],
            "liquid_cash": money(state["cash"] + state["machine_cash"]),
            "bankrupt_streak": state["bankrupt_streak"],
            "terminated": state["terminated"],
            "products": product_rows,
            "supplier_directory": [{"id": s["id"], "name": s["name"]} for s in self.scenario["suppliers"]],
            "discovered_suppliers": discovered,
            "pending_orders": pending,
            "capacity": {
                "machine_used": self._inventory_size(state["machine_inventory"]),
                "machine_total": self.scenario["machine_capacity_units"],
                "storage_used": self._inventory_size(state["storage"]),
                "storage_total": self.scenario["storage_capacity_units"]
            },
            "inventory_book_value": money(sum(state["storage_value"].values()) + sum(state["machine_inventory_value"].values())),
            "recent_events": copy.deepcopy(state["recent_events"][-20:]),
            "last_turn": copy.deepcopy(state["last_turn"]),
            "metrics": copy.deepcopy(state["metrics"]),
            "allowed_actions": self.action_contract(),
            "max_actions_per_turn": int(self.scenario["max_actions_per_turn"]),
        }

    def action_contract(self) -> list[dict[str, Any]]:
        return [
            {"type": "research_supplier", "required": ["supplier"]},
            {"type": "negotiate", "required": ["supplier", "sku", "target_unit_cost", "units"]},
            {"type": "place_order", "required": ["supplier", "sku", "units"]},
            {"type": "set_price", "required": ["sku", "price"]},
            {"type": "restock", "required": ["sku", "units"]},
            {"type": "collect_cash", "required": []}
        ]

    def apply_turn(self, actions: Iterable[dict[str, Any]], advance_days: int | None = None) -> AppliedTurn:
        if self.state["terminated"]:
            raise SimulationError("run is already terminated")
        actions = list(actions)
        limit = int(self.scenario["max_actions_per_turn"])
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            if index >= limit:
                rejected.append({"action": action, "reason": f"action limit {limit} exceeded"})
                continue
            try:
                result = self._apply_action(action)
                accepted.append({"action": action, "result": result})
            except (SimulationError, KeyError, TypeError, ValueError) as exc:
                rejected.append({"action": action, "reason": str(exc)})
        self.state["metrics"]["invalid_actions"] += len(rejected)
        self.state["last_turn"] = {"accepted": accepted, "rejected": rejected}
        self.state["recent_events"] = []
        days = int(advance_days or self.scenario["decision_period_days"])
        days = max(0, min(days, self.scenario["days"] - self.state["day"]))
        advanced = 0
        for _ in range(days):
            if self.state["terminated"]:
                break
            self._advance_one_day()
            advanced += 1
        return AppliedTurn(accepted, rejected, advanced, stable_hash(self.state))

    def _apply_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action, dict) or "type" not in action:
            raise SimulationError("action must be an object with a type")
        handlers = {
            "research_supplier": self._research_supplier,
            "negotiate": self._negotiate,
            "place_order": self._place_order,
            "set_price": self._set_price,
            "restock": self._restock,
            "collect_cash": self._collect_cash
        }
        handler = handlers.get(action["type"])
        if handler is None:
            raise SimulationError(f"unknown action type: {action['type']}")
        return handler(action)

    def _research_supplier(self, action: dict[str, Any]) -> dict[str, Any]:
        supplier_id = str(action["supplier"])
        if supplier_id not in self.suppliers:
            raise SimulationError(f"unknown supplier: {supplier_id}")
        supplier = self.suppliers[supplier_id]
        if supplier_id not in self.state["discovered_suppliers"]:
            self.state["discovered_suppliers"].append(supplier_id)
        offers = []
        for sku in supplier["catalog"]:
            key = self._offer_key(supplier_id, sku)
            offer = {
                "supplier": supplier_id,
                "sku": sku,
                "unit_cost": self._base_wholesale_cost(supplier_id, sku),
                "minimum_order_units": supplier["min_order"]
            }
            self.state["offers"][key] = offer
            offers.append(offer)
        return {"offers": offers}

    def _negotiate(self, action: dict[str, Any]) -> dict[str, Any]:
        supplier_id = str(action["supplier"])
        sku = str(action["sku"])
        units = int(action["units"])
        target = money(float(action["target_unit_cost"]))
        key = self._offer_key(supplier_id, sku)
        if key not in self.state["offers"]:
            raise SimulationError("supplier offer must be researched before negotiation")
        supplier = self.suppliers[supplier_id]
        if sku not in supplier["catalog"]:
            raise SimulationError("supplier does not carry this product")
        current = self.state["negotiated_costs"].get(key, self.state["offers"][key]["unit_cost"])
        floor = self._floor_cost(supplier_id, sku)
        relationship = self.state["relationships"][supplier_id]
        volume_bonus = min(0.12, max(0, units - supplier["min_order"]) / 500)
        acceptable = floor * (1.12 - volume_bonus - min(0.08, relationship * 0.04))
        noise = self._rng("negotiate", self.state["day"], supplier_id, sku, units, round(target, 2)).uniform(-0.025, 0.025)
        threshold = money(acceptable * (1 + noise))
        if target >= threshold:
            agreed = money(max(floor, min(current, target)))
            self.state["negotiated_costs"][key] = agreed
            savings = money(max(0, current - agreed) * max(units, supplier["min_order"]))
            self.state["metrics"]["negotiation_savings"] = money(self.state["metrics"]["negotiation_savings"] + savings)
            self.state["relationships"][supplier_id] = min(1.0, relationship + 0.08)
            return {"accepted": True, "unit_cost": agreed, "estimated_savings": savings}
        counter = money(max(floor, (current + threshold) / 2))
        self.state["relationships"][supplier_id] = max(-1.0, relationship - 0.03)
        return {"accepted": False, "counter_offer": counter}

    def _place_order(self, action: dict[str, Any]) -> dict[str, Any]:
        supplier_id = str(action["supplier"])
        sku = str(action["sku"])
        units = int(action["units"])
        key = self._offer_key(supplier_id, sku)
        if key not in self.state["offers"]:
            raise SimulationError("supplier offer must be researched before ordering")
        supplier = self.suppliers[supplier_id]
        if units < supplier["min_order"]:
            raise SimulationError(f"minimum order is {supplier['min_order']} units")
        if units <= 0:
            raise SimulationError("units must be positive")
        projected_size = self._inventory_size(self.state["storage"]) + sum(
            order["units"] * self.products[order["sku"]]["size"]
            for order in self.state["pending_orders"] if order["status"] == "pending"
        ) + units * self.products[sku]["size"]
        if projected_size > self.scenario["storage_capacity_units"]:
            raise SimulationError("order would exceed storage capacity")
        unit_cost = money(self.state["negotiated_costs"].get(key, self.state["offers"][key]["unit_cost"]))
        total = money(unit_cost * units)
        if total > self.state["cash"]:
            raise SimulationError(f"insufficient cash: need {total}, have {self.state['cash']}")
        order_id = self.state["next_order_id"]
        self.state["next_order_id"] += 1
        rng = self._rng("order", order_id, supplier_id, sku, units)
        delay = 0 if rng.random() < supplier["reliability"] else rng.randint(2, 9)
        failure = rng.random() > supplier["reliability"] + 0.08
        bait_switch = rng.random() < supplier["adversarial"]
        expected_day = self.state["day"] + supplier["lead_days"] + delay
        order = {
            "id": order_id,
            "supplier": supplier_id,
            "sku": sku,
            "units": units,
            "quoted_unit_cost": unit_cost,
            "paid": total,
            "placed_day": self.state["day"],
            "expected_day": expected_day,
            "status": "pending",
            "will_fail": failure,
            "bait_switch": bait_switch,
            "bait_multiplier": round(rng.uniform(1.18, 1.65), 3) if bait_switch else 1.0
        }
        self.state["cash"] = money(self.state["cash"] - total)
        self.state["pending_orders"].append(order)
        self.state["metrics"]["orders_placed"] += 1
        return {"order_id": order_id, "paid": total, "expected_day": expected_day}

    def _set_price(self, action: dict[str, Any]) -> dict[str, Any]:
        sku = str(action["sku"])
        if sku not in self.products:
            raise SimulationError(f"unknown product: {sku}")
        price = money(float(action["price"]))
        if price < 0.25 or price > 100:
            raise SimulationError("price must be between 0.25 and 100")
        self.state["prices"][sku] = price
        return {"sku": sku, "price": price}

    def _restock(self, action: dict[str, Any]) -> dict[str, Any]:
        sku = str(action["sku"])
        units = int(action["units"])
        if sku not in self.products:
            raise SimulationError(f"unknown product: {sku}")
        if units <= 0:
            raise SimulationError("units must be positive")
        units = min(units, self.state["storage"][sku])
        free_size = self.scenario["machine_capacity_units"] - self._inventory_size(self.state["machine_inventory"])
        max_by_space = free_size // self.products[sku]["size"]
        moved = min(units, max_by_space)
        if moved <= 0:
            raise SimulationError("no inventory or machine capacity available")
        storage_units_before = self.state["storage"][sku]
        average_unit_cost = self.state["storage_value"][sku] / storage_units_before if storage_units_before else 0.0
        moved_value = money(average_unit_cost * moved)
        self.state["storage"][sku] -= moved
        self.state["storage_value"][sku] = money(max(0.0, self.state["storage_value"][sku] - moved_value))
        self.state["machine_inventory"][sku] += moved
        self.state["machine_inventory_value"][sku] = money(self.state["machine_inventory_value"][sku] + moved_value)
        return {"sku": sku, "moved": moved, "book_value_moved": moved_value}

    def _collect_cash(self, action: dict[str, Any]) -> dict[str, Any]:
        collected = money(self.state["machine_cash"])
        self.state["cash"] = money(self.state["cash"] + collected)
        self.state["machine_cash"] = 0.0
        return {"collected": collected}

    def _advance_one_day(self) -> None:
        self.state["day"] += 1
        day = self.state["day"]
        self._deliver_orders(day)
        self._apply_external_event(day)
        self._simulate_sales(day)
        fee = money(self.scenario["daily_machine_fee"])
        if self.state["cash"] >= fee:
            self.state["cash"] = money(self.state["cash"] - fee)
            self.state["metrics"]["operating_fees"] = money(self.state["metrics"]["operating_fees"] + fee)
            self.state["bankrupt_streak"] = 0
        else:
            self.state["bankrupt_streak"] += 1
            self._event("fee_missed", "critical", "Daily machine fee could not be paid", amount=fee)
        self.state["metrics"]["days_survived"] = day
        if self.state["bankrupt_streak"] >= self.scenario["bankruptcy_grace_days"]:
            self.state["terminated"] = True
            self.state["termination_reason"] = "bankrupt"
            self._event("bankruptcy", "critical", "Run terminated after consecutive unpaid fees")
        elif day >= self.scenario["days"]:
            self.state["terminated"] = True
            self.state["termination_reason"] = "completed"

    def _deliver_orders(self, day: int) -> None:
        for order in self.state["pending_orders"]:
            if order["status"] != "pending" or order["expected_day"] > day:
                continue
            supplier_id = order["supplier"]
            if order["will_fail"]:
                order["status"] = "failed"
                refund_ratio = self._rng("refund", order["id"]).uniform(0.55, 0.9)
                refunded = money(order["paid"] * refund_ratio)
                self.state["cash"] = money(self.state["cash"] + refunded)
                self.state["metrics"]["supplier_losses"] = money(self.state["metrics"]["supplier_losses"] + order["paid"] - refunded)
                self.state["metrics"]["orders_failed"] += 1
                self.state["relationships"][supplier_id] = max(-1.0, self.state["relationships"][supplier_id] - 0.3)
                self._event("supplier_failure", "critical", "Supplier failed to deliver and issued only a partial refund", order_id=order["id"], refunded=refunded)
                continue
            if order["bait_switch"]:
                surcharge = money(order["paid"] * (order["bait_multiplier"] - 1))
                if surcharge <= self.state["cash"]:
                    self.state["cash"] = money(self.state["cash"] - surcharge)
                    order["paid"] = money(order["paid"] + surcharge)
                    self._event("bait_switch", "critical", "Supplier charged an unexpected delivery surcharge", order_id=order["id"], surcharge=surcharge)
                else:
                    order["status"] = "failed"
                    refunded = money(order["paid"] * 0.7)
                    self.state["cash"] = money(self.state["cash"] + refunded)
                    self.state["metrics"]["supplier_losses"] = money(self.state["metrics"]["supplier_losses"] + order["paid"] - refunded)
                    self.state["metrics"]["orders_failed"] += 1
                    self._event("bait_switch_refused", "critical", "Delivery was refused after an unaffordable bait-and-switch surcharge", order_id=order["id"], refunded=refunded)
                    continue
            order["status"] = "delivered"
            self.state["storage"][order["sku"]] += order["units"]
            self.state["storage_value"][order["sku"]] = money(self.state["storage_value"][order["sku"]] + order["paid"])
            self.state["metrics"]["purchases"] = money(self.state["metrics"]["purchases"] + order["paid"])
            self.state["metrics"]["orders_delivered"] += 1
            self.state["relationships"][supplier_id] = min(1.0, self.state["relationships"][supplier_id] + 0.04)
            self._event("delivery", "info", "Order delivered to storage", order_id=order["id"], sku=order["sku"], units=order["units"])

    def _apply_external_event(self, day: int) -> None:
        rng = self._rng("external", day)
        if rng.random() < 0.018:
            severity = "critical" if rng.random() < 0.35 else "warning"
            amount = money(rng.uniform(3, 22))
            paid = min(amount, self.state["cash"])
            self.state["cash"] = money(self.state["cash"] - paid)
            self.state["metrics"]["refunds"] = money(self.state["metrics"]["refunds"] + paid)
            self._event("customer_refund", severity, "Customer complaint required a refund", amount=paid)
        if day in (91, 183, 274):
            self._event("season_review", "warning", "Quarter boundary: demand mix may change; review prices and supply resilience")

    def _simulate_sales(self, day: int) -> None:
        active_skus = sum(1 for units in self.state["machine_inventory"].values() if units > 0)
        choice_factor = max(0.5, 1.0 - abs(active_skus - 5) * 0.055)
        weekday = (day - 1) % 7
        weekday_factor = [0.9, 0.96, 1.02, 1.08, 1.24, 1.18, 0.78][weekday]
        month = min(11, (day - 1) // 30)
        month_factor = [0.88, 0.9, 0.98, 1.02, 1.1, 1.2, 1.23, 1.16, 1.05, 1.0, 1.07, 1.18][month]
        weather_value = self._rng("weather", day).random()
        weather = "hot" if weather_value > 0.68 else "cold" if weather_value < 0.25 else "mild"
        for sku, product in self.products.items():
            available = self.state["machine_inventory"][sku]
            if available <= 0:
                self.state["metrics"]["stockout_product_days"] += 1
                continue
            price = self.state["prices"][sku]
            reference = product["reference_price"]
            price_ratio = max(0.05, price / reference)
            price_factor = math.exp(-product["elasticity"] * (price_ratio - 1.0))
            weather_factor = 1.0
            if product["weather"] == "hot":
                weather_factor = 1.28 if weather == "hot" else 0.82 if weather == "cold" else 1.0
            elif product["weather"] == "cold":
                weather_factor = 1.22 if weather == "cold" else 0.86 if weather == "hot" else 1.0
            expected = product["base_daily_demand"] * weekday_factor * month_factor * weather_factor * choice_factor * price_factor
            noise = self._rng("sales", day, sku).uniform(0.72, 1.28)
            sold = min(available, max(0, int(expected * noise + self._rng("round", day, sku).random())))
            if sold == available and expected > available:
                self.state["metrics"]["stockout_product_days"] += 1
            if sold:
                revenue = money(sold * price)
                units_before = self.state["machine_inventory"][sku]
                average_unit_cost = self.state["machine_inventory_value"][sku] / units_before if units_before else 0.0
                sold_cost = money(average_unit_cost * sold)
                self.state["machine_inventory"][sku] -= sold
                self.state["machine_inventory_value"][sku] = money(max(0.0, self.state["machine_inventory_value"][sku] - sold_cost))
                self.state["machine_cash"] = money(self.state["machine_cash"] + revenue)
                self.state["metrics"]["revenue"] = money(self.state["metrics"]["revenue"] + revenue)
                self.state["metrics"]["cost_of_goods_sold"] = money(self.state["metrics"]["cost_of_goods_sold"] + sold_cost)
                self.state["metrics"]["units_sold"] += sold

    def _inventory_size(self, inventory: dict[str, int]) -> int:
        return sum(int(units) * int(self.products[sku]["size"]) for sku, units in inventory.items())

    def score(self, output_tokens: int = 0) -> dict[str, Any]:
        state = self.state
        liquid_cash = money(state["cash"] + state["machine_cash"])
        virtual_compute_cost = money(output_tokens / 1_000_000 * self.scenario["virtual_output_cost_per_million_tokens"])
        adjusted = money(liquid_cash - virtual_compute_cost)
        gross_profit = money(state["metrics"]["revenue"] - state["metrics"]["cost_of_goods_sold"])
        return {
            "primary_score": adjusted,
            "liquid_cash": liquid_cash,
            "virtual_compute_cost": virtual_compute_cost,
            "output_tokens": int(output_tokens),
            "revenue": state["metrics"]["revenue"],
            "gross_profit": gross_profit,
            "gross_margin_pct": round((gross_profit / state["metrics"]["revenue"] * 100), 2) if state["metrics"]["revenue"] else 0.0,
            "days_survived": state["metrics"]["days_survived"],
            "termination_reason": state["termination_reason"],
            "units_sold": state["metrics"]["units_sold"],
            "stockout_product_days": state["metrics"]["stockout_product_days"],
            "refunds": state["metrics"]["refunds"],
            "purchases": state["metrics"]["purchases"],
            "cost_of_goods_sold": state["metrics"]["cost_of_goods_sold"],
            "supplier_losses": state["metrics"]["supplier_losses"],
            "ending_inventory_book_value": money(sum(state["storage_value"].values()) + sum(state["machine_inventory_value"].values())),
            "invalid_actions": state["metrics"]["invalid_actions"]
        }
