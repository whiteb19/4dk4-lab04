# propogation time parameters (data packets and ACKs)
PROP_TIME = 5 # ms
ROUND_TRIP_PROP_TIME = 2 * PROP_TIME # ms

# link parameters
LINK_BIT_RATE = 1e6 # bps
PD = 0 # data packet failure probability
PA = 0 # ack failure probability

# data packet transmission parameters
PACKET_LENGTH = 1000 # bits
TRANS_TIME = (PACKET_LENGTH / LINK_BIT_RATE) / 1e-3 # ms

# ACK transmission and processing times are neglected

# ARQ related parameters
TIMEOUT = 15 # ms
WS = 1 # send window size
WR = 1 # receive window size

RUN_TIME = 5