import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from database_profile import profile_for_header
from database_rtc import create_rtc, apply_rtc_gpio
from rtc3049 import Rtc3049, calendar_bytes
from rtc4543 import Rtc4543, CE, CLK, DATA, calendar_bits


class Bus:
    """Bit-bang the shared data line just like the native M202 routines."""
    def __init__(self, rtc):
        self.rtc = rtc
        self.control = 0xF0
        self.port = 0

    def drive(self, port):
        self.port = apply_rtc_gpio(self.rtc, self.control, port)
        return self.port

    def send(self, value, width=8):
        for shift in range(width - 1, -1, -1):
            port = CE | 3 | (DATA if value & (1 << shift) else 0)
            self.drive(port)
            self.drive(port | CLK)
            self.drive(self.port & ~CLK)

    def start(self, command):
        self.control = 0xF0
        self.drive(3)
        self.drive(CE | 3)
        self.send(command)
        if command & 0x80:
            self.control = 0x70
            self.drive(self.port)

    def receive(self):
        value = 0
        for _ in range(8):
            value = (value << 1) | bool(self.port & DATA)
            self.drive(self.port | CLK)
            self.drive(self.port & ~CLK)
        return value

    def stop(self):
        self.drive(self.port & ~CE)

    def read(self, command, size):
        self.start(command)
        data = bytes(self.receive() for _ in range(size))
        self.stop()
        return data

    def write(self, command, data):
        self.start(command)
        for value in data:
            self.send(value)
        self.stop()


class Rtc3049Tests(unittest.TestCase):
    def test_family_selection_covers_module_versions_and_keeps_old_bitstream(self):
        for family, model in ((0x61640400, Rtc4543), (0x61640600, Rtc3049)):
            for version in (0, 4, 127, 255):
                header = bytearray(0x54)
                header[12:16] = (family | version).to_bytes(4, 'big')
                rtc = create_rtc(profile_for_header(header), monotonic=lambda: 0)
                self.assertIsInstance(rtc, model)
                if model is Rtc4543:
                    apply_rtc_gpio(rtc, 0x70, CE)
                    bits = []
                    for _ in range(52):
                        bits.append(int(bool(apply_rtc_gpio(rtc, 0x70, CE | CLK) & DATA)))
                        apply_rtc_gpio(rtc, 0xF0, CE)
                    self.assertEqual(tuple(bits), calendar_bits(rtc.now()))

    def test_full_read_snapshot_then_seconds_poll_follows_minute_rollover(self):
        tick = [0.0]
        when = dt.datetime(2020, 2, 1, 22, 14, 59)
        rtc = Rtc3049(when, monotonic=lambda: tick[0])
        bus = Bus(rtc)
        bus.start(0x88)
        fields = []
        for _ in range(7):
            fields.append(bus.receive())
            tick[0] += 0.4
            self.assertEqual(bus.port & 3, 3)
        bus.stop()
        self.assertEqual(bytes(fields), calendar_bytes(when))
        self.assertEqual(rtc.completed_read, bytes(fields))
        self.assertEqual(bus.read(0x88, 1), b'\x01')
        self.assertEqual(bus.read(0x89, 2), bytes.fromhex('15 22'))

    def test_native_order_write_and_partial_write_with_incomplete_tail(self):
        tick = [0.0]
        rtc = Rtc3049(monotonic=lambda: tick[0])
        bus = Bus(rtc)
        target = dt.datetime(2020, 2, 29, 8, 3, 4)
        bus.write(0x08, calendar_bytes(target))
        self.assertEqual(rtc.now(), target)
        self.assertEqual(rtc.completed_write, target)
        bus.start(0x08)
        bus.send(0x45)
        bus.send(0x10)
        bus.send(0b1111, width=4)
        bus.stop()
        expected = target.replace(second=45, minute=10)
        self.assertEqual(rtc.now(), expected)
        self.assertEqual(bus.read(0x88, 7), calendar_bytes(expected))
        tick[0] = 16
        self.assertEqual(rtc.now(), expected + dt.timedelta(seconds=16))

    def test_page_counter_wraps_and_accepts_long_transactions(self):
        bus = Bus(Rtc3049(monotonic=lambda: 0))
        bus.write(0x3E, bytes(range(12)))
        # 3E,3F,38..3D,3E,3F,38,39; crossing the page never touches clock bytes.
        self.assertEqual(bus.read(0xBE, 12), bytes((8, 9, 10, 11, 4, 5, 6, 7, 8, 9, 10, 11)))
        self.assertEqual(bus.rtc.now(), dt.datetime(2012, 2, 1, 22, 14))

    def test_new_year_rollover_12_hour_mode_and_weekday_remapping(self):
        tick = [0.0]
        rtc = Rtc3049(dt.datetime(2026, 12, 31, 23, 59, 59), monotonic=lambda: tick[0])
        bus = Bus(rtc)
        tick[0] = 2
        self.assertEqual(bus.read(0x88, 7), calendar_bytes(dt.datetime(2027, 1, 1, 0, 0, 1)))
        bus.write(0x0A, b'\x72')  # 12 PM in 12-hour mode.
        self.assertEqual(rtc.now().hour, 12)
        self.assertEqual(bus.read(0x8A, 1), b'\x72')
        bus.write(0x0C, b'\x01')  # Weekday is a separate user-settable counter.
        tick[0] += 86400
        self.assertEqual(bus.read(0x8C, 1), b'\x02')

    def test_direction_change_does_not_clock_bus_or_clobber_cpu_output(self):
        rtc = Rtc3049(dt.datetime(2020, 1, 1, second=59), monotonic=lambda: 0)
        bus = Bus(rtc)
        bus.start(0x88)
        first = bus.port & DATA
        self.assertEqual(first, 0)
        bus.control = 0xF0
        self.assertTrue(bus.drive(bus.port | DATA) & DATA)
        bus.control = 0x70
        self.assertEqual(bus.drive(bus.port) & DATA, first)
        self.assertEqual(bus.receive(), 0x59)
        bus.stop()

    def test_invalid_clock_write_remains_visible_and_can_be_repaired(self):
        bus = Bus(Rtc3049(monotonic=lambda: 0))
        bus.write(0x08, b'\x6A')
        self.assertEqual(bus.read(0x88, 1), b'\x6A')
        bus.write(0x08, b'\x12')
        self.assertEqual(bus.rtc.now().second, 12)

    def test_we_stops_and_restarts_without_counting_stopped_time(self):
        tick = [0.0]
        rtc = Rtc3049(monotonic=lambda: tick[0])
        bus = Bus(rtc)
        tick[0] = 10
        bus.write(0, b'\0')
        stopped = rtc.now()
        tick[0] = 500
        self.assertEqual(rtc.now(), stopped)
        bus.write(0, b'\x01')
        tick[0] += 2
        self.assertEqual(rtc.now(), stopped + dt.timedelta(seconds=2))


if __name__ == '__main__':
    unittest.main()
