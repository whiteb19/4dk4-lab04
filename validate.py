import argparse
import csv
import contextlib
import io
from pathlib import Path

import sim


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
    "completion_ms",
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


def main():
    parser = argparse.ArgumentParser(
        description="Run deterministic ARQ validation scenarios and write CSV results."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation-results"),
        help="Directory for event_trace.csv and run_summary.csv.",
    )
    args = parser.parse_args()
    event_path, summary_path = run_validation(args.output_dir)
    print(f"Wrote {event_path} and {summary_path}")


if __name__ == "__main__":
    main()
