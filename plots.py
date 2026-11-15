"""Make the lab plots from the CSVs written by experiments.py.

Points are the mean over seeds; error bars are +/- one standard deviation.
Dashed lines are analytic results (not simulation data).
Usage: .venv/bin/python plots.py   -> PNGs in results/plots/
"""
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = Path("results")
OUT = RES / "plots"
TTX, RTT, TOUT = 1.0, 10.0, 15.0
MARKERS = "osD^vP*X"


def load(name):
    with (RES / f"{name}.csv").open() as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("ws", "wr"):
            r[k] = int(r[k])
        for k in ("pd", "pa", "timeout", "efficiency", "attempts"):
            r[k] = float(r[k])
        r["completion_ms"] = float(r["completion_ms"]) if r["completion_ms"] else None
        r["n"] = int(r["n"]) if r["n"] else None
    return rows


def curves(rows, x, y, label):
    """Group rows into {label: [(x, mean y, std y), ...]} sorted by x."""
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[label(r)][r[x]].append(r[y])
    return {lab: [(xv, mean(v), pstdev(v)) for xv, v in sorted(pts.items())]
            for lab, pts in groups.items()}


def plot(rows, x, y, label, xlabel, ylabel, title, fname, theory=None, logx=False,
         sw_reference=False):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    data = curves(rows, x, y, label)
    for i, (lab, pts) in enumerate(sorted(data.items())):
        if sw_reference and lab == "S&W":
            ax.axhline(pts[0][1], color="gray", ls=":", label="S&W (reference)")
            continue
        xs, ms, sds = zip(*pts)
        ax.errorbar(xs, ms, yerr=sds, fmt=MARKERS[i % len(MARKERS)], ms=5, capsize=2, label=lab)
    for lab, (tx, ty) in (theory or {}).items():
        ax.plot(tx, ty, "--", lw=1, label=lab)
    if logx:
        ax.set_xscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=150)
    plt.close(fig)
    print(f"wrote {OUT / fname}")


def window_label(r):
    return "S&W" if r["protocol"] == "S&W" else r["protocol"]


def fixed_label(r):
    if r["protocol"] == "S&W":
        return "S&W"
    if r["wr"] == 1:
        return f"GBN Ws={r['ws']}"
    return f"SR Ws={r['ws']} Wr={r['wr']}"


def grid(lo, hi, n=200):
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # Exp 2
    rows = load("exp2")
    ws = grid(1, 64)
    plot(rows, "ws", "efficiency", window_label, "Send window size Ws", "Link efficiency",
         "Exp 2: error-free, efficiency vs Ws (pd = pa = 0)", "exp2_eff_vs_ws.png",
         theory={"analysis min(1, Ws/11)": (ws, [min(1, w * TTX / (TTX + RTT)) for w in ws])},
         sw_reference=True)

    # Exp 3a
    rows = load("exp3a")
    p = grid(0, 0.5)
    K = TOUT / TTX
    plot(rows, "pd", "efficiency", fixed_label, "Data packet error probability pd",
         "Link efficiency", "Exp 3a: efficiency vs pd (pa = 0, Tout = 15 ms)",
         "exp3a_eff_vs_pd.png",
         theory={"S&W analysis": (p, [TTX / (TTX + RTT + q / (1 - q) * TOUT) for q in p]),
                 "GBN analysis (1-p)/(1+(K-1)p), K=15": (p, [(1 - q) / (1 + (K - 1) * q) for q in p]),
                 "SR analysis 1-p": (p, [1 - q for q in p])})

    # Exp 3b
    rows = load("exp3b")
    plot(rows, "ws", "efficiency", window_label, "Send window size Ws", "Link efficiency",
         "Exp 3b: efficiency vs Ws (pd = 0.1, pa = 0)", "exp3b_eff_vs_ws.png", sw_reference=True)
    plot(rows, "ws", "attempts", window_label, "Send window size Ws",
         "Transmissions per delivered packet",
         "Exp 3b: attempts per delivered packet vs Ws (pd = 0.1, pa = 0)",
         "exp3b_attempts_vs_ws.png", sw_reference=True)

    # Exp 4
    rows = load("exp4")
    plot(rows, "pa", "efficiency", fixed_label, "ACK error probability pa", "Link efficiency",
         "Exp 4: efficiency vs pa (pd = 0.1)", "exp4_eff_vs_pa.png")
    plot(rows, "pa", "attempts", fixed_label, "ACK error probability pa",
         "Transmissions per delivered packet",
         "Exp 4: attempts per delivered packet vs pa (pd = 0.1)", "exp4_attempts_vs_pa.png")

    # Exp 5
    rows = load("exp5")
    plot(rows, "timeout", "efficiency", fixed_label, "Retransmission timeout Tout (ms)",
         "Link efficiency", "Exp 5: efficiency vs Tout (pd = pa = 0.1, RTT + Ttx = 11 ms)",
         "exp5_eff_vs_tout.png", logx=True)
    plot(rows, "timeout", "attempts", fixed_label, "Retransmission timeout Tout (ms)",
         "Transmissions per delivered packet",
         "Exp 5: attempts per delivered packet vs Tout (pd = pa = 0.1)",
         "exp5_attempts_vs_tout.png", logx=True)

    # Exp 6 and 7: completion time vs Ws, one figure per (N, pd, pa)
    rows = load("exp6") + load("exp7")
    cases = sorted({(r["n"], r["pd"], r["pa"]) for r in rows})
    for n, pd, pa in cases:
        sub = [r for r in rows if (r["n"], r["pd"], r["pa"]) == (n, pd, pa)]
        exp = "6" if n == 100 else "7"
        plot(sub, "ws", "completion_ms", window_label, "Send window size Ws",
             "Mean transfer completion time (ms)",
             f"Exp {exp}: completion time vs Ws (N = {n}, pd = {pd}, pa = {pa})",
             f"exp{exp}_completion_N{n}_pd{pd}_pa{pa}.png", sw_reference=True)


if __name__ == "__main__":
    main()
