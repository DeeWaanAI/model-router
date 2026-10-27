#!/usr/bin/env python3
"""make_figures.py — two vector figures from frozen artifacts, nothing recomputed by hand.

fig1_cost_accuracy.pdf : every strategy on the six computation items (baselines.json panel B).
                          Free points sit on the left edge because $0 cannot be plotted on a log
                          axis; they are labelled as free rather than nudged to a fake price.
fig2_sweep.pdf         : the 24-point eligibility sweep — what the policy costs and what it buys.

Run after experiments/baselines.py and experiments/exp3/sweep_eligibility.py.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
L5 = HERE.parent
OUT = HERE / "figures"
OUT.mkdir(exist_ok=True)


def fig_cost_accuracy():
    d = json.loads((L5 / "experiments/results/derived/baselines.json").read_text())
    rows = {r["strategy"]: r for r in d["panels"]["B_compute_6_items"]}
    # offsets are explicit: three points sit at the same y and would overprint otherwise.
    keep = [("always local 3B", "always-local-3b", "o", (8, -3), "left"),
            ("always local 8B", "always-local-8b", "o", (8, -3), "left"),
            ("cheapest cloud", "cheapest-cloud", "s", (8, -3), "left"),
            ("always 70B", "always-70b", "s", (8, -3), "left"),
            ("flagship = capability-only", "always-flagship", "D", (8, 0), "left"),
            ("router (v9)", "router, default policy (v9)", "*", (0, -16), "center")]
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    for label, key, marker, off, ha in keep:
        r = rows.get(key)
        if not r:
            continue
        cost = max(r["cost_usd"], 1e-9)
        free = r["cost_usd"] == 0
        x = 1e-7 if free else cost
        ax.scatter([x], [r["accuracy"]], marker=marker, s=90 if marker == "*" else 45,
                   color="#1f4e79" if free else "#8a4b2a" if key.startswith("router") else "#555",
                   zorder=3)
        ax.annotate(label + ("  (free)" if free else f"  ${cost:.5f}"),
                    (x, r["accuracy"]), textcoords="offset points", xytext=off,
                    ha=ha, fontsize=7)
    ax.set_xscale("symlog", linthresh=1e-6)
    ax.set_xlabel("cost for the six items, USD (symlog; free = left edge)")
    ax.set_ylabel("measured accuracy")
    ax.set_ylim(-0.14, 1.14)
    ax.set_xlim(right=0.09)
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.set_title("Cost and accuracy on six computation items", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_cost_accuracy.pdf")
    fig.savefig(OUT / "fig1_cost_accuracy.png", dpi=140)
    print("fig1 ->", (OUT / "fig1_cost_accuracy.pdf").relative_to(L5))


def fig_sweep():
    """24 policy points collapse onto four distinct outcomes.

    That collapse is the finding, so the figure plots the four outcomes and labels each with
    the threshold combinations that produce it, rather than drawing 24 overlapping dots.
    """
    d = json.loads((L5 / "experiments/exp3/results/derived/sweep_exp3-sweep-v3.json").read_text())
    pts = [p for p in d["points"] if p["mode"] == "allow_flagged"]
    groups = {}
    for p in pts:
        key = (p["est_cost_usd"], p["mean_measured_accuracy"],
               tuple(sorted(p["chosen_counts"].items())))
        groups.setdefault(key, []).append((p["min_accuracy"], p["min_schema"]))
    fig, ax = plt.subplots(figsize=(6.0, 3.5))
    order = sorted(groups, key=lambda k: (k[1], k[0]))
    for i, (cost, acc, model) in enumerate(order):
        name = model[0][0].replace("llama3.2:latest", "local 3B") \
                          .replace("llama3.1:8b-instruct-q4_K_M", "local 8B") \
                          .replace("Llama-3.3-70B-Instruct-Turbo", "70B") \
                          .replace("Qwen3.8-2.4T-A95B", "flagship")
        n = len(groups[(cost, acc, model)])
        x = 1e-7 if cost == 0 else cost
        ax.scatter([x], [acc], s=90, zorder=3,
                   color="#8a4b2a" if cost else "#1f4e79")
        ax.annotate(f"{name}  ({n} of 12 policies)", (x, acc), textcoords="offset points",
                    xytext=(10, 8), ha="left", fontsize=7.5)
    ax.set_xscale("symlog", linthresh=1e-6)
    ax.set_xlim(-2e-8, 0.02)
    ax.set_ylim(-0.08, 1.20)
    ax.set_xlabel("estimated cost for six items, USD (symlog; free = left edge)")
    ax.set_ylabel("mean measured accuracy")
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.set_title("24 policy points, 4 distinct outcomes", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig2_sweep.pdf")
    fig.savefig(OUT / "fig2_sweep.png", dpi=140)
    print("fig2 ->", (OUT / "fig2_sweep.pdf").relative_to(L5))


if __name__ == "__main__":
    fig_cost_accuracy()
    fig_sweep()
    sys.exit(0)
