from __future__ import annotations

from typing import Any


def heuristic_actions(view: dict[str, Any], arm: str = "control") -> list[dict[str, Any]]:
    """Deterministic non-LLM policy used only for simulator validation."""
    actions: list[dict[str, Any]] = [{"type": "collect_cash"}]
    discovered = {supplier["id"]: supplier for supplier in view["discovered_suppliers"]}
    for supplier in view["supplier_directory"]:
        if supplier["id"] not in discovered:
            actions.append({"type": "research_supplier", "supplier": supplier["id"]})
            if len(actions) >= 4:
                return actions

    products = {product["sku"]: product for product in view["products"]}
    priority = ["water", "cola", "chips", "energy", "protein", "candy", "trailmix", "charger"]
    target_price_ratio = 1.08 if arm == "control" else 1.12
    for sku in priority:
        product = products[sku]
        target_price = round(product["reference_price"] * target_price_ratio, 2)
        if abs(product["current_price"] - target_price) >= 0.04:
            actions.append({"type": "set_price", "sku": sku, "price": target_price})

    machine_free = view["capacity"]["machine_total"] - view["capacity"]["machine_used"]
    for sku in priority:
        product = products[sku]
        if product["storage_units"] > 0 and machine_free >= product["size"]:
            desired = 12 if product["size"] == 1 else 5
            units = min(product["storage_units"], max(0, desired - product["machine_units"]), machine_free // product["size"])
            if units > 0:
                actions.append({"type": "restock", "sku": sku, "units": units})
                machine_free -= units * product["size"]

    pending_by_sku: dict[str, int] = {}
    for order in view["pending_orders"]:
        pending_by_sku[order["sku"]] = pending_by_sku.get(order["sku"], 0) + order["units"]
    pending_size = sum(pending_by_sku[sku] * products[sku]["size"] for sku in pending_by_sku)
    storage_free = view["capacity"]["storage_total"] - view["capacity"]["storage_used"] - pending_size
    all_offers = [offer for supplier in view["discovered_suppliers"] for offer in supplier["offers"]]
    for sku in priority:
        product = products[sku]
        on_hand = product["storage_units"] + product["machine_units"] + pending_by_sku.get(sku, 0)
        target = 42 if sku in ("water", "cola", "chips") else 24 if product["size"] == 1 else 10
        if on_hand >= target:
            continue
        offers = [offer for offer in all_offers if offer["sku"] == sku]
        if not offers:
            continue
        offer = min(offers, key=lambda item: item["unit_cost"])
        units = max(offer["minimum_order_units"], target - on_hand)
        estimated = units * offer["unit_cost"]
        if units * product["size"] > storage_free:
            continue
        reserve = 80 if arm == "control" else 110
        if estimated <= max(0, view["cash"] - reserve):
            if arm == "theatre" and view["day"] % 28 == 0:
                actions.append({
                    "type": "negotiate",
                    "supplier": offer["supplier"],
                    "sku": sku,
                    "target_unit_cost": round(offer["unit_cost"] * 0.88, 2),
                    "units": units
                })
            actions.append({"type": "place_order", "supplier": offer["supplier"], "sku": sku, "units": units})
            storage_free -= units * product["size"]
        if len(actions) >= 14:
            break
    return actions[:14]
