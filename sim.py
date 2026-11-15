import simpy
import numpy as np

from params import *


def validate_parameters():
    if WS < 1 or WR < 1 or M < 1 or WS + WR > M:
        raise ValueError("Require WS >= 1, WR >= 1, and WS + WR <= M")


validate_parameters()

env = None
window_base = 0
next_packet_idx = 0
receiver_base = 0
receiver_buffer = set()
sender_packets = {}
window_changed = None
timeout_event = None
tx_resource = None
data_loss_schedule = {}
ack_loss_schedule = {}
events = []
data_attempts_by_packet = {}
ack_attempts_by_packet = {}
delivered_packets = []


def record_event(event, packet_idx=None, seq_num=None, attempt=None):
    events.append({
        "time_ms": env.now,
        "event": event,
        "packet_idx": packet_idx,
        "seq_num": seq_num,
        "attempt": attempt,
    })


def should_drop(schedule, packet_idx, attempt, probability):
    scheduled_attempts = schedule.get(packet_idx, ())
    return attempt in scheduled_attempts or (
        attempt not in scheduled_attempts and np.random.random() < probability
    )


def packet_idx_to_seq_num(packet_idx):
    return packet_idx % M


def receive_data(seq_num):
    global receiver_base

    if WR == 1:
        if seq_num == packet_idx_to_seq_num(receiver_base):
            delivered_packets.append(receiver_base)
            record_event("delivered", receiver_base, seq_num)
            receiver_base += 1
        return packet_idx_to_seq_num(receiver_base)

    offset = (seq_num - packet_idx_to_seq_num(receiver_base)) % M
    if offset < WR:
        packet_idx = receiver_base + offset
        if packet_idx not in receiver_buffer:
            receiver_buffer.add(packet_idx)
            if offset > 0:
                record_event("data_buffered", packet_idx, seq_num)
                print(f"{env.now}: Receiver buffered packet {seq_num}")

        while receiver_base in receiver_buffer:
            receiver_buffer.remove(receiver_base)
            delivered_packets.append(receiver_base)
            record_event("delivered", receiver_base, packet_idx_to_seq_num(receiver_base))
            print(f"{env.now}: Receiver delivered packet {packet_idx_to_seq_num(receiver_base)}")
            receiver_base += 1
        return seq_num

    behind = (packet_idx_to_seq_num(receiver_base) - seq_num) % M
    if 0 < behind <= WS:
        return seq_num
    return None


def handle_ack(seq_num):
    global window_base, timeout_event, window_changed

    if WR == 1:
        advance = (seq_num - packet_idx_to_seq_num(window_base)) % M
        outstanding = next_packet_idx - window_base
        if not 0 < advance <= outstanding:
            record_event("duplicate_or_stale_ack", seq_num=seq_num)
            return

        record_event("ack_accepted", packet_idx=window_base + advance - 1, seq_num=seq_num)
        window_base += advance
        print(f"{env.now}: Window base advanced to {window_base}")
        timeout_event = env.timeout(TIMEOUT) if window_base < next_packet_idx else None
    else:
        offset = (seq_num - packet_idx_to_seq_num(window_base)) % M
        packet_idx = window_base + offset
        state = sender_packets.get(packet_idx)
        if state is None or packet_idx >= next_packet_idx or state["acked"]:
            record_event("duplicate_or_stale_ack", packet_idx=packet_idx, seq_num=seq_num)
            return

        record_event("ack_accepted", packet_idx=packet_idx, seq_num=seq_num)
        state["acked"] = True
        state["ack_event"].succeed()
        print(f"{env.now}: Packet {seq_num} acknowledged")
        previous_base = window_base
        while window_base < next_packet_idx and sender_packets[window_base]["acked"]:
            del sender_packets[window_base]
            window_base += 1
        if window_base != previous_base:
            print(f"{env.now}: Window base advanced to {window_base}")

    if not window_changed.triggered:
        window_changed.succeed()


def send(packet_idx, started_event=None, ack_event=None):
    seq_num = packet_idx_to_seq_num(packet_idx)
    with tx_resource.request() as request:
        if ack_event is None:
            yield request
        else:
            result = yield request | ack_event
            if ack_event in result:
                return

        print(f"{env.now}: Data packet {seq_num} sent")
        if started_event is not None:
            started_event.succeed()
        attempt = data_attempts_by_packet.get(packet_idx, 0) + 1
        data_attempts_by_packet[packet_idx] = attempt
        record_event("data_sent", packet_idx, seq_num, attempt)
        if attempt > 1:
            record_event("retransmission", packet_idx, seq_num, attempt)
        yield env.timeout(TRANS_TIME)

    yield env.timeout(PROP_TIME)

    if should_drop(data_loss_schedule, packet_idx, attempt, PD):
        print(f"{env.now}: Data packet {seq_num} lost")
        record_event("data_lost", packet_idx, seq_num, attempt)
        return

    print(f"{env.now}: Data packet {seq_num} received")
    record_event("data_received", packet_idx, seq_num, attempt)
    ack_seq_num = receive_data(seq_num)
    if ack_seq_num is None:
        print(f"{env.now}: Receiver discarded packet {seq_num}")
        record_event("data_discarded", packet_idx, seq_num, attempt)
        return

    print(f"{env.now}: Ack {ack_seq_num} sent")
    ack_attempt = ack_attempts_by_packet.get(packet_idx, 0) + 1
    ack_attempts_by_packet[packet_idx] = ack_attempt
    record_event("ack_sent", packet_idx, ack_seq_num, ack_attempt)
    yield env.timeout(PROP_TIME)
    if should_drop(ack_loss_schedule, packet_idx, ack_attempt, PA):
        print(f"{env.now}: Ack {ack_seq_num} lost")
        record_event("ack_lost", packet_idx, ack_seq_num, ack_attempt)
        return

    print(f"{env.now}: Ack {ack_seq_num} received")
    record_event("ack_received", packet_idx, ack_seq_num, ack_attempt)
    handle_ack(ack_seq_num)


def packet_timer(packet_idx):
    state = sender_packets[packet_idx]
    first_started = state["started_event"]
    yield first_started

    while packet_idx in sender_packets and not sender_packets[packet_idx]["acked"]:
        state = sender_packets[packet_idx]
        result = yield state["ack_event"] | env.timeout(TIMEOUT)
        if state["ack_event"] in result:
            return

        if packet_idx in sender_packets and not sender_packets[packet_idx]["acked"]:
            print(f"{env.now}: Timeout, retransmitting packet {packet_idx_to_seq_num(packet_idx)}")
            record_event("timeout", packet_idx, packet_idx_to_seq_num(packet_idx))
            started_event = env.event()
            env.process(send(packet_idx, started_event, state["ack_event"]))
            result = yield state["ack_event"] | started_event
            if state["ack_event"] in result:
                return


def sim():
    global window_base, next_packet_idx, timeout_event, window_changed

    while window_base < RUN_TIME:
        while next_packet_idx < RUN_TIME and next_packet_idx - window_base < WS:
            packet_idx = next_packet_idx
            if WR == 1:
                if packet_idx == window_base:
                    timeout_event = env.timeout(TIMEOUT)
            else:
                sender_packets[packet_idx] = {
                    "acked": False,
                    "ack_event": env.event(),
                    "started_event": env.event(),
                }
                env.process(packet_timer(packet_idx))

            if WR == 1:
                env.process(send(packet_idx))
            else:
                env.process(send(packet_idx, sender_packets[packet_idx]["started_event"]))
            next_packet_idx += 1
            yield env.timeout(TRANS_TIME)

        if window_base >= RUN_TIME:
            break

        if WR == 1:
            active_timeout = timeout_event
            if active_timeout is None:
                continue
            result = yield window_changed | active_timeout
            timed_out = active_timeout in result and active_timeout is timeout_event
            window_changed = env.event()
            if timed_out and window_base < next_packet_idx:
                print(f"{env.now}: Timeout, retransmitting packets {range(window_base, next_packet_idx)}")
                for packet_idx in range(window_base, next_packet_idx):
                    record_event("timeout", packet_idx, packet_idx_to_seq_num(packet_idx))
                timeout_event = env.timeout(TIMEOUT)
                for packet_idx in range(window_base, next_packet_idx):
                    env.process(send(packet_idx))
                    yield env.timeout(TRANS_TIME)
        else:
            yield window_changed
            window_changed = env.event()


def run_sim(seed=None, data_losses=None, ack_losses=None):
    global env, window_base, next_packet_idx, receiver_base
    global receiver_buffer, sender_packets, window_changed, timeout_event, tx_resource
    global data_loss_schedule, ack_loss_schedule, events
    global data_attempts_by_packet, ack_attempts_by_packet, delivered_packets

    validate_parameters()
    if seed is not None:
        np.random.seed(seed)
    env = simpy.Environment()
    tx_resource = simpy.Resource(env, capacity=1)
    window_base = 0
    next_packet_idx = 0
    receiver_base = 0
    receiver_buffer = set()
    sender_packets = {}
    window_changed = env.event()
    timeout_event = None
    data_loss_schedule = data_losses or {}
    ack_loss_schedule = ack_losses or {}
    events = []
    data_attempts_by_packet = {}
    ack_attempts_by_packet = {}
    delivered_packets = []
    env.process(sim())
    env.run()
    last_result.clear()
    last_result.update({
        "completion_ms": env.now,
        "data_attempts": sum(data_attempts_by_packet.values()),
        "retransmissions": sum(max(0, attempts - 1) for attempts in data_attempts_by_packet.values()),
        "delivered": len(delivered_packets),
        "delivered_packets": tuple(delivered_packets),
    })
    return env.now


last_result = {}


if __name__ == "__main__":
    run_sim()
