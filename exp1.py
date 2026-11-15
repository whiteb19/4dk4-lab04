"""Experiment 1: simulation verification.

Produces, for S&W, GBN and SR:
  * short human-readable event traces for (pd, pa) = (0, 0) and (0.5, 0.5);
  * automated checks over many seeds (exactly-once in-order delivery and
    event timing consistency);
  * a comparison of long-run efficiency against the analytic results.

Outputs go to results/exp1/.
"""
import statistics
from pathlib import Path

import sim
from params import PROP_TIME, TIMEOUT, TRANS_TIME

STUDENT_ID_SEED = 400454641
SEEDS = (STUDENT_ID_SEED, 12345, 67890)
OUT_DIR = Path("results/exp1")

# (name, Ws, Wr, sequence space M)
PROTOCOLS = (
    ("Stop-and-Wait", 1, 1, 2),
    ("Go-Back-N", 4, 1, 8),
    ("Selective-Repeat", 4, 4, 8),
)
ERROR_CASES = ((0.0, 0.0), (0.5, 0.5))
TRACE_PACKETS = 6

DESCRIPTIONS = {
    "data_sent": "SENDER   transmit data   ",
    "retransmission": None,  # folded into data_sent line
    "data_lost": "CHANNEL  data LOST       ",
    "data_received": "RECEIVER data arrives    ",
    "data_buffered": "RECEIVER buffer (out of order)",
    "data_discarded": "RECEIVER discard (outside window)",
    "delivered": "RECEIVER deliver to upper layer",
    "ack_sent": "RECEIVER send ACK       ",
    "ack_lost": "CHANNEL  ACK LOST        ",
    "ack_received": "SENDER   ACK arrives     ",
    "ack_accepted": "SENDER   ACK accepted -> window advances",
    "duplicate_or_stale_ack": "SENDER   duplicate/stale ACK ignored",
    "timeout": "SENDER   TIMEOUT         ",
}


def format_event(event, wr):
    kind = event["event"]
    label = DESCRIPTIONS.get(kind, kind)
    if label is None:
        return None
    parts = [f"{event['time_ms']:7.1f} ms  {label}"]
    if event["packet_idx"] is not None and kind not in {"ack_sent", "ack_lost", "ack_received", "ack_accepted"}:
        parts.append(f"pkt {event['packet_idx']}")
    if event["seq_num"] is not None:
        if kind.startswith("ack") or kind == "duplicate_or_stale_ack":
            meaning = "next expected" if wr == 1 else "acknowledges"
            parts.append(f"ACK seq {event['seq_num']} ({meaning})")
        else:
            parts.append(f"seq {event['seq_num']}")
    if kind == "data_sent" and event["attempt"] and event["attempt"] > 1:
        parts.append(f"[RETRANSMISSION, attempt {event['attempt']}]")
    return "  ".join(parts)


def write_traces():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, ws, wr, m in PROTOCOLS:
        for pd, pa in ERROR_CASES:
            sim.run_sim(
                seed=STUDENT_ID_SEED, ws=ws, wr=wr, sequence_space=m,
                pd=pd, pa=pa, packet_count=TRACE_PACKETS,
            )
            result = sim.last_result
            lines = [
                f"{name}: Ws={ws}, Wr={wr}, M={m}, pd={pd}, pa={pa}, "
                f"N={TRACE_PACKETS} packets, seed={STUDENT_ID_SEED}",
                f"Ttx={TRANS_TIME} ms, one-way propagation={PROP_TIME} ms, timeout={TIMEOUT} ms",
                "",
            ]
            lines += [line for e in sim.events if (line := format_event(e, wr))]
            lines += [
                "",
                f"Delivered (in order): {list(result['delivered_packets'])}",
                f"Data transmissions: {result['total_data_attempts']}, "
                f"completion time: {result['completion_ms']} ms",
            ]
            slug = f"{name.lower().replace('-', '_')}_pd{pd}_pa{pa}"
            path = OUT_DIR / f"trace_{slug}.txt"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            paths.append(path)
    return paths


def check_run(events, packet_count):
    """Return a list of problems found in one finite run's event log."""
    problems = []
    delivered = [e["packet_idx"] for e in events if e["event"] == "delivered"]
    if delivered != list(range(packet_count)):
        problems.append(f"delivery not exactly-once/in-order: {delivered[:20]}")

    sent_at = {}
    for e in events:
        key = (e["packet_idx"], e["attempt"])
        if e["event"] == "data_sent":
            sent_at[key] = e["time_ms"]
        elif e["event"] in {"data_received", "data_lost"}:
            expected = sent_at[key] + TRANS_TIME + PROP_TIME
            if abs(e["time_ms"] - expected) > 1e-9:
                problems.append(f"{e['event']} at {e['time_ms']}, expected {expected}")
    return problems


def run_checks(seed_count=300, packet_count=50):
    lines = []
    for name, ws, wr, m in PROTOCOLS:
        for pd, pa in ((0, 0), (0.5, 0.5), (0.2, 0.0), (0.0, 0.2)):
            failures = 0
            for seed in range(seed_count):
                sim.run_sim(
                    seed=seed, ws=ws, wr=wr, sequence_space=m,
                    pd=pd, pa=pa, packet_count=packet_count,
                )
                if check_run(sim.events, packet_count):
                    failures += 1
            lines.append(
                f"{name:17s} pd={pd:<4} pa={pa:<4}: {seed_count - failures}/{seed_count} "
                f"runs delivered all {packet_count} packets exactly once, in order, with correct timing"
            )
    return lines


def efficiency(ws, wr, m, pd, pa, measurement_ms=50000, warmup_ms=1000):
    values = []
    for seed in SEEDS:
        sim.run_sim(
            seed=seed, mode="continuous", measurement_time_ms=measurement_ms,
            warmup_time_ms=warmup_ms, ws=ws, wr=wr, sequence_space=m, pd=pd, pa=pa,
        )
        values.append(sim.last_result["link_efficiency"])
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0)


def theory_comparison():
    """Long-run efficiency vs analysis (Ttx=1 ms, RTT=10 ms, Tout=15 ms).

    S&W : eta = Ttx / (Ttx + RTT + P/(1-P) * Tout), with P = 1-(1-pd)(1-pa)
    GBN : eta = (1-pd) / (1 + (K-1) pd), K = Tout/Ttx (window >= 16, pa = 0)
    SR  : eta = 1 - pd (window large enough never to stall)
    """
    rtt = 2 * PROP_TIME
    k = TIMEOUT / TRANS_TIME
    rows = []
    for pd, pa in ((0, 0), (0.1, 0), (0.5, 0), (0.5, 0.5)):
        p = 1 - (1 - pd) * (1 - pa)
        sw_theory = TRANS_TIME / (TRANS_TIME + rtt + p / (1 - p) * TIMEOUT)
        sw = efficiency(1, 1, 2, pd, pa)
        rows.append(("S&W (Ws=Wr=1)", pd, pa, sw, sw_theory))
        if pa == 0:
            gbn = efficiency(32, 1, 64, pd, pa)
            rows.append(("GBN (Ws=32)", pd, pa, gbn, (1 - pd) / (1 + (k - 1) * pd)))
            sr = efficiency(128, 128, 256, pd, pa)
            rows.append(("SR (Ws=Wr=128)", pd, pa, sr, 1 - pd))
    lines = [f"{'protocol':16s} {'pd':>4} {'pa':>4}  {'simulated (mean ± sd, 3 seeds)':>32}  {'analysis':>8}"]
    for name, pd, pa, (mean, sd), theory in rows:
        lines.append(f"{name:16s} {pd:4} {pa:4}  {mean:22.4f} ± {sd:.4f}  {theory:8.4f}")
    return lines


def main():
    for path in write_traces():
        print(f"wrote {path}")
    summary = ["Automated trace checks", "======================", *run_checks(), "",
               "Efficiency vs analysis", "======================", *theory_comparison()]
    summary_path = OUT_DIR / "verification_summary.txt"
    summary_path.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
