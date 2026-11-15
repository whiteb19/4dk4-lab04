import gc
from dataclasses import dataclass

import numpy as np
import simpy

from params import *


@dataclass(frozen=True)
class SimulationConfig:
    ws: int = WS
    wr: int = WR
    sequence_space: int = M
    timeout_ms: float = TIMEOUT
    data_loss_probability: float = PD
    ack_loss_probability: float = PA
    propagation_ms: float = PROP_TIME
    transmission_ms: float = TRANS_TIME
    packet_length_bits: int = PACKET_LENGTH
    link_rate_bps: float = LINK_BIT_RATE

    def validate(self):
        if self.ws < 1 or self.wr < 1 or self.sequence_space < 1:
            raise ValueError("WS, WR, and M must be positive")
        if self.sequence_space & (self.sequence_space - 1):
            raise ValueError("M must be a power of two")
        # A larger sender window can leave old retransmissions outside the
        # receiver window, so reserve sequence space for the larger window.
        minimum_sequence_space = (
            self.ws + 1
            if self.wr == 1
            else self.ws + max(self.ws, self.wr)
        )
        if self.sequence_space < minimum_sequence_space:
            raise ValueError(
                f"Require M >= {minimum_sequence_space} for these window sizes"
            )
        if self.timeout_ms <= 0 or self.transmission_ms <= 0:
            raise ValueError("Timeout and transmission times must be positive")
        if self.propagation_ms < 0:
            raise ValueError("Propagation time cannot be negative")
        if not 0 <= self.data_loss_probability <= 1:
            raise ValueError("PD must be between 0 and 1")
        if not 0 <= self.ack_loss_probability <= 1:
            raise ValueError("PA must be between 0 and 1")
        if self.packet_length_bits <= 0 or self.link_rate_bps <= 0:
            raise ValueError("Packet length and link rate must be positive")


class ARQSimulation:
    def __init__(
        self,
        config,
        mode,
        packet_count,
        measurement_time_ms,
        warmup_time_ms,
        seed,
        data_losses,
        ack_losses,
        verbose,
    ):
        config.validate()
        if mode not in {"finite", "continuous"}:
            raise ValueError("mode must be 'finite' or 'continuous'")
        if mode == "finite" and (packet_count is None or packet_count < 1):
            raise ValueError("Finite transfers require packet_count >= 1")
        if mode == "finite" and (
            config.data_loss_probability == 1
            or config.ack_loss_probability == 1
        ):
            raise ValueError("Finite transfers cannot complete when PD or PA is 1")
        if mode == "continuous" and (
            measurement_time_ms is None or measurement_time_ms <= 0
        ):
            raise ValueError("Continuous mode requires measurement_time_ms > 0")
        if warmup_time_ms < 0:
            raise ValueError("warmup_time_ms cannot be negative")

        self.config = config
        self.mode = mode
        self.packet_count = packet_count
        self.measurement_time_ms = measurement_time_ms
        self.warmup_time_ms = warmup_time_ms
        self.seed = seed
        self.data_loss_schedule = data_losses or {}
        self.ack_loss_schedule = ack_losses or {}
        self.verbose = verbose

        self.env = simpy.Environment()
        self.tx_resource = simpy.Resource(self.env, capacity=1)
        self.window_base = 0
        self.next_packet_idx = 0
        self.receiver_base = 0
        self.receiver_buffer = set()
        self.sender_packets = {}
        self.window_changed = self.env.event()
        self.timeout_event = None
        # GBN/S&W: start time of the most recent transmission of each
        # unacknowledged packet; the timer for the window base expires
        # TIMEOUT after that packet's transmission began.
        self.gbn_send_time = {}
        self.data_attempts_by_packet = {}
        self.ack_attempts_by_packet = {}
        self.delivered_packets = []
        self.events = []
        self.first_transmission_time = None

    def record_event(self, event, packet_idx=None, seq_num=None, attempt=None):
        self.events.append({
            "time_ms": self.env.now,
            "event": event,
            "packet_idx": packet_idx,
            "seq_num": seq_num,
            "attempt": attempt,
        })

    def log(self, message):
        if self.verbose:
            print(f"{self.env.now}: {message}")

    def packet_idx_to_seq_num(self, packet_idx):
        return packet_idx % self.config.sequence_space

    def should_drop(self, schedule, packet_idx, attempt, probability):
        scheduled_attempts = schedule.get(packet_idx, ())
        return attempt in scheduled_attempts or (
            attempt not in scheduled_attempts and np.random.random() < probability
        )

    def receive_data(self, packet_idx):
        seq_num = self.packet_idx_to_seq_num(packet_idx)

        if self.config.wr == 1:
            if seq_num == self.packet_idx_to_seq_num(self.receiver_base):
                self.delivered_packets.append(self.receiver_base)
                self.record_event(
                    "delivered",
                    self.receiver_base,
                    seq_num,
                    self.data_attempts_by_packet.get(packet_idx, 0),
                )
                self.receiver_base += 1
            return self.packet_idx_to_seq_num(self.receiver_base)

        offset = (seq_num - self.packet_idx_to_seq_num(self.receiver_base)) % (
            self.config.sequence_space
        )
        if offset < self.config.wr:
            received_idx = self.receiver_base + offset
            if received_idx not in self.receiver_buffer:
                self.receiver_buffer.add(received_idx)
                if offset > 0:
                    self.record_event("data_buffered", received_idx, seq_num)
                    self.log(f"Receiver buffered packet {seq_num}")

            while self.receiver_base in self.receiver_buffer:
                self.receiver_buffer.remove(self.receiver_base)
                delivered_seq_num = self.packet_idx_to_seq_num(self.receiver_base)
                self.delivered_packets.append(self.receiver_base)
                self.record_event(
                    "delivered",
                    self.receiver_base,
                    delivered_seq_num,
                    self.data_attempts_by_packet.get(self.receiver_base, 0),
                )
                self.log(f"Receiver delivered packet {delivered_seq_num}")
                self.receiver_base += 1
            return seq_num

        behind = (
            self.packet_idx_to_seq_num(self.receiver_base) - seq_num
        ) % self.config.sequence_space
        if 0 < behind <= self.config.ws:
            return seq_num
        return None

    def handle_ack(self, seq_num):
        if self.config.wr == 1:
            advance = (
                seq_num - self.packet_idx_to_seq_num(self.window_base)
            ) % self.config.sequence_space
            outstanding = self.next_packet_idx - self.window_base
            if not 0 < advance <= outstanding:
                self.record_event("duplicate_or_stale_ack", seq_num=seq_num)
                return

            acknowledged_idx = self.window_base + advance - 1
            self.record_event("ack_accepted", acknowledged_idx, seq_num)
            for packet_idx in range(self.window_base, self.window_base + advance):
                self.gbn_send_time.pop(packet_idx, None)
            self.window_base += advance
            self.log(f"Window base advanced to {self.window_base}")
        else:
            offset = (
                seq_num - self.packet_idx_to_seq_num(self.window_base)
            ) % self.config.sequence_space
            packet_idx = self.window_base + offset
            state = self.sender_packets.get(packet_idx)
            if (
                state is None
                or packet_idx >= self.next_packet_idx
                or state["acked"]
            ):
                self.record_event(
                    "duplicate_or_stale_ack",
                    packet_idx=packet_idx,
                    seq_num=seq_num,
                )
                return

            self.record_event("ack_accepted", packet_idx, seq_num)
            state["acked"] = True
            state["ack_event"].succeed()
            self.log(f"Packet {seq_num} acknowledged")
            previous_base = self.window_base
            while (
                self.window_base < self.next_packet_idx
                and self.sender_packets[self.window_base]["acked"]
            ):
                del self.sender_packets[self.window_base]
                self.window_base += 1
            if self.window_base != previous_base:
                self.log(f"Window base advanced to {self.window_base}")

        if not self.window_changed.triggered:
            self.window_changed.succeed()

    def send(self, packet_idx, started_event=None, ack_event=None):
        seq_num = self.packet_idx_to_seq_num(packet_idx)
        with self.tx_resource.request() as request:
            if ack_event is None:
                yield request
            else:
                result = yield request | ack_event
                if ack_event in result:
                    return

            self.log(f"Data packet {seq_num} sent")
            if self.first_transmission_time is None:
                self.first_transmission_time = self.env.now
            if started_event is not None:
                started_event.succeed()
            attempt = self.data_attempts_by_packet.get(packet_idx, 0) + 1
            self.data_attempts_by_packet[packet_idx] = attempt
            self.record_event("data_sent", packet_idx, seq_num, attempt)
            if attempt > 1:
                self.record_event("retransmission", packet_idx, seq_num, attempt)
            yield self.env.timeout(self.config.transmission_ms)

        yield self.env.timeout(self.config.propagation_ms)

        if self.should_drop(
            self.data_loss_schedule,
            packet_idx,
            attempt,
            self.config.data_loss_probability,
        ):
            self.log(f"Data packet {seq_num} lost")
            self.record_event("data_lost", packet_idx, seq_num, attempt)
            return

        self.log(f"Data packet {seq_num} received")
        self.record_event("data_received", packet_idx, seq_num, attempt)
        ack_seq_num = self.receive_data(packet_idx)
        if ack_seq_num is None:
            self.log(f"Receiver discarded packet {seq_num}")
            self.record_event("data_discarded", packet_idx, seq_num, attempt)
            return

        self.log(f"Ack {ack_seq_num} sent")
        ack_attempt = self.ack_attempts_by_packet.get(packet_idx, 0) + 1
        self.ack_attempts_by_packet[packet_idx] = ack_attempt
        self.record_event("ack_sent", packet_idx, ack_seq_num, ack_attempt)
        yield self.env.timeout(self.config.propagation_ms)
        if self.should_drop(
            self.ack_loss_schedule,
            packet_idx,
            ack_attempt,
            self.config.ack_loss_probability,
        ):
            self.log(f"Ack {ack_seq_num} lost")
            self.record_event("ack_lost", packet_idx, ack_seq_num, ack_attempt)
            return

        self.log(f"Ack {ack_seq_num} received")
        self.record_event("ack_received", packet_idx, ack_seq_num, ack_attempt)
        self.handle_ack(ack_seq_num)

    def packet_timer(self, packet_idx):
        state = self.sender_packets[packet_idx]
        yield state["started_event"]

        while packet_idx in self.sender_packets and not self.sender_packets[packet_idx]["acked"]:
            state = self.sender_packets[packet_idx]
            result = yield state["ack_event"] | self.env.timeout(self.config.timeout_ms)
            if state["ack_event"] in result:
                return

            if packet_idx in self.sender_packets and not self.sender_packets[packet_idx]["acked"]:
                seq_num = self.packet_idx_to_seq_num(packet_idx)
                self.log(f"Timeout, retransmitting packet {seq_num}")
                self.record_event("timeout", packet_idx, seq_num)
                started_event = self.env.event()
                self.env.process(self.send(packet_idx, started_event, state["ack_event"]))
                result = yield state["ack_event"] | started_event
                if state["ack_event"] in result:
                    return

    def gbn_sender(self):
        """Go-Back-N sender (also Stop-and-Wait when WS = 1).

        A single send pointer walks through the window one transmission at a
        time. The retransmission timer always refers to the window base and
        expires TIMEOUT after the base packet's most recent transmission
        started. On expiry the send pointer goes back to the base, so every
        outstanding packet is resent in order. The timer is checked after
        every transmission, so a go-back can also interrupt an earlier
        go-back burst. A transmission already in progress is never aborted.
        """
        send_ptr = 0
        while True:
            if self.mode == "finite" and self.window_base >= self.packet_count:
                return
            # Cumulative ACKs may have overtaken the send pointer.
            send_ptr = max(send_ptr, self.window_base)

            # The timer only runs once the base has actually been (re)sent; right
            # after a go-back, send_ptr == window_base and the base is resent next.
            outstanding = self.window_base < send_ptr
            deadline = (
                self.gbn_send_time[self.window_base] + self.config.timeout_ms
                if outstanding
                else None
            )
            if deadline is not None and self.env.now >= deadline:
                for packet_idx in range(self.window_base, self.next_packet_idx):
                    self.record_event(
                        "timeout", packet_idx, self.packet_idx_to_seq_num(packet_idx)
                    )
                self.log(
                    "Timeout, going back to packet "
                    f"{self.packet_idx_to_seq_num(self.window_base)}"
                )
                send_ptr = self.window_base
                continue

            can_send = send_ptr < self.window_base + self.config.ws and (
                self.mode == "continuous" or send_ptr < self.packet_count
            )
            if can_send:
                packet_idx = send_ptr
                send_ptr += 1
                self.next_packet_idx = max(self.next_packet_idx, send_ptr)
                self.gbn_send_time[packet_idx] = self.env.now
                self.env.process(self.send(packet_idx))
                yield self.env.timeout(self.config.transmission_ms)
                continue

            # Window full (or nothing left to send): wait for an ACK that
            # advances the base, or for the base packet's timer to expire.
            if deadline is None:
                yield self.window_changed
            else:
                yield self.window_changed | self.env.timeout(deadline - self.env.now)
            if self.window_changed.triggered:
                self.window_changed = self.env.event()

    def sim(self):
        if self.config.wr == 1:
            yield from self.gbn_sender()
            return

        while True:
            while (
                (self.mode == "continuous" or self.next_packet_idx < self.packet_count)
                and self.next_packet_idx - self.window_base < self.config.ws
            ):
                packet_idx = self.next_packet_idx
                self.sender_packets[packet_idx] = {
                    "acked": False,
                    "ack_event": self.env.event(),
                    "started_event": self.env.event(),
                }
                self.env.process(self.packet_timer(packet_idx))
                self.env.process(
                    self.send(
                        packet_idx,
                        self.sender_packets[packet_idx]["started_event"],
                    )
                )

                self.next_packet_idx += 1
                yield self.env.timeout(self.config.transmission_ms)

            if self.mode == "finite" and self.window_base >= self.packet_count:
                return

            yield self.window_changed
            self.window_changed = self.env.event()

    def make_result(self):
        if self.mode == "finite":
            measurement_start = self.first_transmission_time or 0
            measurement_end = self.env.now
        else:
            measurement_start = self.warmup_time_ms
            measurement_end = self.warmup_time_ms + self.measurement_time_ms

        interval_events = [
            event for event in self.events
            if measurement_start <= event["time_ms"] < measurement_end
        ]
        delivered = sum(event["event"] == "delivered" for event in interval_events)
        data_attempts = sum(event["event"] == "data_sent" for event in interval_events)
        measured_retransmissions = sum(
            event["event"] == "retransmission" for event in interval_events
        )
        total_retransmissions = sum(
            max(0, attempts - 1)
            for attempts in self.data_attempts_by_packet.values()
        )
        measured_time_ms = measurement_end - measurement_start
        efficiency = (
            delivered * self.config.packet_length_bits
            / (self.config.link_rate_bps * measured_time_ms / 1000)
            if measured_time_ms > 0
            else 0
        )

        return {
            "mode": self.mode,
            "seed": self.seed,
            "completion_ms": (
                self.env.now - (self.first_transmission_time or 0)
                if self.mode == "finite"
                else None
            ),
            "measurement_start_ms": measurement_start,
            "measurement_time_ms": measured_time_ms,
            "data_attempts": data_attempts,
            "total_data_attempts": sum(self.data_attempts_by_packet.values()),
            "retransmissions": total_retransmissions,
            "measurement_retransmissions": measured_retransmissions,
            "total_retransmissions": total_retransmissions,
            "delivered": delivered,
            "link_efficiency": efficiency,
            "attempts_per_delivered_packet": (
                data_attempts / delivered if delivered else 0
            ),
            "delivered_packets": tuple(self.delivered_packets),
        }

    def run(self):
        if self.seed is not None:
            np.random.seed(self.seed)

        process = self.env.process(self.sim())
        if self.mode == "finite":
            self.env.run(until=process)
        else:
            horizon = self.warmup_time_ms + self.measurement_time_ms
            self.env.run(until=horizon)
        return self.make_result()


def run_sim(
    seed=None,
    data_losses=None,
    ack_losses=None,
    *,
    config=None,
    mode="finite",
    packet_count=None,
    measurement_time_ms=None,
    warmup_time_ms=0,
    ws=None,
    wr=None,
    sequence_space=None,
    timeout_ms=None,
    pd=None,
    pa=None,
    verbose=False,
):
    """Run a finite transfer or warm-up plus continuous interval.

    Finite runs return completion time; continuous runs return their end time.
    In both modes, counters and measurements are available in ``last_result``.
    """
    global events, last_result
    overrides = (ws, wr, sequence_space, timeout_ms, pd, pa)
    if config is not None and any(value is not None for value in overrides):
        raise ValueError("Pass either config or individual parameter overrides, not both")
    if config is None:
        config = SimulationConfig(
            ws=WS if ws is None else ws,
            wr=WR if wr is None else wr,
            sequence_space=M if sequence_space is None else sequence_space,
            timeout_ms=TIMEOUT if timeout_ms is None else timeout_ms,
            data_loss_probability=PD if pd is None else pd,
            ack_loss_probability=PA if pa is None else pa,
        )
    run_packet_count = RUN_TIME if packet_count is None else packet_count
    simulation = ARQSimulation(
        config=config,
        mode=mode,
        packet_count=run_packet_count,
        measurement_time_ms=measurement_time_ms,
        warmup_time_ms=warmup_time_ms,
        seed=seed,
        data_losses=data_losses,
        ack_losses=ack_losses,
        verbose=verbose,
    )
    result = simulation.run()
    event_log = simulation.events
    simulation.env = None
    simulation.tx_resource = None
    simulation.window_changed = None
    simulation.timeout_event = None
    simulation.sender_packets.clear()
    simulation.receiver_buffer.clear()
    del simulation
    events = event_log
    last_result = result
    # Discard detached SimPy timer/process cycles from completed or time-bounded runs.
    gc.collect()
    if mode == "finite":
        return result["completion_ms"]
    return warmup_time_ms + measurement_time_ms


events = []
last_result = {}


if __name__ == "__main__":
    run_sim(verbose=True)
