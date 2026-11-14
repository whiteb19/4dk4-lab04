import simpy
import numpy as np

from params import *

if WR != 1:
    raise ValueError("Go-Back-N requires a receive window size of 1")
if WS < 1 or M < WS + 1:
    raise ValueError("Go-Back-N requires 1 <= WS <= M - 1")

ack_seq_num = 0
ack_queue = None
env = None
window_base = 0

def get_next_seq_num(seq_num):
    return (seq_num + 1) % M

def send(seq_num):
    global ack_seq_num

    # the sender sends a packet. after PROP_TIME+TRANS_TIME it reaches the receiver
    print(f"{env.now}: Data packet {seq_num} sent")
    yield env.timeout(PROP_TIME + TRANS_TIME)

    # data packet successfully received
    if not (np.random.random() < PD):
        print(f"{env.now}: Data packet {seq_num} received")

        # the receiver checks the sequence number with the most recent ack's sequence number (i.e., expected seq number)
        # if its the right packet, move ack to next seq number
        if ack_seq_num == seq_num:
            ack_seq_num = get_next_seq_num(ack_seq_num)

        # the receiver sends back an ACK. after PROP_TIME it reaches the sender
        local_ack_seq_num = ack_seq_num
        print(f"{env.now}: Ack {local_ack_seq_num} sent")
        yield env.timeout(PROP_TIME)
        if not (np.random.random() < PA): # ack successfully transmitted
            print(f"{env.now}: Ack {local_ack_seq_num} received")
            yield ack_queue.put(local_ack_seq_num)

def within_send_window(packet_idx):
    global window_base
    return packet_idx - window_base < WS

def packet_idx_to_seq_num(packet_idx):
    return packet_idx % M

def sim():
    global window_base # idx first non-completed packet (since it starts at 0 this means window_base also hold total number of packets completed up until now)
    next_packet_idx = 0 # global packet id (ith packet)
    ack = ack_queue.get()
    timeout = None

    while window_base < RUN_TIME:
        while within_send_window(next_packet_idx):
            # set timeout based when the base of the window started sending
            if window_base == next_packet_idx:
                timeout = env.timeout(TIMEOUT)

            # send the packet
            env.process(send(packet_idx_to_seq_num(next_packet_idx)))
            next_packet_idx += 1
            yield env.timeout(TRANS_TIME) # wait until the packet is fully sent before moving onto next

        # once we've filled the window, wait for the next ack or timeout
        result = yield ack | timeout
        ack_received = ack in result
        timeout_expired = timeout in result

        window_advanced = False

        # if we receive the ack
        if ack_received:
            received_ack_seq_num = result[ack]
            ack = ack_queue.get() # move to the next ack in the queue

            # move the window forward if the ack matches the one we expect
            if received_ack_seq_num == get_next_seq_num(packet_idx_to_seq_num(window_base)):
                window_base += 1
                window_advanced = True
                print(f"{env.now}: Window base advanced to {window_base}")
                timeout = env.timeout(TIMEOUT)

        # note: if window_base < next_packet_idx, that means there are outstanding packets that need to be re-transmitted
        if (timeout_expired) and (not window_advanced) and (window_base < next_packet_idx):
            print(f"{env.now}: Timeout, retransmitting packets {range(window_base, next_packet_idx)}")
            for packet_idx in range(window_base, next_packet_idx):
                env.process(send(packet_idx_to_seq_num(packet_idx)))
                if packet_idx == window_base:
                    timeout = env.timeout(TIMEOUT)
                yield env.timeout(TRANS_TIME)

env = simpy.Environment()
ack_queue = simpy.Store(env)
sim_process = env.process(sim())
env.run(sim_process)