"""The loader's device routine is patched wherever it is found, never by file version."""
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import guest_files
import patch_loader_null_device as null
import unittest.mock

VERIFIED_ORIGINALS = guest_files.ORIGINALS
VERIFIED_GAME_HASH = guest_files.GAME_HASH
ROUTINE = (null.ROUTINE_HEAD + null.PATCH_EXPECTED + null.ROUTINE_CALL + bytes.fromhex("04 92 42 00")
           + null.ROUTINE_CHECK + bytes.fromhex("10 8b 4e 28 51 e8 90 03 00 00 83 c4 04") + null.ROUTINE_FAILURE)


def loader(routine_at=0x1430, *, virtual_size=0x284E, raw_size=0x3000, routine=ROUTINE):
    """A minimal executable: headers, one code section at file offset 0x1000."""
    data = bytearray(0x1000 + raw_size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x14C, 1)
    struct.pack_into("<H", data, 0x80 + 20, 0xE0)
    struct.pack_into("<I", data, 0x80 + 24 + 28, 0x400000)
    struct.pack_into("<8sIIII", data, 0x80 + 24 + 0xE0, b".text", virtual_size, 0x1000, raw_size, 0x1000)
    data[0x1000:0x1000 + virtual_size] = b"\x90" * virtual_size
    data[routine_at:routine_at + len(routine)] = routine
    return bytes(data)


class LoaderNullDeviceTests(unittest.TestCase):
    def test_routine_is_patched_wherever_this_build_keeps_it(self):
        for routine_at in (0x1430, 0x2011):
            with self.subTest(routine_at=routine_at):
                source = loader(routine_at)
                output = null.patched(source)
                site = routine_at + len(null.ROUTINE_HEAD)
                cave = 0x1000 + 0x2850                      # behind the code, aligned to 16
                self.assertEqual(output[site], 0xE9)
                self.assertEqual(site + 5 + struct.unpack_from("<i", output, site + 1)[0], cave)
                self.assertEqual(output[site + 5:site + 7], b"\x90\x90")
                code = null.trampoline(site, cave)
                self.assertEqual(output[cave:cave + len(code)], code)
                # The trampoline returns to the instruction behind the replaced ones.
                back = cave + 11
                self.assertEqual(back + 5 + struct.unpack_from("<i", code, 12)[0], site + 7)
                # Only the site, the trampoline and the section's virtual size change.
                changed = {i for i in range(len(source)) if source[i] != output[i]}
                allowed = set(range(site, site + 7)) | set(range(cave, cave + len(code))) \
                    | set(range(0x80 + 24 + 0xE0 + 8, 0x80 + 24 + 0xE0 + 12))
                self.assertLessEqual(changed, allowed)
                self.assertEqual(struct.unpack_from("<I", output, 0x80 + 24 + 0xE0 + 8)[0], 0x3000)
                self.assertEqual(null.trampoline_offset(output), cave)
                self.assertIsNone(null.trampoline_offset(source))
                self.assertEqual(null.patched(output), output)  # already patched: nothing left to find

    def test_frame_pointer_build_of_the_routine_is_patched_at_every_copy(self):
        # Another loader version: the pointer is loaded after the arguments,
        # once for each of two members.
        def routine(member):
            return (null.FRAME_HEAD + null.FRAME_EXPECTED + null.FRAME_LOAD + bytes([member]) + null.FRAME_CALL
                    + bytes.fromhex("34 e2 42 00 8b 55 fc 89 02 8b e5 5d c3"))
        first, second = 0x1430, 0x1480
        source = bytearray(loader(first, routine=routine(0x28)))
        source[second:second + len(routine(0x2C))] = routine(0x2C)
        source = bytes(source)
        self.assertIsNone(null.plan(source))
        output = null.patched(source)
        cave = 0x1000 + 0x2850 + null.FRAME_CAVE
        changed = {i for i in range(len(source)) if source[i] != output[i]}
        allowed = set(range(0x80 + 24 + 0xE0 + 8, 0x80 + 24 + 0xE0 + 12))
        for index, (start, member) in enumerate(((first, 0x28), (second, 0x2C))):
            site = start + len(null.FRAME_HEAD)
            target = cave + index * null.FRAME_CAVE_STEP
            self.assertEqual(output[site], 0xE9)
            self.assertEqual(site + 5 + struct.unpack_from("<i", output, site + 1)[0], target)
            code = null.frame_trampoline(site, member, target)
            self.assertEqual(output[target:target + len(code)], code)
            # A null pointer leaves through the frame; a set one pushes the
            # replaced argument and continues behind the jump.
            self.assertEqual(code[:9], bytes.fromhex("8b 45 fc 83 78") + bytes([member]) + bytes.fromhex("00 75 06"))
            self.assertEqual(code[9:15], bytes.fromhex("33 c0 8b e5 5d c3"))
            self.assertEqual(code[15:20], null.FRAME_EXPECTED)
            self.assertEqual(target + 25 + struct.unpack_from("<i", code, 21)[0], site + 5)
            allowed |= set(range(site, site + 5)) | set(range(target, target + len(code)))
        self.assertLessEqual(changed, allowed)
        self.assertEqual(struct.unpack_from("<I", output, 0x80 + 24 + 0xE0 + 8)[0], 0x3000)
        self.assertEqual(null.patched(output), output)

    def test_loader_without_the_routine_stays_unchanged(self):
        other = loader(routine=b"\x90" * len(ROUTINE))
        self.assertEqual(null.patched(other), other)
        self.assertEqual(null.patched(b"not an executable"), b"not an executable")
        # The same instructions with another failure exit are not this routine.
        different_exit = ROUTINE.replace(null.ROUTINE_FAILURE, bytes.fromhex("33 c0 5e c2"))
        changed = loader(routine=different_exit)
        self.assertEqual(null.patched(changed), changed)
        # Two candidates cannot be told apart.
        twice = bytearray(loader())
        twice[0x2100:0x2100 + len(ROUTINE)] = ROUTINE
        self.assertEqual(null.patched(bytes(twice)), bytes(twice))

    def test_used_or_missing_padding_is_never_overwritten(self):
        used = bytearray(loader())
        used[0x1000 + 0x2900] = 0x55                        # something lives behind the code
        self.assertEqual(null.patched(bytes(used)), bytes(used))
        full = loader(virtual_size=0x2FF0)                  # no room for the trampolines
        self.assertEqual(null.patched(full), full)

    def test_verified_loader_must_give_its_verified_result(self):
        source = loader()
        with unittest.mock.patch.object(null, "ORIGINAL_SHA256", null.digest(source)):
            with self.assertRaisesRegex(ValueError, "verified form"):
                null.patched(source)
            without = loader(routine=b"\x90" * len(ROUTINE))
            with unittest.mock.patch.object(null, "ORIGINAL_SHA256", null.digest(without)):
                with self.assertRaisesRegex(ValueError, "verified loader"):
                    null.patched(without)

    def test_command_line_writes_and_verifies_the_patched_form(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "explorer_original.exe", Path(directory) / "patched.exe"
            source.write_bytes(loader())
            run = lambda *arguments: subprocess.run(
                [sys.executable, str(SCRIPTS / "patch_loader_null_device.py"), *arguments],
                capture_output=True, text=True)
            written = run(str(source), str(target))
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertIn("nicht verifiziert", written.stdout)
            self.assertEqual(target.read_bytes(), null.patched(source.read_bytes()))
            self.assertEqual(run("--verify", str(source), str(target)).returncode, 0)
            target.write_bytes(source.read_bytes())
            self.assertNotEqual(run("--verify", str(source), str(target)).returncode, 0)
            # A loader without the routine: copied unchanged, and that is its verified form.
            source.write_bytes(loader(routine=b"\x90" * len(ROUTINE)))
            written = run(str(source), str(target))
            self.assertIn("bleibt unverändert", written.stdout)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(run("--verify", str(source), str(target)).returncode, 0)


class GuestFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.originals = tuple((relative, guest_files.hashlib.sha256(relative.encode()).hexdigest(), label)
                               for relative, _, label in guest_files.ORIGINALS)
        game = guest_files.hashlib.sha256(b"verified games").hexdigest()
        for name, value in (("ORIGINALS", self.originals), ("GAME_HASH", game)):
            patcher = unittest.mock.patch.object(guest_files, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        for relative, _, _ in self.originals:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
        for directory in guest_files.GAME_DIRECTORIES:
            (self.root / directory).mkdir(exist_ok=True)
            (self.root / directory / "game.exe").write_bytes(b"verified games")

    def test_verified_card_needs_no_note(self):
        self.assertEqual(guest_files.original_notes(self.root), [])

    def test_other_versions_are_named_once_each(self):
        (self.root / "NVRAM/game.exe").write_bytes(b"other games")
        (self.root / "WorkDir/game.exe").write_bytes(b"other games")
        (self.root / "WINDOWS/explorer.exe").write_bytes(b"other loader")
        notes = guest_files.original_notes(self.root)
        self.assertEqual(len(notes), 2)
        self.assertIn("Loader (explorer.exe)", notes[0])
        self.assertIn("Spielprogramm (game.exe)", notes[1])

    def test_missing_file_means_it_is_not_such_an_image(self):
        (self.root / "WINDOWS/system32/Cgos.dll").unlink()
        with self.assertRaisesRegex(ValueError, "missing file"):
            guest_files.original_notes(self.root)

    def test_game_program_may_be_in_one_folder_only(self):
        (self.root / "NVRAM/game.exe").unlink()
        self.assertEqual(guest_files.original_notes(self.root), [])
        self.assertEqual(guest_files.game_programs(self.root), [self.root / "WorkDir/game.exe"])
        (self.root / "WorkDir/game.exe").unlink()
        with self.assertRaisesRegex(ValueError, "missing file"):
            guest_files.original_notes(self.root)

    def test_check_list_of_a_changed_card_follows_what_the_game_computed(self):
        listed = b"CheckSumme Datei \\A\\*: 215450391\r\nCheckSumme Game.exe: 404314034\r\nGesamtCheckSumme: 2750957944\r\n"
        computed = b"CheckSumme Datei \\A\\*: 57914216\r\nCheckSumme Game.exe: 404314034\r\nGesamtCheckSumme: 3050358922\r\n"
        card, game = self.root / guest_files.CHECK_LIST, self.root / guest_files.CHECK_COMPUTED
        self.assertFalse(guest_files.check_list_pending(self.root))          # a card without lists
        card.write_bytes(listed)
        game.write_bytes(listed)
        self.assertFalse(guest_files.check_list_pending(self.root))          # the card as it was made
        # Not a complete list of the same entries: the card's list stays.
        for other in (computed[:40], computed.replace(b"\\A\\", b"\\B\\"), computed.rsplit(b"\r\n", 2)[0] + b"\r\n",
                      b"CheckSumme Datei \\A\\*: x\r\nGesamtCheckSumme: 1\r\n"):
            game.write_bytes(other)
            self.assertFalse(guest_files.check_list_pending(self.root), other)
        game.write_bytes(computed)
        self.assertTrue(guest_files.check_list_pending(self.root))
        guest_files.adapt_check_list(self.root)
        self.assertEqual(card.read_bytes(), computed)
        self.assertEqual(card.with_name(card.name + ".pre-m90").read_bytes(), listed)
        self.assertFalse(guest_files.check_list_pending(self.root))
        # A later change keeps the card's first list as the saved one.
        game.write_bytes(computed.replace(b"57914216", b"57914217"))
        guest_files.adapt_check_list(self.root)
        self.assertEqual(card.with_name(card.name + ".pre-m90").read_bytes(), listed)

    def test_verified_hashes_are_the_ones_the_updates_use(self):
        import graphics_update
        verified = {relative: value for relative, value, _ in VERIFIED_ORIGINALS}
        self.assertEqual(VERIFIED_GAME_HASH, graphics_update.GAME_HASH)
        self.assertEqual(verified["WINDOWS/explorer.exe"], null.ORIGINAL_SHA256)


if __name__ == "__main__":
    unittest.main()
