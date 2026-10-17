"""RV-3049-compatible SPI calendar on the M202 board GPIO.

CE/CLK/DATA occupy bits 6/4/7; control bit 7 selects DATA output (1) or
input (0). Commands and data are MSB first, with an 8-register page counter.
The clock cache stays stable until CE falls, including partial transactions.
Other registers retain written bytes; alarm/timer interrupts, temperature and
EEPROM programming behavior are not modeled by this calendar adapter.
Reference: Micro Crystal RV-3049-C2 Application Manual, sections 3.3 and 6.2.
"""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from rtc4543 import CE, CLK, DATA, DEFAULT_TIME


def _bcd(value: int) -> int:
    return (value // 10 << 4) | value % 10


def _decimal(value: int) -> int:
    if value & 15 > 9 or value >> 4 > 9:
        raise ValueError('invalid BCD')
    return (value >> 4) * 10 + (value & 15)


def calendar_bytes(value: dt.datetime) -> bytes:
    return bytes(map(_bcd, (value.second, value.minute, value.hour, value.day,
                           (value.weekday() + 1) % 7 + 1, value.month, value.year % 100)))


class Rtc3049:
    def __init__(self, initial_time: dt.datetime = DEFAULT_TIME, *,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._base_time = initial_time
        self._monotonic = monotonic
        self._base_tick = monotonic()
        self._registers = bytearray(128)  # Seven address bits in the command.
        self._registers[0] = 1           # WE: running calendar.
        self._registers[8:15] = calendar_bytes(initial_time)
        self._valid_calendar = True
        self._hour_mode = False
        self._weekday_offset = 0
        self._control = 0
        self._previous_port = 0
        self._cache = bytearray(self._registers)
        self._command = 0
        self._command_bits = 0
        self._address = 0
        self._reading = False
        self._read_bit = -1
        self._read_bytes = bytearray()
        self._write_byte = self._write_bits = 0
        self._written: set[int] = set()
        self.last_command: int | None = None
        self.completed_read: bytes | None = None
        self.completed_write: dt.datetime | None = None

    def now(self) -> dt.datetime:
        elapsed = max(0, int(self._monotonic() - self._base_tick))
        if not self._registers[0] & 1:  # WE cleared: hold the calendar.
            elapsed = 0
        return self._base_time + dt.timedelta(seconds=elapsed)

    def _snapshot(self) -> bytearray:
        if self._valid_calendar:
            when = self.now()
            fields = bytearray(calendar_bytes(when))
            fields[4] = ((fields[4] - 1 + self._weekday_offset) % 7) + 1
            if self._hour_mode:
                fields[2] = 0x40 | (0x20 if when.hour >= 12 else 0) | _bcd(when.hour % 12 or 12)
            self._registers[8:15] = fields
        return bytearray(self._registers)

    def control_write(self, control: int) -> None:
        self._control = control & 255

    def _next_address(self) -> None:
        self._address = (self._address & ~7) | ((self._address + 1) & 7)

    def _commit(self) -> None:
        if not self._written:
            return
        # Preserve elapsed time when only non-calendar registers are written.
        current = self.now()
        for address in self._written:
            self._registers[address] = self._cache[address]
        if self._written.intersection(range(8, 15)):
            second, minute, hour, day, weekday, month, year = self._registers[8:15]
            self._hour_mode = bool(hour & 0x40)
            try:
                h = _decimal(hour & (0x1F if self._hour_mode else 0x3F))
                if self._hour_mode:
                    if not 1 <= h <= 12:
                        raise ValueError('invalid 12-hour value')
                    h = h % 12 + (12 if hour & 0x20 else 0)
                value = dt.datetime(2000 + _decimal(year & 0x7F), _decimal(month & 0x1F),
                                    _decimal(day & 0x3F), h, _decimal(minute & 0x7F),
                                    _decimal(second & 0x7F))
                if not 1 <= weekday & 7 <= 7:
                    raise ValueError('invalid weekday')
            except ValueError:
                # Keep invalid guest-written bytes visible to its native checks.
                self._valid_calendar = False
                return
            self._valid_calendar = True
            self._weekday_offset = ((weekday & 7) - ((value.weekday() + 1) % 7 + 1)) % 7
            current = value
            self.completed_write = value
        self._base_time = current
        self._base_tick = self._monotonic()

    def port_write(self, port: int) -> int:
        previous = self._previous_port
        enabled = bool(port & CE)
        if enabled and not previous & CE:
            self._cache = self._snapshot()
            self._command = self._command_bits = 0
            self._reading = False
            self._read_bit = -1
            self._read_bytes.clear()
            self._write_byte = self._write_bits = 0
            self._written.clear()
            self.completed_read = self.completed_write = None
        if enabled and port & CLK and not previous & CLK:
            if self._command_bits < 8:
                self._command = (self._command << 1) | int(bool(port & DATA))
                self._command_bits += 1
                if self._command_bits == 8:
                    self.last_command = self._command
                    self._reading = bool(self._command & 0x80)
                    self._address = self._command & 0x7F
            elif self._reading:
                if self._read_bit == 7 and len(self._read_bytes) < 8:
                    self._read_bytes.append(self._cache[self._address])
            else:
                self._write_byte = (self._write_byte << 1) | int(bool(port & DATA))
                self._write_bits += 1
                if self._write_bits == 8:
                    self._cache[self._address] = self._write_byte
                    self._written.add(self._address)
                    self._next_address()
                    self._write_byte = self._write_bits = 0
        if enabled and previous & CLK and not port & CLK and self._reading:
            self._read_bit += 1
            if self._read_bit == 8:
                self._read_bit = 0
                self._next_address()
        if enabled and self._reading and not self._control & DATA and self._read_bit >= 0:
            bit = (self._cache[self._address] >> (7 - self._read_bit)) & 1
            port = (port & ~DATA) | (DATA if bit else 0)
        if previous & CE and not enabled:
            if self._reading and self._read_bytes:
                self.completed_read = bytes(self._read_bytes)
            else:
                self._commit()
        self._previous_port = port
        return port
