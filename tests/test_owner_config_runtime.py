import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from m68k_database_transform import transform_database
from owner_config_runtime import (
    CONFIG_CLEAR_START, CONFIG_COPY_START, CONFIG_ENTRY_PREFIX,
    CONFIG_IDENTITY_OPERAND, CONFIG_TABLE_OPERAND, MOVEA_A3,
    IDENTITY_COPY_START, prepare_config_writes,
    FACTORY_ENTRY_CODE, prepare_factory_runtime,
)


class OwnerConfigRuntimeTests(unittest.TestCase):
    def factory_image(self, *, corrupt_code=False):
        decoded = bytearray(0x500 + len(FACTORY_ENTRY_CODE))
        end = 0x1000 + len(decoded) - 1
        struct.pack_into(">III", decoded, 4, end, ~end & 0xFFFFFFFF, 0x61640403)
        struct.pack_into(">II", decoded, 0x4C, 0x1500, ~0x1500 & 0xFFFFFFFF)
        decoded[0x500:] = FACTORY_ENTRY_CODE
        if corrupt_code:
            decoded[-2] ^= 1
        struct.pack_into(">I", decoded, 0, sum(decoded[4:]) & 0xFFFFFFFF)
        return transform_database(bytes(decoded), 0xD27B7159), bytes(decoded)

    def test_factory_upload_header_accepts_zero_exclusive_end(self):
        raw, expected = self.factory_image()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "factory.xc"
            path.write_bytes(raw)
            runtime, entry = prepare_factory_runtime(path, hashlib.sha256(raw).hexdigest(), 0xD27B7159)
        self.assertEqual(runtime, expected)
        self.assertEqual(entry, 0x1500)

    def test_factory_with_changed_executable_code_is_rejected(self):
        raw, _ = self.factory_image(corrupt_code=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "factory.xc"
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "verified native reset code"):
                prepare_factory_runtime(path, hashlib.sha256(raw).hexdigest(), 0xD27B7159)

    def test_factory_requires_matching_selected_file_hash(self):
        raw, _ = self.factory_image()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "factory.xc"
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                prepare_factory_runtime(path, "0" * 64, 0xD27B7159)

    def config_image(self, *, size=1920, identity_at=0x562, family=0x61640403):
        decoded = bytearray(size)
        struct.pack_into(">I", decoded, 4, 0x1000 + len(decoded) - 1)
        struct.pack_into(">I", decoded, 12, family)
        struct.pack_into(">I", decoded, 0x4C, 0x1500)
        decoded[0x500:0x508] = CONFIG_ENTRY_PREFIX
        # MOVEA.L #source,A3 in front of each of the entry's two copy loops.
        decoded[CONFIG_TABLE_OPERAND - 2:CONFIG_TABLE_OPERAND + 4] = MOVEA_A3 + struct.pack(">I", 0x1578)
        decoded[CONFIG_IDENTITY_OPERAND - 2:CONFIG_IDENTITY_OPERAND + 4] = (
            MOVEA_A3 + struct.pack(">I", 0x1000 + identity_at))
        decoded[identity_at:identity_at + 0x18] = b"A" * 0x18
        decoded[0x578:0x778] = bytes(range(256)) * 2      # the 2-MB module's table begins inside its identity
        struct.pack_into(">I", decoded, 0, sum(decoded[4:]) & 0xFFFFFFFF)
        return decoded

    def test_module_may_keep_its_identity_apart_from_the_table(self) -> None:
        # The 4-MB program reads all 24 identity bytes; they must not share two with the table.
        decoded = self.config_image(size=0x790, identity_at=0x778, family=0x61640603)
        raw = transform_database(bytes(decoded), 0xC4D7EA55)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.bin"
            path.write_bytes(raw)
            writes, _ = prepare_config_writes(path, hashlib.sha256(raw).hexdigest(), 0xC4D7EA55)
            self.assertEqual([address for address, _ in writes], [0x3FFC00, 0x3FFC00, 0x3FFF80])
            self.assertEqual(writes[1][1], bytes(range(256)) * 2)
            self.assertEqual(writes[2][1], b"A" * 0x18)
            # A source beyond the module is refused.
            struct.pack_into(">I", decoded, CONFIG_IDENTITY_OPERAND, 0x1000 + 0x780)
            struct.pack_into(">I", decoded, 0, 0)
            struct.pack_into(">I", decoded, 0, sum(decoded[4:]) & 0xFFFFFFFF)
            raw = transform_database(bytes(decoded), 0xC4D7EA55)
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "shorter than its RAM-copy source"):
                prepare_config_writes(path, hashlib.sha256(raw).hexdigest(), 0xC4D7EA55)

    def test_verified_module_copies_only_its_two_proven_sram_ranges(self) -> None:
        decoded = self.config_image()
        raw = transform_database(bytes(decoded), 0xD27B7159)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.bin"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            writes, actual = prepare_config_writes(path, digest, 0xD27B7159)
        self.assertEqual(actual, digest.upper())
        self.assertEqual([address for address, _ in writes], [
            CONFIG_CLEAR_START, CONFIG_COPY_START, IDENTITY_COPY_START
        ])
        self.assertEqual(writes[0][1], bytes(0x400))
        self.assertEqual(writes[1][1], bytes(range(256)) * 2)
        self.assertEqual(writes[2][1], bytes(decoded[0x562:0x57A]))

    def test_wrong_config_hash_is_rejected_before_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.bin"
            path.write_bytes(bytes(1920))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                prepare_config_writes(path, "0" * 64, 0xD27B7159)


if __name__ == "__main__":
    unittest.main()
