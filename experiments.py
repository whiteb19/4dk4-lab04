"""Run Experiments 2-7 and save one CSV per experiment in results/.

Each row is one simulation run (one seed). plots.py averages over seeds.
Usage: .venv/bin/python experiments.py [exp2 exp3a ...]
"""
import csv
import sys
from multiprocessing import Pool
from pathlib import Path

import sim

SEEDS = (400454641, 12345, 67890, 2024, 4404)   # first seed = student ID
FINITE_SEEDS = SEEDS + tuple(range(1, 16))       # finite runs are short, use 20 seeds
MEASURE_MS = 20000                               # continuous measurement interval
WARMUP_MS = 1000
OUT = Path("results")

BIG_WS = 32              # "large enough" window (link full needs >= 11, >= 16 with errors)
SR_WR = (4, 16, 32)      # three receive windows for SR
WS_SWEEP = (1, 2, 4, 6, 8, 10, 11, 12, 14, 16, 20, 24, 32, 40, 48, 64)


def seq_space(ws, wr):
    need = ws + 1 if wr == 1 else ws + max(ws, wr)
    return 1 << (need - 1).bit_length()


def protocol_name(ws, wr):
    if ws == 1 and wr == 1:
        return "S&W"
    return "GBN" if wr == 1 else f"SR Wr={wr}"


def run_one(job):
    exp, ws, wr, pd, pa, tout, n, seed = job
    common = dict(seed=seed, ws=ws, wr=wr, sequence_space=seq_space(ws, wr),
                  pd=pd, pa=pa, timeout_ms=tout)
    if n is None:
        sim.run_sim(mode="continuous", measurement_time_ms=MEASURE_MS,
                    warmup_time_ms=WARMUP_MS, **common)
    else:
        sim.run_sim(mode="finite", packet_count=n, **common)
    r = sim.last_result
    return dict(exp=exp, protocol=protocol_name(ws, wr), ws=ws, wr=wr, pd=pd, pa=pa,
                timeout=tout, n=n or "", seed=seed,
                efficiency=r["link_efficiency"],
                attempts=r["attempts_per_delivered_packet"],
                completion_ms=r["completion_ms"] if n else "")


def continuous(exp, configs, pds, pas, touts):
    """configs: list of (ws, wr). Returns jobs for every combination."""
    return [(exp, ws, wr, pd, pa, t, None, s)
            for ws, wr in configs for pd in pds for pa in pas for t in touts for s in SEEDS]


def window_sweep(ws_values=WS_SWEEP):
    """S&W reference + GBN and SR(3 Wr) over Ws."""
    cfgs = [(1, 1)]
    cfgs += [(ws, 1) for ws in ws_values if ws > 1]
    cfgs += [(ws, wr) for wr in SR_WR for ws in ws_values if ws > 1]
    return cfgs


BIG = [(1, 1), (BIG_WS, 1)] + [(BIG_WS, wr) for wr in SR_WR]

EXPERIMENTS = {
    # 2: no errors, efficiency vs Ws (deterministic, so one seed is enough)
    "exp2": lambda: [j for j in continuous("exp2", window_sweep(), [0], [0], [15]) if j[-1] == SEEDS[0]],
    # 3a: pa = 0, efficiency vs pd with a large window
    "exp3a": lambda: continuous("exp3a", BIG, [0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5], [0], [15]),
    # 3b: pd = 0.1, efficiency and attempts vs Ws
    "exp3b": lambda: continuous("exp3b", window_sweep(), [0.1], [0], [15]),
    # 4: pd = 0.1, vary pa; GBN also with a small window (Ws=12) to show the window effect
    "exp4": lambda: continuous("exp4", BIG + [(12, 1), (12, 12)],
                               [0.1], [0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5], [15]),
    # 5: pd = pa = 0.1, vary the timeout (RTT incl. transmission = 11 ms)
    "exp5": lambda: continuous("exp5", BIG, [0.1], [0.1],
                               [2, 4, 6, 8, 10, 11, 12, 13, 15, 20, 30, 50, 100]),
    # 6: finite transfer N = 100, three error cases
    "exp6": lambda: [("exp6", ws, wr, pd, pa, 15, 100, s)
                     for pd, pa in ((0, 0), (0.05, 0), (0.05, 0.05))
                     for ws, wr in window_sweep()
                     for s in FINITE_SEEDS],
    # 7: transfer size N = 10 and 1000 (plus N = 100 from exp6)
    "exp7": lambda: [("exp7", ws, wr, pd, pa, 15, n, s)
                     for n in (10, 1000)
                     for pd, pa in ((0, 0), (0.05, 0.05))
                     for ws, wr in window_sweep()
                     for s in FINITE_SEEDS],
}


def main():
    names = sys.argv[1:] or list(EXPERIMENTS)
    OUT.mkdir(exist_ok=True)
    with Pool() as pool:
        for name in names:
            jobs = EXPERIMENTS[name]()
            rows = pool.map(run_one, jobs, chunksize=4)
            path = OUT / f"{name}.csv"
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys())
                w.writeheader()
                w.writerows(rows)
            print(f"{name}: {len(rows)} runs -> {path}", flush=True)


if __name__ == "__main__":
    main()
