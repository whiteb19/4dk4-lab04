import contextlib
import io
import unittest

import sim


PROTOCOLS = (
    ("Stop-and-Wait", 1, 1, 2),
    ("Go-Back-N", 3, 1, 4),
    ("Selective-Repeat", 3, 3, 8),
)


class ProtocolValidationTests(unittest.TestCase):
    def run_transfer(self, protocol, data_losses=None, ack_losses=None, pd=0, pa=0, seed=7):
        name, ws, wr, sequence_space = protocol
        sim.WS = ws
        sim.WR = wr
        sim.M = sequence_space
        sim.RUN_TIME = 20
        sim.PD = pd
        sim.PA = pa

        with contextlib.redirect_stdout(io.StringIO()):
            sim.run_sim(seed=seed, data_losses=data_losses, ack_losses=ack_losses)
        return tuple(sim.events), dict(sim.last_result)

    def assert_transfer_complete(self, result, packet_count=20):
        self.assertEqual(result["delivered"], packet_count)
        self.assertEqual(result["delivered_packets"], tuple(range(packet_count)))
        self.assertGreater(result["completion_ms"], 0)

    def test_error_free_transfer_for_each_protocol(self):
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol[0]):
                events, result = self.run_transfer(protocol)
                self.assert_transfer_complete(result)
                self.assertEqual(result["data_attempts"], 20)
                self.assertEqual(result["retransmissions"], 0)
                self.assertFalse(any(event["event"] == "timeout" for event in events))

    def test_data_loss_recovers_for_each_protocol(self):
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol[0]):
                events, result = self.run_transfer(protocol, data_losses={1: {1}})
                self.assert_transfer_complete(result)
                self.assertTrue(any(
                    event["event"] == "data_lost" and event["packet_idx"] == 1
                    for event in events
                ))
                self.assertTrue(any(
                    event["event"] == "retransmission" and event["packet_idx"] == 1
                    for event in events
                ))

    def test_lost_final_ack_recovers_for_each_protocol(self):
        packet_count = 20
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol[0]):
                events, result = self.run_transfer(
                    protocol,
                    ack_losses={packet_count - 1: {1}},
                )
                self.assert_transfer_complete(result, packet_count)
                self.assertTrue(any(
                    event["event"] == "ack_lost"
                    and event["packet_idx"] == packet_count - 1
                    for event in events
                ))
                self.assertTrue(any(
                    event["event"] == "timeout"
                    and event["packet_idx"] == packet_count - 1
                    for event in events
                ))

    def test_selective_repeat_buffers_gap_and_retries_only_missing_packet(self):
        protocol = PROTOCOLS[2]
        events, result = self.run_transfer(protocol, data_losses={1: {1}})
        self.assert_transfer_complete(result)
        self.assertTrue(any(
            event["event"] == "data_buffered" and event["packet_idx"] == 2
            for event in events
        ))
        timed_out_packets = {
            event["packet_idx"] for event in events if event["event"] == "timeout"
        }
        self.assertEqual(timed_out_packets, {1})

    def test_go_back_n_retransmits_outstanding_window_after_gap(self):
        events, result = self.run_transfer(PROTOCOLS[1], data_losses={1: {1}})
        self.assert_transfer_complete(result)
        timed_out_packets = {
            event["packet_idx"] for event in events if event["event"] == "timeout"
        }
        self.assertEqual(timed_out_packets, {1, 2, 3})

    def test_random_data_and_ack_losses_recover_with_fixed_seed(self):
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol[0]):
                events, result = self.run_transfer(protocol, pd=0.5, pa=0.5, seed=123)
                self.assert_transfer_complete(result)
                self.assertGreater(result["retransmissions"], 0)
                self.assertTrue(any(
                    event["event"] in {"data_lost", "ack_lost"} for event in events
                ))

    def test_invalid_window_configuration_is_rejected(self):
        sim.WS, sim.WR, sim.M = 3, 3, 4
        with self.assertRaises(ValueError):
            sim.run_sim()


if __name__ == "__main__":
    unittest.main()
