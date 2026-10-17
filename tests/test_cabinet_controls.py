"""Offline checks for local cabinet controls and firmware switch mapping."""

from __future__ import annotations

import sys
import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cabinet_controls import (
    CabinetControlServer, KEY_CURRENT_BASE, KEY_EVENT_BASE, KEY_IDS, KEY_LAYOUTS,
    KEY_TABLE_BASE, format_tablet_packet, key_active_high, key_location, send_command,
    validate_command,
)
from cabinet_control_panel import ControlPanel, qmp_screendump
import cabinet_control_panel as panel_module
from m68k_database_bridge import (
    BUTTON_PULSE_BOARD_SCANS, MP_STATE_ADDRESS, advance_button_pulses,
    publish_cabinet_buttons,
)


class CabinetDisplayTests(unittest.TestCase):
    def test_calibration_reset_asks_first_and_sends_reset_only_after_yes(self):
        panel = ControlPanel.__new__(ControlPanel)
        panel.root = MagicMock()
        panel._send = MagicMock(return_value=True)
        with patch.object(panel_module.messagebox, "askyesno", return_value=False) as ask:
            panel._reset_touch_calibration()
            ask.assert_called_once()
            panel._send.assert_not_called()
        with patch.object(panel_module.messagebox, "askyesno", return_value=True):
            panel._reset_touch_calibration()
        panel._send.assert_called_once_with({"type": "touch_calibration_reset"})

    def test_panel_no_longer_accepts_free_calibration_points(self):
        with self.assertRaises(ValueError):
            validate_command({"type": "touch_calibration", "points": [[100, 525], [700, 75]]})
        with self.assertRaises(ValueError):
            validate_command({"type": "touch_calibration_reset", "points": None})
        self.assertEqual(validate_command({"type": "touch_calibration_reset"}),
                         {"type": "touch_calibration_reset"})

    def test_preview_captures_cabinet_lower_device_not_boot_primary(self) -> None:
        stream = MagicMock()
        stream.readline.side_effect = [b'{"QMP": {}}\n', b'{"return": {}}\n', b'{"return": {}}\n']
        with patch("cabinet_control_panel.socket.create_connection") as connect:
            connect.return_value.__enter__.return_value.makefile.return_value = stream
            qmp_screendump(4444, Path("preview.png"))
        commands = [json.loads(call.args[0]) for call in stream.write.call_args_list]
        self.assertEqual(commands[-1]["arguments"]["device"], "lower")


class MemoryRsp:
    def __init__(self) -> None:
        self.data: dict[int, int] = {MP_STATE_ADDRESS: 0}
        mappings = {
            "menu": 0x1065, "autostart": 0x1005, "einsatz": 0x1067,
            "maxeinsatz": 0x1006, "start": 0x1026,
            "auszahlung": 0x1023, "automatik": 0x1022, "service": 0x1025,
        }
        for name, mapping in mappings.items():
            self.write_memory(KEY_TABLE_BASE + KEY_IDS[name] * 2,
                              mapping.to_bytes(2, "big"))

    def read_memory(self, address: int, size: int) -> bytes:
        return bytes(self.data.get(address + i, 0) for i in range(size))

    def write_memory(self, address: int, value: bytes) -> None:
        for i, byte in enumerate(value):
            self.data[address + i] = byte


class CabinetControlTests(unittest.TestCase):
    def test_family_layouts_keep_m88_m90_and_add_separate_m202_transfer(self):
        self.assertEqual(KEY_LAYOUTS['m88_m90'], KEY_IDS)
        self.assertEqual(KEY_IDS['einsatz'], 4)
        keys = KEY_LAYOUTS['m202']
        self.assertEqual(keys['einsatz'], 13)
        self.assertEqual(keys['level'], 4)
        self.assertEqual(keys['maxlevel'], 5)
        for layout, names in KEY_LAYOUTS.items():
            buttons = panel_module.BUTTON_LAYOUTS[layout]
            self.assertEqual({name for _, name in buttons}, set(names))
            cells = [panel_module.button_cell(n) for n in range(len(buttons) + 1)]
            self.assertEqual(len(cells), len(set(cells)))

    def test_m202_transfer_and_level_switches_are_independent(self):
        rsp = MemoryRsp()
        keys = KEY_LAYOUTS['m202']
        for name, mapping in {'level': 0x1067, 'maxlevel': 0x1006, 'einsatz': 0x1004}.items():
            rsp.write_memory(KEY_TABLE_BASE + keys[name] * 2, mapping.to_bytes(2, 'big'))
        pending = {'einsatz'}
        mappings = publish_cabinet_buttons(rsp, {'einsatz'}, pending, set(), key_ids=keys,
                                            layout='m202')
        transfer, transfer_event, mask = key_location(mappings['einsatz'])
        level, level_event, level_mask = key_location(mappings['level'])
        self.assertNotEqual(transfer, level)
        self.assertEqual(rsp.data[transfer] & mask, 0)
        self.assertEqual(rsp.data[transfer_event] & mask, mask)
        self.assertEqual(rsp.data[level] & level_mask, level_mask)
        self.assertEqual(rsp.data.get(level_event, 0) & level_mask, 0)
        self.assertEqual(pending, set())
        publish_cabinet_buttons(rsp, set(), set(), {'einsatz'}, key_ids=keys, layout='m202')
        self.assertEqual(rsp.data[transfer] & mask, mask)

    def test_command_names_are_validated_against_the_active_family(self):
        event = {'type': 'button', 'name': 'level', 'down': True}
        self.assertEqual(validate_command(event, key_ids=KEY_LAYOUTS['m202']), event)
        with self.assertRaises(ValueError):
            validate_command(event, key_ids=KEY_IDS)
        event['name'] = 'maxeinsatz'
        self.assertEqual(validate_command(event, key_ids=KEY_IDS), event)
        with self.assertRaises(ValueError):
            validate_command(event, key_ids=KEY_LAYOUTS['m202'])

    def test_m202_info_uses_rising_edge_bank_and_releases_without_phantom_edge(self):
        rsp = MemoryRsp()
        keys = KEY_LAYOUTS['m202']
        rsp.write_memory(KEY_TABLE_BASE + keys['info'] * 2, bytes.fromhex('90 22'))
        current = KEY_CURRENT_BASE + 0x22
        rsp.write_memory(current+8, b'\x10')
        rsp.write_memory(current+0x88, b'\x10')
        publish_cabinet_buttons(rsp, set(), set(), set(), key_ids=keys, layout='m202')
        self.assertEqual(rsp.read_memory(current, 1)[0] & 0x10, 0)
        self.assertEqual(rsp.read_memory(current+8, 1)[0] & 0x10, 0)
        self.assertEqual(rsp.read_memory(current+0x88, 1)[0] & 0x10, 0)
        publish_cabinet_buttons(rsp, {'info'}, {'info'}, set(), key_ids=keys, layout='m202')
        self.assertEqual(rsp.read_memory(current, 1)[0] & 0x10, 0x10)
        self.assertEqual(rsp.read_memory(current+8, 1)[0] & 0x10, 0x10)
        self.assertEqual(rsp.read_memory(current+0x88, 1)[0] & 0x10, 0)

    def test_batched_scan_preserves_unrelated_bytes_and_limits_debugger_trips(self) -> None:
        rsp = MemoryRsp()
        for base in (KEY_CURRENT_BASE, KEY_EVENT_BASE):
            rsp.write_memory(base, bytes([0xA5]) * 128)
        before = dict(rsp.data)
        cache = {}
        publish_cabinet_buttons(rsp, {"service"}, {"service"}, mapping_cache=cache)
        controlled = set()
        for name, mapping in cache[0].items():
            current, event, mask = key_location(mapping)
            controlled.update((current, event))
            self.assertEqual(rsp.data[current] & ~mask, before[current] & ~mask)
            self.assertEqual(rsp.data[event] & ~mask, before[event] & ~mask)
        for address in before.keys() - controlled:
            self.assertEqual(rsp.data[address], before[address])
        with patch.object(rsp, "read_memory", wraps=rsp.read_memory) as read, \
                patch.object(rsp, "write_memory", wraps=rsp.write_memory) as write:
            publish_cabinet_buttons(rsp, {"service"}, set(), mapping_cache=cache)
        self.assertEqual(read.call_count, 3)  # profile and two input banks
        self.assertLessEqual(write.call_count, 2)

    def test_every_panel_key_is_visible_in_its_own_cell(self) -> None:
        cells = [panel_module.button_cell(index) for index in range(len(panel_module.BUTTONS))]
        self.assertEqual(len(set(cells)), len(panel_module.BUTTONS))
        self.assertNotIn(panel_module.COIN_BUTTON_CELL, cells)   # the coin once hid the service key
        self.assertTrue(all(column < panel_module.BUTTON_COLUMNS for _, column in cells))
        self.assertEqual({name for _, name in panel_module.BUTTONS}, set(KEY_IDS))

    def test_key_only_some_cabinets_have_is_touched_only_while_used(self) -> None:
        for mapping, pressed_level, released_level in ((0x1022, 0x00, 0x10), (0x9022, 0x10, 0x00)):
            with self.subTest(mapping=f"{mapping:04X}"):
                rsp = MemoryRsp()
                rsp.write_memory(KEY_TABLE_BASE + KEY_IDS["automatik"] * 2, mapping.to_bytes(2, "big"))
                current, event, mask = key_location(mapping)
                self.assertEqual(mask, 0x10)
                # Unused: whatever the board delivers stays, set or clear.
                for delivered in (0x00, 0xFF):
                    rsp.write_memory(current, bytes([delivered]))
                    rsp.write_memory(event, bytes([delivered]))
                    publish_cabinet_buttons(rsp, set(), set(), set())
                    self.assertEqual((rsp.data[current], rsp.data[event]), (delivered, delivered))
                # Pressed: the level this cabinet reads as pressed, with one edge.
                rsp.write_memory(current, bytes([released_level]))
                rsp.write_memory(event, b"\x00")
                edges = {"automatik"}
                publish_cabinet_buttons(rsp, {"automatik"}, edges, set())
                self.assertEqual((rsp.data[current] & mask, rsp.data[event] & mask), (pressed_level, mask))
                self.assertEqual(edges, set())
                # Released: back to the other level once, then left alone again.
                publish_cabinet_buttons(rsp, set(), set(), {"automatik"})
                self.assertEqual((rsp.data[current] & mask, rsp.data[event] & mask), (released_level, 0))
        # A cabinet without the key: a press is dropped, nothing fails.
        rsp = MemoryRsp()
        rsp.write_memory(KEY_TABLE_BASE + KEY_IDS["automatik"] * 2, b"\x00\x00")
        edges = {"automatik"}
        publish_cabinet_buttons(rsp, {"automatik"}, edges, set())
        self.assertEqual(edges, set())
        with self.assertRaisesRegex(RuntimeError, "unsupported mapping"):
            rsp.write_memory(KEY_TABLE_BASE + KEY_IDS["start"] * 2, b"\x00\x00")
            publish_cabinet_buttons(rsp, {"start"}, {"start"}, set())

    def test_preview_touch_deduplicates_press_and_recovers_missed_release(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.photo = object()
        panel.pad_touch = None
        panel.qemu_touch = None
        panel._pad_point = lambda _event: (320, 240)
        events = []
        panel._send = events.append

        panel._touch_press(object())
        panel._touch_press(object())
        panel._release_pad_if_button_up(True)
        panel._release_pad_if_button_up(False)
        panel._touch_release(object())

        self.assertEqual(events, [
            {"type": "touch", "x": 320, "y": 240, "down": True},
            {"type": "touch", "x": 320, "y": 240, "down": False},
        ])

    def test_qemu_mouse_sends_one_contact_and_releases_outside_window(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.pad_touch = panel.qemu_touch = None
        panel.previous_left_down = False
        events = []
        panel._send = events.append
        panel._poll_qemu_touch(True, (100, 200), True)
        for _ in range(10):
            panel._poll_qemu_touch(True, (100, 200), True)
        panel._poll_qemu_touch(True, (120, 200), True)
        panel._poll_qemu_touch(True, None, True)
        panel._poll_qemu_touch(False, None, True)
        panel._poll_qemu_touch(False, None, True)
        self.assertEqual(events, [
            {"type": "touch", "x": 100, "y": 200, "down": True},
            {"type": "touch", "x": 120, "y": 200, "down": True},
            {"type": "touch", "x": 120, "y": 200, "down": False},
        ])

    def test_dragging_from_preview_to_qemu_does_not_start_second_contact(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.photo = object()
        panel.pad_touch = panel.qemu_touch = None
        panel.previous_left_down = False
        panel._pad_point = lambda _event: (320, 240)
        events = []
        panel._send = events.append
        panel._touch_press(object())
        panel._poll_qemu_touch(True, (100, 200), True)
        panel._release_pad_if_button_up(False)
        panel._poll_qemu_touch(False, None, True)
        self.assertEqual([event["down"] for event in events], [True, False])

    def test_disabling_qemu_mouse_releases_contact_once(self) -> None:
        panel = ControlPanel.__new__(ControlPanel)
        panel.pad_touch = panel.qemu_touch = None
        panel.previous_left_down = False
        events = []
        panel._send = events.append
        panel._poll_qemu_touch(True, (100, 200), True)
        panel._poll_qemu_touch(True, None, False)
        panel._poll_qemu_touch(True, (100, 200), True)
        panel._poll_qemu_touch(False, None, True)
        self.assertEqual([event["down"] for event in events], [True, False])

    def test_format_tablet_packet_has_status_and_7_bit_coordinates(self) -> None:
        self.assertEqual(format_tablet_packet(0, 0, True), b"\xC0\0\0\0\0")
        self.assertEqual(format_tablet_packet(799, 599, False),
                         b"\x80\x7f\x7f\x7f\x7f")
        self.assertEqual(format_tablet_packet(400, 300, True)[0], 0xC0)
        with self.assertRaises(ValueError):
            format_tablet_packet(800, 100, True)

    def test_command_validation_rejects_unbounded_or_invalid_input(self) -> None:
        self.assertEqual(validate_command({"type": "door", "open": True}),
                         {"type": "door", "open": True})
        for command in (
            {"type": "touch", "x": -1, "y": 0, "down": True},
            {"type": "touch", "x": 0, "y": 0, "down": 1},
            {"type": "button", "name": "unknown", "down": True},
            {"type": "door", "open": True, "extra": 1},
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_command(command)

    def test_original_key_location_resolves_current_and_edge(self) -> None:
        self.assertEqual(key_location(0x1023),
                         (KEY_CURRENT_BASE + 0x23, KEY_EVENT_BASE + 0x23, 0x10))
        # Bit 15 turns the level round (accessor at 0x6010C): the same switch,
        # pressed while its bit is set.
        self.assertEqual(key_location(0x9022),
                         (KEY_CURRENT_BASE + 0x22, KEY_EVENT_BASE + 0x22, 0x10))
        self.assertTrue(key_active_high(0x9022))
        self.assertFalse(key_active_high(0x1022))
        self.assertIsNone(key_location(0x0000))
        self.assertIsNone(key_location(0x1880))

    def test_buttons_press_edge_hold_and_release(self) -> None:
        rsp = MemoryRsp()
        self.assertEqual(publish_cabinet_buttons(rsp, set(), set(), set(KEY_IDS))["menu"],
                         0x1065)
        current, event, mask = key_location(0x1065)
        self.assertEqual(rsp.read_memory(current, 1), bytes([mask]))
        edges = {"menu"}
        publish_cabinet_buttons(rsp, {"menu"}, edges)
        self.assertEqual(edges, set())
        self.assertEqual(rsp.read_memory(current, 1), b"\0")
        self.assertEqual(rsp.read_memory(event, 1), bytes([mask]))
        rsp.write_memory(event, b"\0")  # firmware consumed the edge
        publish_cabinet_buttons(rsp, {"menu"}, edges)
        self.assertEqual(rsp.read_memory(event, 1), b"\0")
        publish_cabinet_buttons(rsp, set(), edges, {"menu"})
        self.assertEqual(rsp.read_memory(current, 1), bytes([mask]))

    def test_short_click_survives_multiple_timer_batches_then_releases(self) -> None:
        rsp = MemoryRsp()
        pressed = {"menu"}
        pending_edges = {"menu"}
        released: set[str] = set()
        release_after_scans = {"menu": BUTTON_PULSE_BOARD_SCANS}
        publish_cabinet_buttons(rsp, pressed, pending_edges, released)
        current, event, mask = key_location(0x1065)
        self.assertEqual(rsp.read_memory(current, 1), b"\0")
        self.assertEqual(rsp.read_memory(event, 1), bytes([mask]))
        self.assertEqual(pressed, {"menu"})
        for _ in range(BUTTON_PULSE_BOARD_SCANS - 1):
            self.assertEqual(
                advance_button_pulses(
                    pressed, pending_edges, released, release_after_scans,
                ), set(),
            )
            self.assertEqual(pressed, {"menu"})
        self.assertEqual(
            advance_button_pulses(
                pressed, pending_edges, released, release_after_scans,
            ), {"menu"},
        )
        self.assertEqual(pressed, set())
        self.assertEqual(released, {"menu"})
        self.assertEqual(release_after_scans, {})
        publish_cabinet_buttons(rsp, pressed, pending_edges, released)
        self.assertEqual(rsp.read_memory(current, 1), bytes([mask]))

    def test_floating_scanner_keeps_only_gui_pressed_key_active(self) -> None:
        rsp = MemoryRsp()
        for name, mapping in {
            "menu": 0x1065, "autostart": 0x1005, "einsatz": 0x1067,
            "maxeinsatz": 0x1006, "start": 0x1026,
            "auszahlung": 0x1023, "service": 0x1025,
        }.items():
            current, event, mask = key_location(mapping)
            rsp.write_memory(current, b"\0")
            rsp.write_memory(event, bytes([mask]))
        publish_cabinet_buttons(rsp, set(), set())
        for mapping in (0x1065, 0x1005, 0x1067, 0x1006, 0x1026,
                        0x1023, 0x1025):
            current, event, mask = key_location(mapping)
            self.assertEqual(rsp.read_memory(current, 1)[0] & mask, mask)
            self.assertEqual(rsp.read_memory(event, 1)[0] & mask, 0)

        current, event, mask = key_location(0x1065)
        edges = {"menu"}
        publish_cabinet_buttons(rsp, {"menu"}, edges)
        self.assertEqual(rsp.read_memory(current, 1)[0] & mask, 0)
        self.assertEqual(rsp.read_memory(event, 1), bytes([mask]))
        publish_cabinet_buttons(rsp, set(), edges, {"menu"})
        self.assertEqual(rsp.read_memory(current, 1)[0] & mask, mask)
        self.assertEqual(rsp.read_memory(event, 1)[0] & mask, 0)

    def test_loopback_control_server_accepts_valid_event(self) -> None:
        server = CabinetControlServer(0)
        try:
            port = server._socket.getsockname()[1]
            self.assertEqual(send_command({"type": "ping"}, port), {"ok": True})
            send_command({"type": "button", "name": "start", "down": True}, port)
            self.assertEqual(server.events.get(timeout=1.0),
                             {"type": "button", "name": "start", "down": True})
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
