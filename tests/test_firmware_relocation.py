"""The bridge's addresses are found in another build of the database program."""
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import firmware_relocation as relocation

BASE = relocation.LOAD_ADDRESS
RAM_A, RAM_BETWEEN, RAM_B = 0x001E2000, 0x001E2040, 0x001E2100
# offsets in the made-up build
EARLY, ADDED_AT, MIDDLE, LATE, CLOSE = 0x0500, 0x1000, 0x1200, 0x2000, 0x3000
SOURCE = (f"EARLY = 0x{BASE + EARLY:04X}\nMIDDLE = 0x{BASE + MIDDLE:08X}\nLATE = 0x{BASE + LATE:04X}\n"
          f"CLOSE = 0x{BASE + CLOSE:04X}\nRAM = (0x{RAM_A:08X}, 0x{RAM_BETWEEN:08X}, 0x{RAM_B:08X})\n"
          "NOT_AN_ADDRESS = 0x2000 + 0xFFFF  # and 0x58A9\n")


def verified_build() -> bytearray:
    runtime = bytearray(random.Random(7).randbytes(0x6000))
    for site in (0x0800, 0x2400, 0x4A00):
        struct.pack_into(">I", runtime, site, RAM_A)
    for site in (0x0900, 0x2600, 0x4C00):
        struct.pack_into(">I", runtime, site, RAM_B)
    return runtime


def other_build(runtime: bytes) -> bytes:
    """The same code: its RAM 0x40 lower, six bytes more at ADDED_AT, two more right behind CLOSE."""
    changed = bytearray(runtime)
    for site in (0x0800, 0x2400, 0x4A00):
        struct.pack_into(">I", changed, site, RAM_A - 0x40)
    for site in (0x0900, 0x2600, 0x4C00):
        struct.pack_into(">I", changed, site, RAM_B - 0x40)
    return bytes(changed[:ADDED_AT] + b"\x4e\x71" * 3 + changed[ADDED_AT:CLOSE + 12]
                 + b"\x4e\x71" + changed[CLOSE + 12:])


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for name in relocation.SOURCES:
            (self.directory / name).write_text(SOURCE if name == relocation.SOURCES[0] else "", encoding="utf-8")
        self.runtime = verified_build()
        self.table = relocation.build_table(bytes(self.runtime), self.directory)

    def test_table_names_every_address_and_no_other_number(self):
        self.assertEqual({int(text, 16) for text in self.table["addresses"]},
                         {BASE + EARLY, BASE + MIDDLE, BASE + LATE, BASE + CLOSE, RAM_A, RAM_BETWEEN, RAM_B})
        self.assertNotIn(self.runtime[EARLY:EARLY + 8].hex(), str(self.table))    # hashes, not firmware

    def test_verified_build_keeps_every_address(self):
        moved = relocation.resolve(self.table, bytes(self.runtime))
        self.assertTrue(all(old == new for old, new in moved.items()))
        self.assertEqual(len(moved), 7)

    def test_addresses_follow_the_code_of_another_build(self):
        moved = relocation.resolve(self.table, other_build(self.runtime))
        self.assertEqual(moved[BASE + EARLY], BASE + EARLY)                # in front of the added bytes
        self.assertEqual(moved[BASE + MIDDLE], BASE + MIDDLE + 6)
        self.assertEqual(moved[BASE + LATE], BASE + LATE + 6)
        # Bytes added right behind an address do not move it; the code after them is 8 further.
        self.assertEqual(moved[BASE + CLOSE], BASE + CLOSE + 6)
        # RAM: read from the instructions that use it; one without any lies between its neighbours.
        self.assertEqual((moved[RAM_A], moved[RAM_B]), (RAM_A - 0x40, RAM_B - 0x40))
        self.assertEqual(moved[RAM_BETWEEN], RAM_BETWEEN - 0x40)

    def test_unknown_program_stops_instead_of_guessing(self):
        with self.assertRaisesRegex(relocation.UnresolvedAddresses, "7 von 7 Adressen"):
            relocation.resolve(self.table, random.Random(99).randbytes(0x6000))
        # One address missing is enough.
        damaged = bytearray(self.runtime)
        damaged[MIDDLE - 200:MIDDLE + 200] = bytes(400)
        with self.assertRaisesRegex(relocation.UnresolvedAddresses, "1 von 7"):
            relocation.resolve(self.table, bytes(damaged))

    def test_source_gets_the_addresses_of_the_other_build_only(self):
        moved = relocation.resolve(self.table, other_build(self.runtime))
        text = relocation.relocated_source(SOURCE, moved)
        self.assertIn(f"MIDDLE = 0x{BASE + MIDDLE + 6:08X}", text)
        self.assertIn(f"0x{RAM_BETWEEN - 0x40:08X}", text)
        self.assertIn("NOT_AN_ADDRESS = 0x2000 + 0xFFFF  # and 0x58A9", text)
        self.assertEqual(relocation.relocated_source(SOURCE, {}), SOURCE)


class BridgeTableTests(unittest.TestCase):
    def test_table_belongs_to_the_verified_build_and_the_current_sources(self):
        import m68k_database_bridge as bridge
        table = relocation.load_table()
        self.assertEqual(table["runtime_sha256"], bridge.VERIFIED_RUNTIME_SHA256)
        # A new address constant needs a rebuilt table (firmware_relocation.py --build).
        self.assertEqual({int(text, 16) for text in table["addresses"]},
                         relocation.source_addresses(code_end=int(table["code_end"], 16)))
        for address in (bridge.AUX_RESPONSE_CHECK_PC, bridge.TIMER_HANDLER, bridge.AUX_TRANSACTION_DATA,
                        bridge.KEY_TABLE_BASE, bridge.KEY_CURRENT_BASE, *bridge.AUX_MEMORY_COMMANDS):
            self.assertIn(f"{address:08X}", table["addresses"])

    def test_bridge_is_loaded_with_the_addresses_of_the_other_build(self):
        import cabinet_controls
        import m68k_database_bridge as bridge
        moved = {bridge.AUX_RESPONSE_CHECK_PC: bridge.AUX_RESPONSE_CHECK_PC + 0x52C,
                 bridge.KEY_TABLE_BASE: bridge.KEY_TABLE_BASE - 0x42C,
                 bridge.KEY_CURRENT_BASE: bridge.KEY_CURRENT_BASE - 0x107C}
        other = relocation.relocated_bridge(moved)
        self.assertEqual(other.AUX_RESPONSE_CHECK_PC, bridge.AUX_RESPONSE_CHECK_PC + 0x52C)
        self.assertEqual(other.KEY_TABLE_BASE, bridge.KEY_TABLE_BASE - 0x42C)       # its own controls
        self.assertEqual(other.key_location(0x1023)[0], bridge.KEY_CURRENT_BASE - 0x107C + 0x23)
        self.assertEqual(other.AUX_PROFILE_RESPONSE_CHECK_PC, bridge.AUX_PROFILE_RESPONSE_CHECK_PC)
        self.assertIn(other.AUX_RESPONSE_CHECK_PC,
                      [address for *_, addresses, _ in other.DATABASE_HOOK_GROUPS for address in addresses])
        # The verified bridge and the controls every other part imports stay as they are.
        self.assertIs(sys.modules["cabinet_controls"], cabinet_controls)
        self.assertEqual(cabinet_controls.KEY_TABLE_BASE, bridge.KEY_TABLE_BASE)
        self.assertTrue(callable(other.run_bridge))


if __name__ == "__main__":
    unittest.main()
