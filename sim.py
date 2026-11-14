import simpy
# from simpy_helpers import Entity, Resource, Source, Stats
import numpy as np
# import matplotlib.pyplot as plt

from params import *

ack_seq_num = 0 # holds the most recently sent ACK sequence number, i.e., the next seq number we expect to see on a data packet

def get_next_seq_num(seq_num):
    return (seq_num+1) % (WR+1)  # mod window size + 1 because if a window size of 1 we need 0 and 1 as options, so need mod 2

def send(seq_num, ack):
    global ack_seq_num
    global env

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
        print(f"{env.now}: Ack {ack_seq_num} sent")
        yield env.timeout(PROP_TIME)
        if not (np.random.random() < PD): # ack successfully transmitted
            print(f"{env.now}: Ack {ack_seq_num} received")
            ack.succeed(ack_seq_num)
    
def sim():
    data_seq_num = 0

    for data_pkt in range(RUN_TIME):
        success = False
        while not success: # retry until ack is successfully received
            ack = env.event()
            env.process(send(data_seq_num, ack)) # sends the packet
            res = yield ack | env.timeout(TIMEOUT) # wait for the ack to be successfully returned or the timeout to elapse

            # if the ack with next seq number was returned, the packet was success
            if (ack in res) and (res[ack] == get_next_seq_num(data_seq_num)):
                data_seq_num = get_next_seq_num(data_seq_num)
                break # stop trying to transmit this packet

env = simpy.Environment()
sim_process = env.process(sim())
env.run(sim_process)