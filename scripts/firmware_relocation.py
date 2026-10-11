"""Find the bridge's firmware addresses in another build of the same database code.

The bridge works on addresses of the verified database build.  Another build
of the same code keeps it elsewhere.  firmware_addresses.json holds, for every
address the bridge uses, short hashes of code windows around it - for a RAM
address around the instructions that use it - and no firmware bytes.  With
them the addresses are located in the selected database.  An address that
cannot be located unambiguously stops the start; nothing is guessed.

    python firmware_relocation.py --build VERIFIED_RUNTIME   rewrites the table
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import types
import zlib

SCRIPT_DIR = Path(__file__).resolve().parent
TABLE = SCRIPT_DIR / "firmware_addresses.json"
SOURCES = ("m68k_database_bridge.py", "cabinet_controls.py")
LITERAL = re.compile(r"0x([0-9A-Fa-f]{4,8})\b")
LOAD_ADDRESS = 0x1000
RAM_LOW, RAM_HIGH = 0x001E0000, 0x00200000
# Values in the address range that are not addresses of the database build.
NOT_ADDRESSES = frozenset({
    0x1100, 0x2000, 0x2580, 0x2700, 0x3A99, 0x58A9, 0xFFFF, 0x10000, 0x100000,
    0x1FFF80, 0x1FFC00,
})
WINDOW = 8          # bytes hashed per window
REACH = 160          # windows start this far before and after an address
STEP = 8
MAX_SITES = 6       # instructions looked at for one RAM address
MIN_WEIGHT = 2.0    # agreement needed for a place: two windows found nowhere else
NEAR = 32           # two candidate places this close differ by inserted or dropped bytes
NO_WINDOW = "-" * 8


class UnresolvedAddresses(ValueError):
    """The selected database is not a build of the code the bridge knows."""


def digest(window: bytes) -> str:
    return f"{zlib.crc32(window):08x}"


def is_ram(address: int) -> bool:
    return RAM_LOW <= address < RAM_HIGH


def source_addresses(directory: Path = SCRIPT_DIR, code_end: int | None = None) -> set[int]:
    """Every literal of the bridge sources that names a place in the database build."""
    found = set()
    for name in SOURCES:
        for match in LITERAL.finditer((directory / name).read_text(encoding="utf-8")):
            value = int(match.group(1), 16)
            if value in NOT_ADDRESSES:
                continue
            if is_ram(value) or (LOAD_ADDRESS + 0x100 <= value < RAM_LOW
                                 and (code_end is None or value < code_end)):
                found.add(value)
    return found


def _windows(runtime: bytes, offset: int) -> str:
    """Hashes of the windows around a place, one after the other; gaps where none is usable."""
    parts = []
    for shift in range(-REACH, REACH + 1, STEP):
        start = offset + shift
        window = runtime[start:start + WINDOW] if start >= 0 else b""
        usable = len(window) == WINDOW and len(set(window)) >= 4
        parts.append(digest(window) if usable else NO_WINDOW)
    return "".join(parts)


def build_table(runtime: bytes, directory: Path = SCRIPT_DIR) -> dict:
    """The table for the verified build.  Needs the firmware; its output does not contain it."""
    code_end = LOAD_ADDRESS + len(runtime)
    entries = {}
    for address in sorted(source_addresses(directory, code_end)):
        if not is_ram(address):
            entries[f"{address:08X}"] = {"at": _windows(runtime, address - LOAD_ADDRESS)}
            continue
        needle, sites, start = struct.pack(">I", address), [], 0
        while True:
            hit = runtime.find(needle, start)
            if hit < 0:
                break
            start = hit + 1
            if hit % 2 == 0:
                sites.append(hit)
        # Sites spread over the whole build: a change in one routine leaves the others.
        chosen = sites[::max(1, len(sites) // MAX_SITES)][:MAX_SITES]
        entries[f"{address:08X}"] = {"used": [[site, _windows(runtime, site)] for site in chosen]}
    return {"runtime_sha256": hashlib.sha256(runtime).hexdigest().upper(),
            "code_end": f"{code_end:08X}", "window": WINDOW, "reach": REACH, "step": STEP, "addresses": entries}


def load_table(path: Path = TABLE) -> dict:
    table = json.loads(path.read_text(encoding="utf-8"))
    if (table["window"], table["reach"], table["step"]) != (WINDOW, REACH, STEP):
        raise ValueError("firmware address table was built with other window settings")
    return table


def _place(windows: str, positions: dict[str, list[int]]) -> int | None:
    """Offset in the selected build that most windows of a place agree on."""
    votes: Counter[int] = Counter()
    named: dict[int, set[int]] = {}
    for index in range(0, len(windows), 8):
        shift = -REACH + index // 8 * STEP
        found = positions.get(windows[index:index + 8], ())
        for position in found:
            # A window that occurs in many places says little about any of them.
            votes[position - shift] += 1.0 / len(found)
        named[shift] = {position - shift for position in found}
    ranked = votes.most_common(2)
    if not ranked or ranked[0][1] < MIN_WEIGHT:
        return None
    if len(ranked) == 1:
        return ranked[0][0]
    (first, first_weight), (second, second_weight) = ranked
    if second_weight >= MIN_WEIGHT and abs(first - second) <= NEAR:
        # A few bytes were added or dropped close by, and the windows before
        # and behind that spot point to places a little apart.  The address
        # is where its own window is, or where both neighbours agree.
        both = {first, second}
        own = named.get(0, set()) & both
        if len(own) == 1:
            return own.pop()
        before, after = named.get(-STEP, set()) & both, named.get(STEP, set()) & both
        return before.pop() if len(before) == 1 and before == after else None
    # Similar code elsewhere shares some windows; the place itself has more.
    return first if first_weight - second_weight >= 1.0 else None


def resolve(table: dict, runtime: bytes) -> dict[int, int]:
    """Verified address -> address in the selected build, for every address of the table."""
    wanted = set()
    for entry in table["addresses"].values():
        for windows in [entry["at"]] if "at" in entry else [site[1] for site in entry["used"]]:
            wanted.update(windows[index:index + 8] for index in range(0, len(windows), 8))
    wanted.discard(NO_WINDOW)
    sought = {int(key, 16) for key in wanted}
    positions: dict[str, list[int]] = {}
    crc32 = zlib.crc32
    for position in range(len(runtime) - WINDOW + 1):   # tables may begin at odd addresses
        key = crc32(runtime[position:position + WINDOW])
        if key in sought:
            positions.setdefault(f"{key:08x}", []).append(position)
    moved: dict[int, int] = {}
    open_ram = []
    for text, entry in table["addresses"].items():
        address = int(text, 16)
        if "at" in entry:
            offset = _place(entry["at"], positions)
            if offset is not None:
                moved[address] = LOAD_ADDRESS + offset
            continue
        votes: Counter[int] = Counter()
        for _site, windows in entry["used"]:
            offset = _place(windows, positions)
            if offset is not None and offset + 4 <= len(runtime):
                value = struct.unpack_from(">I", runtime, offset)[0]
                if is_ram(value):
                    votes[value] += 1
        ranked = votes.most_common(2)
        if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            moved[address] = ranked[0][0]
        else:
            open_ram.append(address)
    # A RAM address no instruction names directly lies in a block that moved
    # as a whole: both located neighbours must have moved by the same amount.
    located = sorted(address for address in moved if is_ram(address))
    for address in open_ram:
        below = [other for other in located if other < address]
        above = [other for other in located if other > address]
        if below and above and moved[below[-1]] - below[-1] == moved[above[0]] - above[0]:
            moved[address] = address + moved[below[-1]] - below[-1]
    missing = sorted(int(text, 16) for text in table["addresses"] if int(text, 16) not in moved)
    if missing:
        raise UnresolvedAddresses(
            "Diese Datenbank ist kein Stand des bekannten Datenbankprogramms: "
            f"{len(missing)} von {len(table['addresses'])} Adressen wurden nicht gefunden "
            f"(erste: {missing[0]:08X}).")
    return moved


def relocated_source(text: str, moved: dict[int, int]) -> str:
    """The source with every address literal of the table set to its place in the selected build."""
    def replace(match: re.Match) -> str:
        value = int(match.group(1), 16)
        return f"0x{moved[value]:08X}" if value in moved else match.group(0)
    return LITERAL.sub(replace, text)


def relocated_bridge(moved: dict[int, int], directory: Path = SCRIPT_DIR) -> types.ModuleType:
    """The bridge and its controls as modules working on the selected build's addresses."""
    previous = {name: sys.modules.get(name) for name in ("cabinet_controls",)}
    modules = {}
    try:
        for file_name in reversed(SOURCES):          # controls first: the bridge imports them
            name = "cabinet_controls" if file_name == "cabinet_controls.py" else "m68k_database_bridge_relocated"
            module = types.ModuleType(name)
            module.__file__ = str(directory / file_name)
            source = relocated_source((directory / file_name).read_text(encoding="utf-8"), moved)
            if name == "cabinet_controls":
                sys.modules[name] = module
            exec(compile(source, module.__file__, "exec"), module.__dict__)
            modules[name] = module
    finally:
        for name, module in previous.items():
            if module is not None:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)
    return modules["m68k_database_bridge_relocated"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, metavar="VERIFIED_RUNTIME",
                        help="decrypted runtime of the verified database build (loaded at 0x1000)")
    parser.add_argument("--resolve", type=Path, metavar="RUNTIME",
                        help="decrypted runtime of another build: print where its addresses are")
    options = parser.parse_args()
    if options.build is not None:
        table = build_table(options.build.read_bytes())
        TABLE.write_text(json.dumps(table, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"{TABLE.name}: {len(table['addresses'])} addresses of build {table['runtime_sha256'][:16]}")
    if options.resolve is not None:
        moved = resolve(load_table(), options.resolve.read_bytes())
        for address, target in sorted(moved.items()):
            print(f"{address:08X} -> {target:08X} ({target - address:+d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
