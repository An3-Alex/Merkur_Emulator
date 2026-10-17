"""Board detection and cross-family refusal, without a QEMU process."""
import hashlib
import datetime
import contextlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from database_profile import PROFILES, profile_for_database, profile_for_loader
from database_key import KeyCache, resolve_key
from m68k_database_transform import transform_database
from owner_database_runtime import prepare_runtime
from owner_config_runtime import prepare_config_writes
from serialloader_chip_emulator import VirtualDatabaseChip, ChipPhase


def runtime_for(family, size=0x900):
    profile = PROFILES[family]
    data = bytearray((n * 17 + 3) & 255 for n in range(size))
    end = 0x1000 + size - 1
    struct.pack_into('>5I', data, 0, 0, end, end ^ 0xffffffff, family | 3,
                     end + profile.end_pointer_delta)
    struct.pack_into('>2I', data, 0x4c, 0x1500, 0x1500 ^ 0xffffffff)
    struct.pack_into('>2I', data, 0x100, 0x80000, 0x1500)
    struct.pack_into('>I', data, 0, sum(data[4:]) & 0xffffffff)
    return bytes(data)


def loader_for(family):
    profile = PROFILES[family]
    data = bytearray(3008)
    data[:5] = b'|load'
    data[0xe:0x10] = b'\x4e\xf9'
    struct.pack_into('>I', data, 0x10, profile.loader_entry)
    data[0x100:0x106] = b'\xb0\xbc' + struct.pack('>I', family)
    data[0x120:0x128] = b'\x0c\xb8' + struct.pack('>I', profile.ram_size) + b'\x00\x00'
    return bytes(data)


class DatabaseProfileTests(unittest.TestCase):
    def test_ram_is_selected_by_family_even_when_both_files_fit_2mb(self):
        for family, expected in ((0x61640400, 2), (0x61640600, 4)):
            self.assertEqual(profile_for_database(runtime_for(family)).ram_mib, expected)
            self.assertEqual(profile_for_loader(loader_for(family)).ram_mib, expected)

    def test_unknown_family_and_oversized_2mb_image_are_rejected(self):
        data = bytearray(runtime_for(0x61640400))
        struct.pack_into('>I', data, 0xc, 0x61649903)
        with self.assertRaisesRegex(ValueError, 'Unbekannte Datenbankfamilie'):
            profile_for_database(data)
        with self.assertRaisesRegex(ValueError, 'RAM-Größe'):
            profile_for_database(runtime_for(0x61640400, 0x210000))

    def test_family_specific_end_pointer_is_enforced(self):
        data = bytearray(runtime_for(0x61640600))
        struct.pack_into('>I', data, 0x10, 0x1000 + len(data))
        with self.assertRaisesRegex(ValueError, 'Kopfzeile'):
            profile_for_database(data)

    def test_automatic_key_and_runtime_work_for_both_boards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family, profile in PROFILES.items():
                expected = runtime_for(family)
                path = root / f'{family:x}.bin'
                path.write_bytes(transform_database(expected, profile.default_key))
                key = resolve_key(path, 'auto', KeyCache(root / 'keys.json'),
                                  root / 'missing.exe', lambda _: None, lambda _: None)
                actual, report = prepare_runtime(path, hashlib.sha256(path.read_bytes()).hexdigest(), d3=key)
                self.assertEqual(actual, expected)
                self.assertEqual(report['ram_mib'], profile.ram_mib)
                self.assertEqual(report['ram_size'], profile.ram_size)

    def test_wrong_loader_family_is_rejected_before_chip_is_programmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loader = root / 'loader.bin'
            loader.write_bytes(loader_for(0x61640400))
            chip = VirtualDatabaseChip()
            chip.upload_loader(loader, hashlib.sha256(loader.read_bytes()).hexdigest())
            chip.phase = ChipPhase.DATE_SET
            runtime = runtime_for(0x61640600)
            path = root / 'db.bin'
            path.write_bytes(transform_database(runtime, PROFILES[0x61640600].default_key))
            with self.assertRaisesRegex(ValueError, 'unterschiedliche RAM'):
                chip.upload_database(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                     d3=PROFILES[0x61640600].default_key)
            self.assertEqual(chip.phase, ChipPhase.DATE_SET)
            self.assertIsNone(chip.runtime)

    def test_database_without_factory_or_config_is_programmed_for_either_board(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family, profile in PROFILES.items():
                loader = root / 'loader.bin'
                loader.write_bytes(loader_for(family))
                database = root / 'db.bin'
                database.write_bytes(transform_database(runtime_for(family), profile.default_key))
                chip = VirtualDatabaseChip()
                chip.upload_loader(loader, hashlib.sha256(loader.read_bytes()).hexdigest())
                chip.set_date(datetime.datetime(2012, 2, 1), without_factory=True)
                chip.upload_database(database, hashlib.sha256(database.read_bytes()).hexdigest(),
                                     d3=profile.default_key)
                self.assertEqual(chip.phase, ChipPhase.PROGRAMMED)
                self.assertEqual(chip.manifest()['ram_mib'], profile.ram_mib)

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_start_scripts_detect_both_boards_and_refuse_foreign_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family, profile in PROFILES.items():
                database = root / 'db.bin'
                database.write_bytes(transform_database(runtime_for(family), profile.default_key))
                loader = root / 'loader.bin'
                loader.write_bytes(loader_for(family))
                for script in ('start-real-database.ps1', 'program-and-start-emulator.ps1'):
                    command = ['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(ROOT / script), '-Python', sys.executable,
                               '-Database', str(database), '-Loader', str(loader), '-DryRun']
                    result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    plan = json.loads(result.stdout)
                    bridge = plan.get('runtime', plan)['database_bridge']
                    self.assertEqual(bridge['board']['ram_mib'], profile.ram_mib)
                    visible = plan.get('runtime', plan)['visible_qemu']
                    controls = plan.get('runtime', plan)['control_window']
                    self.assertEqual(controls['key_layout'], profile.cabinet_controls)
                    if family == 0x61640600:
                        self.assertIn('pc,vmport=off', visible['arguments'])
                        self.assertNotIn('i8042=off', visible['arguments'])
                        self.assertNotIn('usb-tablet', visible['arguments'])
                        self.assertEqual(visible['guest_pointer'], 'Windows mouse disabled (virtual cabinet touch only)')
                        self.assertIn('level', controls['buttons'])
                        self.assertIn('einsatz', controls['buttons'])
                    else:
                        self.assertNotIn('i8042=off', visible['arguments'])
                        self.assertEqual(visible['guest_pointer'], 'PS/2 mouse')
                        self.assertIn('maxeinsatz', controls['buttons'])
                    arguments = bridge['arguments']
                    self.assertEqual(arguments[arguments.index('--d3') + 1], f'0x{profile.default_key:08X}')
                    self.assertNotIn('--factory', arguments)
                    foreign = root / 'foreign.bin'
                    foreign.write_bytes(loader_for(0x61640600 if family == 0x61640400 else 0x61640400))
                    command[command.index('-Loader') + 1] = str(foreign)
                    result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('kein Emulator gestartet', result.stderr)

    def _bridge_args(self, root, family, profile):
        loader = root / 'loader.bin'
        loader.write_bytes(loader_for(family))
        path = root / 'db.bin'
        path.write_bytes(transform_database(runtime_for(family), profile.default_key))
        return SimpleNamespace(timer_interval=0.01, admission_eeprom=None, duart_x1_hz=3686400,
                               door_open=False, loader=loader, database=path,
                               expected_loader_sha256=hashlib.sha256(loader.read_bytes()).hexdigest(),
                               expected_database_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                               d3=profile.default_key, runtime_dump=None, config=None, factory=None,
                               qemu=Path('never-started.exe'), fast_tb=False, icount_shift=6,
                               gdb_port=1235, control_port=4554)

    def test_unknown_build_starts_no_database_process(self):
        import m68k_database_bridge as bridge
        from firmware_relocation import UnresolvedAddresses
        with tempfile.TemporaryDirectory() as directory:
            for family, profile in PROFILES.items():
                args = self._bridge_args(Path(directory), family, profile)
                with patch('firmware_relocation.resolve', side_effect=UnresolvedAddresses('unknown build')), \
                     patch.object(bridge.subprocess, 'Popen') as process, \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(UnresolvedAddresses):
                        bridge.run_bridge(args)
                process.assert_not_called()

    def test_database_ram_is_bootstrapped_for_the_detected_board(self):
        import m68k_database_bridge as bridge
        class BootstrapComplete(Exception):
            pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family, profile in PROFILES.items():
                args = self._bridge_args(root, family, profile)
                rsp = Mock()
                socket_context = Mock()
                socket_context.__enter__ = Mock(return_value=Mock())
                socket_context.__exit__ = Mock(return_value=False)
                output = io.StringIO()
                # The synthetic image stands for the verified build here.
                with patch.object(bridge, 'database_hook_mismatches', return_value=[]), \
                     patch.object(bridge.subprocess, 'Popen', return_value=Mock()) as process, \
                     patch.object(bridge, 'CabinetControlServer'), \
                     patch.object(bridge, 'connect_rsp', return_value=socket_context), \
                     patch.object(bridge, 'RspClient', return_value=rsp), \
                     patch.object(bridge, 'load_memory'), \
                     patch.object(bridge, 'set_watchpoint', side_effect=BootstrapComplete), \
                     patch.object(bridge, 'stop_database_process'), contextlib.redirect_stdout(output):
                    with self.assertRaises(BootstrapComplete):
                        bridge.run_bridge(args)
                process.assert_called_once()
                rsp.write_memory.assert_any_call(0, profile.ram_size.to_bytes(4, 'big'))
                rsp.write_register_u32.assert_any_call(bridge.REG_A7, profile.ram_size - 0x80)
                self.assertIn(f'ram_mib={profile.ram_mib}', output.getvalue())

    def test_gui_reports_ram_and_refuses_unknown_build(self):
        from emulator_launcher import Launcher
        from portable_launcher_model import Selection
        from firmware_relocation import UnresolvedAddresses
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family, profile in PROFILES.items():
                path = root / 'db.bin'
                path.write_bytes(transform_database(runtime_for(family), profile.default_key))
                loader = root / 'loader.bin'
                loader.write_bytes(loader_for(family))
                launcher = SimpleNamespace(variables={'database': Mock()}, database_board=Mock(), events=Mock())
                launcher.variables['database'].get.return_value = str(path)
                Launcher._database_changed(launcher)
                launcher.database_board.set.assert_called_with(f'Datenbank-RAM automatisch erkannt: {profile.ram_mib} MB')
                selection = Selection(database=str(path), loader=str(loader), db_key=f'{profile.default_key:08X}')
                with patch('firmware_relocation.resolve', side_effect=UnresolvedAddresses('unknown build')):
                    with self.assertRaises(UnresolvedAddresses):
                        Launcher._report_database_version(launcher, selection)
                self.assertIn(f'{profile.ram_mib} MB RAM', launcher.events.put.call_args.args[0][1])

    def test_clock_before_the_database_minimum_year_is_refused(self):
        from dataclasses import replace
        from emulator_launcher import Launcher
        from portable_launcher_model import Selection
        from database_profile import minimum_clock_year
        family = 0x61640600
        profile = PROFILES[family]
        data = bytearray(runtime_for(family))
        self.assertIsNone(minimum_clock_year(bytes(data)))         # no digits in this head
        data[0x8C:0x90] = b'1501'
        struct.pack_into('>I', data, 0, 0)
        struct.pack_into('>I', data, 0, sum(data[4:]) & 0xffffffff)
        self.assertEqual(minimum_clock_year(bytes(data)), 2015)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'db.bin'
            path.write_bytes(transform_database(bytes(data), profile.default_key))
            loader = root / 'loader.bin'
            loader.write_bytes(loader_for(family))
            launcher = SimpleNamespace(events=Mock())
            early = Selection(database=str(path), loader=str(loader), db_key=f'{profile.default_key:08X}')
            with self.assertRaisesRegex(ValueError, 'Mindestjahr 2015'):
                Launcher._report_database_version(launcher, early)
            with patch('firmware_relocation.resolve', return_value={}):
                Launcher._report_database_version(launcher, replace(early, database_date='2020-06-15T12:00:00'))

    def test_config_writes_move_to_the_detected_ram_end(self):
        with tempfile.TemporaryDirectory() as directory:
            for family, profile in PROFILES.items():
                data = bytearray(runtime_for(family))
                struct.pack_into('>I', data, 0x10, 0)
                data[0x500:0x508] = bytes.fromhex('42b8100022780000')
                data[0x528:0x52E] = bytes.fromhex('267c00001578')   # the entry's two sources
                data[0x54A:0x550] = bytes.fromhex('267c00001562')
                struct.pack_into('>I', data, 0, sum(data[4:]) & 0xffffffff)
                path = Path(directory) / 'config.bin'
                path.write_bytes(transform_database(data, profile.default_key))
                writes, _ = prepare_config_writes(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                                  profile.default_key, profile=profile)
                self.assertEqual([address for address, _ in writes],
                                 [profile.ram_size - 0x400, profile.ram_size - 0x400, profile.ram_size - 0x80])
                other = PROFILES[0x61640600 if family == 0x61640400 else 0x61640400]
                with self.assertRaisesRegex(ValueError, 'unterschiedlichen RAM'):
                    prepare_config_writes(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                          profile.default_key, profile=other)

    @unittest.skipUnless((ROOT / 'build/recover-d3/recover_database_d3.exe').is_file(), 'native tool not built')
    def test_native_search_selects_both_cipher_variants_from_header(self):
        with tempfile.TemporaryDirectory() as directory:
            for family in PROFILES:
                path = Path(directory) / 'db.bin'
                path.write_bytes(transform_database(runtime_for(family), 0x12345678))
                result = subprocess.run([str(ROOT / 'build/recover-d3/recover_database_d3.exe'),
                                         str(path), '0x12345670', '16', '1'],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('D3_FOUND=0x12345678', result.stdout)


if __name__ == '__main__':
    unittest.main()
