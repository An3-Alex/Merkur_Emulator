"""Identify database RAM and loader layout from the clear module header."""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class DatabaseProfile:
    module_family: int
    ram_size: int
    end_pointer_delta: int
    loader_entry: int
    sync_kill_offset: int
    sync_wait_offset: int
    movec_offset: int
    default_key: int
    loader_idle_tx_stop_pc: int
    loader_idle_clear_stop_pc: int
    loader_idle_return_pc: int
    initvideo_frame_length: int   # the 4-MB board's start frame carries one more cabinet byte
    rtc_model: str = 'RTC4543'
    cabinet_controls: str = 'm88_m90'

    @property
    def ram_mib(self):
        return self.ram_size // (1024 * 1024)

    @property
    def config_clear_start(self):
        return self.ram_size - 0x400


PROFILES = {
    0x61640400: DatabaseProfile(0x61640400, 0x200000, 1, 0xCC8,
                              0x8B0, 0x8B8, 0x2D4, 0xD27B7159, 0xC76, 0xC88, 0xC8A, 39),
    0x61640600: DatabaseProfile(0x61640600, 0x400000, 5, 0xDBE,
                              0x8D0, 0x8D8, 0x298, 0xC4D7EA55, 0xC96, 0xCA8, 0xCAA, 40,
                              rtc_model='RV3049', cabinet_controls='m202'),
}


def profile_for_header(data: bytes) -> DatabaseProfile:
    if len(data) < 0x54:
        raise ValueError('Datenbank-Kopfzeile ist zu kurz')
    family = struct.unpack_from('>I', data, 0x0C)[0] & 0xFFFFFF00
    try:
        return PROFILES[family]
    except KeyError:
        raise ValueError(f'Unbekannte Datenbankfamilie {family:08X}; RAM-Größe nicht erkannt') from None


def minimum_clock_year(data: bytes) -> int | None:
    """The year the database program takes its clock to be valid from.

    Two digits in the clear head (0x8C); with an earlier clock the program
    stops with the clock fault F_UHR.
    """
    digits = data[0x8C:0x8E]
    return 2000 + int(digits) if len(digits) == 2 and digits.isdigit() else None


def profile_for_database(data: bytes) -> DatabaseProfile:
    profile = profile_for_header(data)
    checksum, end, inverse, module, pointer = struct.unpack_from('>5I', data)
    entry, entry_inverse = struct.unpack_from('>2I', data, 0x4C)
    if (len(data) < 0x100 or end != 0x1000 + len(data) - 1
            or inverse != (end ^ 0xFFFFFFFF)
            or pointer != end + profile.end_pointer_delta
            or entry_inverse != (entry ^ 0xFFFFFFFF)
            or not 0x1000 <= entry <= end
            or end >= profile.config_clear_start):
        raise ValueError('Datenbank-Kopfzeile passt nicht zur erkannten RAM-Größe')
    return profile


def profile_for_loader(data: bytes) -> DatabaseProfile:
    if not data.startswith(b'|load') or len(data) < 0x14 or data[0x0E:0x10] != b'\x4e\xf9':
        raise ValueError('Unbekannte Loader-Kopfzeile')
    entry = struct.unpack_from('>I', data, 0x10)[0]
    matches = [p for p in PROFILES.values() if entry == p.loader_entry
               and bytes.fromhex('b0bc') + struct.pack('>I', p.module_family) in data
               and bytes.fromhex('0cb8') + struct.pack('>I', p.ram_size) + b'\x00\x00' in data]
    if len(matches) != 1:
        raise ValueError('Loader-Familie und RAM-Prüfung nicht eindeutig erkannt')
    return matches[0]


def main():
    import argparse
    import hashlib
    import json
    from pathlib import Path
    from owner_database_runtime import prepare_runtime
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--loader', type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--d3', type=lambda value: int(value, 0))
    source.add_argument('--runtime-dump', type=Path)
    parser.add_argument('--header-only', action='store_true')
    args = parser.parse_args()
    data = args.database.read_bytes()
    profile = profile_for_database(data)
    if args.loader is not None and profile_for_loader(args.loader.read_bytes()) != profile:
        raise ValueError('Datenbank und Loader benötigen unterschiedliche RAM-Größen/Familien')
    d3 = args.d3 if args.d3 is not None else profile.default_key
    report = {'module_family': f'{profile.module_family:08X}',
              'cabinet_controls': profile.cabinet_controls,
              'ram_mib': profile.ram_mib, 'ram_size': profile.ram_size,
              'config_clear_start': profile.config_clear_start,
              'd3': d3 if args.runtime_dump is None else None}
    if not args.header_only:
        _, runtime_report = prepare_runtime(args.database, hashlib.sha256(data).hexdigest(),
                                            d3=d3 if args.runtime_dump is None else None,
                                            runtime_dump_path=args.runtime_dump)
        report['runtime_sha256'] = runtime_report['runtime_sha256']
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
