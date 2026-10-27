#!/usr/bin/env python3
"""cost.py — declared cost only (spec §14, RC-3).

The router does not manufacture a valuation on the user's behalf. A connection costs what
the user declared; local connections are declared at $0 and therefore win whenever they are
feasible. The old `local_shadow_price_per_mtok` and `latency_price_usd_per_s` knobs are
gone: they existed to stop the frontier collapsing to a point, which was an experiment
concern leaking into the product's decision rule. The frontier stays in experiment 1, where
local is priced at $0 and any crossover is a sensitivity curve in the paper (RC-3.4).

`price.source: "unknown"` means the connection cannot be ranked by cost. It is not given a
fake price: it ranks after every priced connection and the decision record says so (RC-1.4).
"""

UNIT = "usd_per_million_tokens"


class CostError(Exception):
    pass


def _price(conn, key):
    p = conn.get("price") or {}
    if p.get("source") == "unknown":
        return None
    v = p.get(key)
    if v is None:
        return None
    if v > 1000.0:
        raise CostError(f"{conn['id']}: price.{key}={v} exceeds $1000/Mtok — suspected 1e6 "
                        f"unit error (spec §3.1)")
    return float(v)


def expected_cost_usd(conn, est_in, expected_out):
    """USD per million tokens, divided by 1e6 exactly once. None = not rankable."""
    pin, pout = _price(conn, "input_per_mtok"), _price(conn, "output_per_mtok")
    if pin is None or pout is None:
        return None
    return (est_in * pin + expected_out * pout) / 1e6


def actual_cost_usd(conn, tokens_in, tokens_out):
    """Post-hoc, from the provider's own meter. None when the endpoint reported no usage:
    a missing meter is reported as missing, never filled in with an estimate (RC-3.5)."""
    if tokens_in is None or tokens_out is None:
        return None
    pin, pout = _price(conn, "input_per_mtok"), _price(conn, "output_per_mtok")
    if pin is None or pout is None:
        return None
    return (tokens_in * pin + tokens_out * pout) / 1e6


def rank(feasible, tie_break):
    """Cheapest declared cost first; unpriced connections last; then the tie-break.

    `id_asc` is the original rule and stays the meaning of every policy written before
    2026-10-25. `measured_then_id_asc` adds one key before the id: at equal declared cost, a
    connection we have measured evidence for wins over one we do not. The defect that forced
    it is real — two arms priced identically ($2/$6 chat and reasoning) tied, the unmeasured
    one had the lower id, and the router selected an arm it knew nothing about. Registration
    order should not decide that.
    """
    if tie_break not in ("id_asc", "measured_then_id_asc"):
        raise CostError(f"unknown tie_break {tie_break!r}")
    if tie_break == "id_asc":
        return sorted(feasible, key=lambda f: (f["expected_cost_usd"] is None,
                                               f["expected_cost_usd"] or 0.0,
                                               f["model_ref"]))
    return sorted(feasible, key=lambda f: (f["expected_cost_usd"] is None,
                                           f["expected_cost_usd"] or 0.0,
                                           not f.get("measured_known", False),
                                           f["model_ref"]))
