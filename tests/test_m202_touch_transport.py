import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from m202_touch_transport import ACK, ACK_TIMEOUT, M202TouchTransport

DOWN = bytes.fromhex('01 02 33 00 5E 4C FB 02 12 01 04')
UP = bytes.fromhex('01 02 33 00 5E 4C FF FF FF FF 04')


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def transport(mode=None):
    clock = Clock()
    return M202TouchTransport(mode, clock=clock), clock


class M202TouchTransportTests(unittest.TestCase):
    def test_completed_tap_is_one_point_and_release_with_balanced_native_acks(self):
        t, _ = transport()
        self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(UP, host_released=True), (DOWN, b''))
        self.assertEqual(t.receive(ACK), b'')
        self.assertEqual(t.next_frame(), UP)
        self.assertEqual(t.receive(ACK), ACK)
        self.assertEqual(t.skip_acks, 0)

    def test_the_pc_gets_each_frame_only_after_acknowledging_the_previous(self):
        # The game handles one frame per serial event; a second frame in the
        # same burst waited for the next bytes and the menu auto-repeated.
        t, clock = transport()
        t.frame(DOWN, host_released=True)
        self.assertEqual(t.frame(UP, host_released=True), (DOWN, b''))
        self.assertEqual(t.next_frame(), b'')
        clock.now += 0.1
        self.assertEqual(t.next_frame(), b'')
        self.assertEqual(t.receive(ACK[:2]), b'')
        self.assertEqual(t.next_frame(), b'')
        self.assertEqual(t.receive(ACK[2:]), b'')
        self.assertEqual(t.next_frame(), UP)

    def test_an_unacknowledged_frame_holds_the_next_only_for_a_while(self):
        t, clock = transport()
        t.frame(DOWN, host_released=True)
        t.frame(UP, host_released=True)
        clock.now += ACK_TIMEOUT
        self.assertEqual(t.next_frame(), UP)

    def test_repeated_taps_at_the_same_point_remain_independent(self):
        t, _ = transport()
        for _ in range(3):
            self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
            self.assertEqual(t.frame(UP, host_released=True), (DOWN, b''))
            self.assertEqual(t.receive(ACK), b'')
            self.assertEqual(t.next_frame(), UP)
            self.assertEqual(t.receive(ACK), ACK)

    def test_reserved_ack_can_be_split_at_every_tcp_boundary(self):
        for boundary in range(5):
            t, _ = transport()
            t.frame(DOWN, host_released=True)
            t.frame(UP, host_released=True)
            self.assertEqual(t.receive(ACK[:boundary]) + t.receive(ACK[boundary:]), b'')
            self.assertEqual(t.next_frame(), UP)
            self.assertEqual(t.receive(ACK[:boundary]) + t.receive(ACK[boundary:]), ACK)

    def test_other_guest_messages_and_malformed_ack_prefixes_are_preserved(self):
        t, _ = transport()
        t.frame(DOWN, host_released=True)
        t.frame(UP, host_released=True)
        other = b'\x03\x22\x06\x03\x01abc'
        self.assertEqual(t.receive(other+ACK), other)
        self.assertEqual(t.next_frame(), UP)
        self.assertEqual(t.receive(ACK), ACK)

    def test_real_held_contact_streams_and_real_drag_positions_are_preserved(self):
        t, _ = transport()
        self.assertEqual(t.frame(DOWN, host_released=False), (DOWN, b''))
        self.assertEqual(t.receive(ACK), ACK)
        self.assertEqual(t.frame(UP, host_released=False), (UP, b''))
        self.assertEqual(t.receive(ACK), ACK)
        moved = DOWN[:6]+bytes.fromhex('FC 02 12 01')+DOWN[10:]
        t.frame(DOWN, host_released=True)
        t.frame(moved, host_released=True)
        sent = [t.frame(UP, host_released=True)[0]]
        for _ in range(2):
            self.assertEqual(t.receive(ACK), b'')
            sent.append(t.next_frame())
        self.assertEqual(sent, [DOWN, moved, UP])
        self.assertEqual(t.receive(ACK), ACK)

    def test_other_targets_and_protocols_are_forwarded_unchanged(self):
        t, _ = transport()
        t.frame(DOWN, host_released=True)
        other = DOWN[:4]+b'\x00\x01'+DOWN[6:]
        legacy = DOWN[:2]+b'\x41'+DOWN[3:]
        for frame in (other, legacy, b'\x06', b'\x01\x02\x38\x00abc\x04'):
            self.assertEqual(t.frame(frame, host_released=True), (frame,b''))
        self.assertEqual(t.receive(b'unrelated'), b'unrelated')

    def test_point_only_diagnostic_withholds_release_and_keeps_acks_balanced(self):
        t, _ = transport('point_only')
        self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
        # The PC sees one point; the MCU gets its release ACK locally.
        self.assertEqual(t.frame(UP, host_released=True), (DOWN, ACK))
        self.assertEqual(t.receive(ACK), b'')
        self.assertEqual(t.next_frame(), b'')
        self.assertEqual(t.skip_acks, 0)

    def test_tap_mode_comes_from_environment_and_rejects_unknown_values(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'M90_M202_TAP_MODE': 'point_only'}):
            self.assertEqual(M202TouchTransport().mode, 'point_only')
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(M202TouchTransport().mode, 'point_release')
        with self.assertRaises(ValueError):
            M202TouchTransport('bogus')

    def test_point_sent_while_the_finger_was_down_is_not_sent_again(self):
        point = b'\x01\x02\x33\x00\x5e\x4c\x04\x03\x0f\x01\x04'
        release = b'\x01\x02\x33\x00\x5e\x4c\xff\xff\xff\xff\x04'
        t, _ = transport('point_release')
        self.assertEqual(t.frame(point, host_released=False), (point, b''))   # live
        self.assertEqual(t.receive(ACK), ACK)
        self.assertEqual(t.frame(point, host_released=True), (b'', ACK))     # liftoff copy
        self.assertEqual(t.frame(release, host_released=True), (release, b''))
        self.assertEqual(t.receive(ACK), ACK)   # the PC ACKs the release for the MCU
        self.assertEqual(t.skip_acks, 0)
        # The next contact at the same place is a new tap again.
        self.assertEqual(t.frame(point, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(release, host_released=True), (point, b''))
        self.assertEqual(t.receive(ACK), b'')
        self.assertEqual(t.next_frame(), release)

    def test_host_release_ends_a_live_contact_before_the_late_firmware_liftoff(self):
        t, _ = transport()
        self.assertEqual(t.frame(DOWN, host_released=False), (DOWN, b''))
        self.assertTrue(t.host_release())
        # The release follows the point's acknowledgement, which the MCU gets.
        self.assertEqual(t.next_frame(), b'')
        self.assertEqual(t.receive(ACK), b'')
        self.assertEqual(t.next_frame(), UP)
        self.assertEqual(t.receive(ACK), ACK)
        # The firmware's late liftoff point and release are acknowledged locally.
        self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(UP, host_released=True), (b'', ACK))
        self.assertEqual(t.skip_acks, 0)
        # The next contact is a normal tap again.
        self.assertEqual(t.frame(DOWN, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(UP, host_released=True), (DOWN, b''))

    def test_host_release_without_a_point_at_the_pc_sends_nothing(self):
        t, _ = transport()
        self.assertFalse(t.host_release())
        t.frame(DOWN, host_released=True)   # retained, completed by the firmware
        self.assertFalse(t.host_release())
        self.assertEqual(t.frame(UP, host_released=True), (DOWN, b''))
        self.assertFalse(t.host_release())
        self.assertFalse(transport('point_only')[0].host_release())

    def test_service_points_without_a_firmware_release_reach_the_pc(self):
        # The service object gets the contact and liftoff points, never -1,-1.
        service = DOWN[:4] + b'\x35\x4C' + DOWN[6:]
        t, clock = transport()
        self.assertEqual(t.frame(service, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(service, host_released=True), (b'', ACK))   # liftoff
        clock.now += 0.3
        self.assertEqual(t.next_frame(), b'')
        clock.now += 0.2
        self.assertEqual(t.next_frame(), service)
        self.assertEqual(t.receive(ACK), b'')   # the MCU got its ACKs locally
        self.assertEqual(t.skip_acks, 0)
        # Known now: the next tap goes out at its liftoff, without waiting.
        other = service[:6] + bytes.fromhex('10 00 20 01') + service[10:]
        self.assertEqual(t.frame(other, host_released=True), (b'', ACK))
        self.assertEqual(t.frame(other, host_released=True), (other, ACK))
        self.assertEqual(t.receive(ACK), b'')
        # A held contact there gets no synthetic release (a corner click).
        self.assertEqual(t.frame(service, host_released=False), (service, b''))
        self.assertFalse(t.host_release())
        self.assertEqual(t.receive(ACK), ACK)

    def test_menu_release_right_behind_the_liftoff_is_still_one_tap(self):
        t, clock = transport()
        t.frame(DOWN, host_released=True)
        t.frame(DOWN, host_released=True)   # liftoff
        clock.now += 0.1
        self.assertEqual(t.next_frame(), b'')
        self.assertEqual(t.frame(UP, host_released=True), (DOWN, b''))
        self.assertEqual(t.no_release, set())

    def test_release_gap_is_used_only_in_its_diagnostic_mode(self):
        from unittest import mock
        self.assertEqual(M202TouchTransport('point_release').release_gap, 0.0)
        self.assertEqual(M202TouchTransport('point_release_gap').release_gap, 0.15)
        with mock.patch.dict('os.environ', {'M90_M202_TAP_GAP_MS': '80'}):
            self.assertEqual(M202TouchTransport('point_release_gap').release_gap, 0.08)

    def test_release_gap_delays_only_the_release_after_its_point(self):
        t, clock = transport('point_release_gap')
        t.frame(DOWN, host_released=True)
        t.frame(UP, host_released=True)
        t.receive(ACK)
        self.assertEqual(t.next_frame(), b'')
        clock.now += 0.15
        self.assertEqual(t.next_frame(), UP)


if __name__ == '__main__':
    unittest.main()
