import argparse
import csv
import contextlib
import io
import statistics
from collections import defaultdict
from pathlib import Path

import sim


TIMEOUT = sim.TIMEOUT
STUDENT_ID_SEED = 400454641
DEFAULT_EXPERIMENT_SEEDS = (STUDENT_ID_SEED, 12345, 67890)

PROTOCOLS = (
    ("Stop-and-Wait", 1, 1, 2),
    ("Go-Back-N", 3, 1, 4),
    ("Selective-Repeat", 3, 3, 8),
)
PACKET_COUNT = 20
SEED = 4404

EVENT_FIELDS = (
    "seed", "case", "protocol", "ws", "wr", "m", "pd", "pa", "timeout_ms",
    "packet_count", "time_ms", "event", "packet_idx", "seq_num", "attempt",
)
SUMMARY_FIELDS = (
    "seed", "case", "protocol", "ws", "wr", "m", "pd", "pa", "timeout_ms",
    "packet_count", "data_attempts", "retransmissions", "delivered",
    "completion_ms", "measurement_time_ms", "link_efficiency",
    "attempts_per_delivered_packet", "total_data_attempts",
    "measurement_retransmissions", "total_retransmissions",
)


def run_validation(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    event_path = output_dir / "event_trace.csv"
    summary_path = output_dir / "run_summary.csv"

    with event_path.open("w", newline="", encoding="utf-8") as event_file, \
            summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        event_writer = csv.DictWriter(event_file, fieldnames=EVENT_FIELDS)
        summary_writer = csv.DictWriter(summary_file, fieldnames=SUMMARY_FIELDS)
        event_writer.writeheader()
        summary_writer.writeheader()

        for protocol, ws, wr, sequence_space in PROTOCOLS:
            for case in ("error_free", "data_loss", "ack_loss", "random_errors"):
                sim.WS = ws
                sim.WR = wr
                sim.M = sequence_space
                sim.RUN_TIME = PACKET_COUNT
                sim.PD = 0.5 if case == "random_errors" else 0
                sim.PA = 0.5 if case == "random_errors" else 0
                data_losses = {1: {1}} if case == "data_loss" else None
                ack_losses = {PACKET_COUNT - 1: {1}} if case == "ack_loss" else None

                with contextlib.redirect_stdout(io.StringIO()):
                    sim.run_sim(
                        seed=SEED,
                        data_losses=data_losses,
                        ack_losses=ack_losses,
                    )
                metadata = {
                    "seed": SEED,
                    "case": case,
                    "protocol": protocol,
                    "ws": ws,
                    "wr": wr,
                    "m": sequence_space,
                    "pd": sim.PD,
                    "pa": sim.PA,
                    "timeout_ms": sim.TIMEOUT,
                    "packet_count": PACKET_COUNT,
                }

                for event in sim.events:
                    event_writer.writerow({**metadata, **event})
                summary = {field: sim.last_result[field] for field in SUMMARY_FIELDS
                           if field in sim.last_result}
                summary_writer.writerow({**metadata, **summary})

    return event_path, summary_path


def run_lab_experiments(
    output_dir,
    seeds=DEFAULT_EXPERIMENT_SEEDS,
    measurement_time_ms=10000,
    warmup_time_ms=1000,
):
    if not seeds:
        raise ValueError("At least one seed is required")
    if measurement_time_ms <= 0 or warmup_time_ms < 0:
        raise ValueError("Measurement time must be positive and warm-up non-negative")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "lab_experiments.csv"
    summary_path = output_dir / "lab_experiments_summary.csv"
    ws_values = (1, 2, 4, 8, 12, 16)
    sr_receive_windows = (2, 4, 8)
    rows = []

    def run_case(
        experiment,
        source_mode,
        packet_count,
        ws,
        wr,
        pd,
        pa,
        timeout,
        sweep_ws=None,
    ):
        minimum_sequence_space = (
            ws + 1 if wr == 1 else ws + max(ws, wr)
        )
        sequence_space = 1 << (minimum_sequence_space - 1).bit_length()
        for seed in seeds:
            sim.run_sim(
                seed=seed,
                mode=source_mode,
                packet_count=packet_count,
                measurement_time_ms=measurement_time_ms,
                warmup_time_ms=warmup_time_ms,
                ws=ws,
                wr=wr,
                sequence_space=sequence_space,
                pd=pd,
                pa=pa,
                timeout_ms=timeout,
            )
            result = sim.last_result
            rows.append({
                "experiment": experiment,
                "mode": source_mode,
                "protocol": (
                    "Stop-and-Wait" if ws == 1 and wr == 1
                    else "Go-Back-N" if wr == 1
                    else "Selective-Repeat"
                ),
                "seed": seed,
                "ws": ws,
                "sweep_ws": ws if sweep_ws is None else sweep_ws,
                "wr": wr,
                "m": sequence_space,
                "pd": pd,
                "pa": pa,
                "timeout_ms": timeout,
                "packet_count": packet_count if source_mode == "finite" else "",
                "warmup_time_ms": warmup_time_ms if source_mode == "continuous" else "",
                "measurement_time_ms": result["measurement_time_ms"],
                "data_attempts": result["data_attempts"],
                "total_data_attempts": result["total_data_attempts"],
                "retransmissions": result["retransmissions"],
                "measurement_retransmissions": result["measurement_retransmissions"],
                "total_retransmissions": result["total_retransmissions"],
                "delivered": result["delivered"],
                "link_efficiency": result["link_efficiency"],
                "attempts_per_delivered_packet": result["attempts_per_delivered_packet"],
                "completion_ms": result["completion_ms"],
            })

    protocol_windows = [(1, 1)] + [(16, wr) for wr in (1,) + sr_receive_windows]

    for ws, wr in protocol_windows:
        sweep_values = ws_values[1:] if wr == 1 and ws > 1 else ws_values
        for sweep_ws in sweep_values:
            run_ws = min(sweep_ws, ws) if ws != 1 else 1
            run_case(
                "error_free_efficiency_vs_ws",
                "continuous",
                None,
                run_ws,
                wr,
                0,
                0,
                TIMEOUT,
                sweep_ws,
            )

    for ws, wr in ((1, 1),) + tuple((16, wr) for wr in (1,) + sr_receive_windows):
        for pd in (0, 0.1, 0.2, 0.3, 0.5):
            run_case("efficiency_vs_pd", "continuous", None, ws, wr, pd, 0, TIMEOUT)

    for wr in (1,) + sr_receive_windows:
        for ws in (ws_values[1:] if wr == 1 else ws_values):
            run_case(
                "metrics_vs_ws_with_data_errors",
                "continuous",
                None,
                ws,
                wr,
                0.2,
                0,
                TIMEOUT,
                ws,
            )

    for ws, wr in ((1, 1),) + tuple((16, wr) for wr in (1,) + sr_receive_windows):
        for pa in (0, 0.1, 0.2, 0.3, 0.5):
            run_case("metrics_vs_pa", "continuous", None, ws, wr, 0.1, pa, TIMEOUT)

    for ws, wr in ((1, 1),) + tuple((16, wr) for wr in (1,) + sr_receive_windows):
        for timeout in (5, 10, 15, 20, 30, 50):
            run_case("metrics_vs_timeout", "continuous", None, ws, wr, 0.1, 0.1, timeout)

    for packet_count, pd, pa in (
        (100, 0, 0),
        (100, 0.05, 0),
        (100, 0.05, 0.05),
    ):
        for wr in (1,) + sr_receive_windows:
            window_sizes = ws_values if wr == 1 else ws_values[1:]
            for ws in window_sizes:
                run_case(
                    "finite_completion_vs_ws",
                    "finite",
                    packet_count,
                    ws,
                    wr,
                    pd,
                    pa,
                    TIMEOUT,
                )

    for packet_count in (10, 1000):
        finite_configurations = [(1, 1)]
        finite_configurations.extend((ws, 1) for ws in (4, 8, 16))
        finite_configurations.extend(
            (ws, wr)
            for wr in sr_receive_windows
            for ws in (4, 8, 16)
        )
        for ws, wr in finite_configurations:
            run_case(
                "finite_completion_vs_transfer_size",
                "finite",
                packet_count,
                ws,
                wr,
                0.05,
                0.05,
                TIMEOUT,
                ws,
            )

    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    grouping_fields = (
        "experiment",
        "mode",
        "protocol",
        "ws",
        "sweep_ws",
        "wr",
        "m",
        "pd",
        "pa",
        "timeout_ms",
        "packet_count",
        "warmup_time_ms",
        "measurement_time_ms",
    )
    grouped = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in grouping_fields)
        grouped[key].append(row)

    summary_rows = []
    summary_metrics = (
        "data_attempts",
        "total_data_attempts",
        "retransmissions",
        "measurement_retransmissions",
        "total_retransmissions",
        "delivered",
        "link_efficiency",
        "attempts_per_delivered_packet",
        "completion_ms",
    )
    for key, group in grouped.items():
        summary = dict(zip(grouping_fields, key))
        summary["runs"] = len(group)
        for metric in summary_metrics:
            values = [row[metric] for row in group if row[metric] is not None]
            summary[f"{metric}_mean"] = statistics.mean(values) if values else ""
            summary[f"{metric}_stddev"] = (
                statistics.pstdev(values) if len(values) > 1 else 0
            )
        summary_rows.append(summary)

    summary_fields = grouping_fields + ("runs",) + tuple(
        f"{metric}_{stat}" for metric in summary_metrics for stat in ("mean", "stddev")
    )
    with summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        writer = csv.DictWriter(summary_file, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    return output_path, summary_path


def main():
    parser = argparse.ArgumentParser(
        description="Run ARQ correctness scenarios or the lab experiment sweeps."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation-results"),
        help="Directory for event_trace.csv and run_summary.csv.",
    )
    parser.add_argument(
        "--suite",
        choices=("validation", "lab", "all"),
        default="validation",
        help="Run deterministic validation, lab sweeps, or both.",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=DEFAULT_EXPERIMENT_SEEDS,
        help="Random seeds reused for each experiment configuration.",
    )
    parser.add_argument(
        "--measurement-ms",
        type=float,
        default=10000,
        help="Continuous measurement interval in milliseconds.",
    )
    parser.add_argument(
        "--warmup-ms",
        type=float,
        default=1000,
        help="Continuous simulation warm-up in milliseconds.",
    )
    args = parser.parse_args()
    if args.suite in {"validation", "all"}:
        event_path, summary_path = run_validation(args.output_dir)
        print(f"Wrote {event_path} and {summary_path}")
    if args.suite in {"lab", "all"}:
        experiment_path, experiment_summary_path = run_lab_experiments(
            args.output_dir,
            seeds=args.seeds,
            measurement_time_ms=args.measurement_ms,
            warmup_time_ms=args.warmup_ms,
        )
        print(f"Wrote {experiment_path} and {experiment_summary_path}")


if __name__ == "__main__":
    main()
