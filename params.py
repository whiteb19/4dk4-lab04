# propogation time parameters (data packets and ACKs)
PROP_TIME = 5 # ms
ROUND_TRIP_PROP_TIME = 2 * PROP_TIME # ms

# link parameters
LINK_BIT_RATE = 1e6 # bps
PD = 0.2 # data packet failure probability
PA = 0 # ack failure probability

# data packet transmission parameters
PACKET_LENGTH = 1000 # bits
TRANS_TIME = (PACKET_LENGTH / LINK_BIT_RATE) / 1e-3 # ms

# ACK transmission and processing times are neglected

# ARQ related parameters
TIMEOUT = 15 # ms
WS = 3 # send window size
WR = 1 # receive window size
M = 4 # sequence limit (modM), ex M=4 sequence numbers will be 0,1,2,3. M = 2**m, where m = # of sequence bits, Rule: Wmax = M-1

RUN_TIME = 5 # default finite-transfer packet count (legacy parameter name)