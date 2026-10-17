"""Attach the XP service executable to the shared SRAM compatibility DLL.

Adds only a PE import section. Original instructions/resources and original
imports are preserved. Other versions of the service program get the same
import; their data deadlines are extended where the same instruction is found.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import struct

SERVICE_HASH = "d9d04e9b70bf0fc6ec3dd4e57624940b107881067b4d1952f581bfc12befa2c8"
# Earlier shims: before the service import, and before the service was
# recognized by its import (an 8.3-named service got no SRAM and crashed).
PREVIOUS_SRAM_HASHES = {"4d62ee6e183ba534f7ac7d2780d4a5fb90f2394bc4fc68fd6d6b9ea640b3aa94",
                        "31cac0b2141d2c8896e9dcf5cc2bd19ea0e3e0e1196625b9a79645d1edc6c9d6",
                        "4b98b1f2a60e939e1dc40c135e73edb47805249493d566e3f0599106f21ed608",  # before the 10-MB guest log limit
                        # found the game's imports at the verified version's addresses only
                        "555f2a7b6e886e9476b7f83ecee89c3cfa369c823f82f5bdf4c6181c7ce41dd2",
                        # without the cache exports newer game programs import
                        "4b6445c874c7495b88bc0c7cb138741fcc7fd6ca1944afbcd077aa1eb6d320cf"}
SRAM_HASH = "05db66e5c87089383ed74b83a0ee1b07b1c0f6422b2112b68d3d61366bb260a7"
SERVICE_PATH = "WorkDir/GGSG_Servic/GGSG_Servic.exe"
# The service program requests its data from the database through the game
# and shows "FEHLER : keine Daten" when they are not complete in time: 60 s for
# the menu with its values, 25 s for the follow-up request. The cabinet's
# database sends them within seconds; the emulated one needs several times as
# long. Entries: VA of the MOV EDX,imm32 loading the time, cabinet ms, emulator ms.
SERVICE_DEADLINES = ((0x00406B03, 60_000, 300_000), (0x00406D80, 25_000, 125_000))
# Other versions wait for the follow-up request with another time (M202 service
# 7.5.4.6: 30 s). It is extended where exactly one MOV EDX,imm32;MOV EAX,EBX
# passes it to the same wait routine as the 60 s menu deadline.
FOLLOW_UP_DEADLINES = ((25_000, 125_000), (30_000, 150_000))
MENU_DEADLINE = 60_000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wait_calls(source: bytes, milliseconds: int) -> list[tuple[int, int]]:
    """(offset, call target) of each MOV EDX,ms; MOV EAX,EBX; CALL rel32."""
    instruction = b"\xBA" + struct.pack("<I", milliseconds) + b"\x8B\xC3\xE8"
    found, at = [], source.find(instruction)
    while at >= 0:
        target = at + 12 + struct.unpack_from("<i", source, at + 8)[0]
        found.append((at, target))
        at = source.find(instruction, at + 1)
    return found


def patch_service(source: bytes, *, writable_iat: bool = True, longer_deadlines: bool = True,
                  follow_up_deadline: bool = True) -> bytes:
    """writable_iat=False reproduces the earlier patch, which XP cannot load: its
    loader unprotects only the section holding the import directory while
    binding, so the Borland IATs in read-only .idata faulted (0xC0000005).
    longer_deadlines=False reproduces the patch before SERVICE_DEADLINES,
    follow_up_deadline=False the one before FOLLOW_UP_DEADLINES."""
    verified = digest(source) == SERVICE_HASH
    if len(source) < 0x40 or source[:2] != b"MZ":
        raise ValueError("Unrecognized service executable; original must remain unchanged")
    data = bytearray(source)
    u16 = lambda off: struct.unpack_from("<H", data, off)[0]
    u32 = lambda off: struct.unpack_from("<I", data, off)[0]
    pe = u32(0x3C)
    if pe + 248 > len(data):
        raise ValueError("Unrecognized service executable; original must remain unchanged")
    opt = pe + 24
    count = u16(pe + 6)
    table = opt + u16(pe + 20)
    if data[pe:pe + 4] != b"PE\0\0" or u16(pe + 4) != 0x14C or u16(opt) != 0x10B:
        raise ValueError("Expected XP x86 PE32 service")
    sections = [(u32(table + i * 40 + 12), u32(table + i * 40 + 8),
                 u32(table + i * 40 + 20), u32(table + i * 40 + 16)) for i in range(count)]
    header = table + count * 40
    first_raw = min(raw for _, _, raw, size in sections if size)
    if header + 40 > first_raw or any(data[header:header + 40]):
        raise ValueError("No safe space for additive SRAM import section")
    def offset(rva):
        for va, _, raw, size in sections:
            if va <= rva < va + size:
                return raw + rva - va
        raise ValueError("Invalid import RVA")
    if longer_deadlines:
        for address, cabinet, emulator in SERVICE_DEADLINES:
            instruction = b"\xBA" + struct.pack("<I", cabinet)
            try:
                at = offset(address - u32(opt + 28))
            except ValueError:
                at = -1
            if at < 0 or data[at:at + 5] != instruction:
                if verified:
                    raise ValueError("Service data deadline not at its verified location")
                # Another version: the same instruction, if it occurs exactly once.
                at = bytes(source).find(instruction)
                if at < 0 or bytes(source).find(instruction, at + 1) >= 0:
                    continue
            struct.pack_into("<I", data, at + 1, emulator)
        menu = wait_calls(bytes(source), MENU_DEADLINE)
        if follow_up_deadline and not verified and len(menu) == 1:
            for cabinet, emulator in FOLLOW_UP_DEADLINES:
                calls = [at for at, target in wait_calls(bytes(source), cabinet) if target == menu[0][1]]
                if len(calls) == 1 and data[calls[0] + 1:calls[0] + 5] == struct.pack("<I", cabinet):
                    struct.pack_into("<I", data, calls[0] + 1, emulator)
    old_import = offset(u32(opt + 104))
    descriptors = bytearray()
    cursor = old_import
    while any(data[cursor:cursor + 20]):
        if cursor + 20 > len(data) or len(descriptors) > 8192:
            raise ValueError("Invalid import descriptors")
        descriptors += data[cursor:cursor + 20]
        cursor += 20
    if writable_iat:
        # The moved import directory no longer marks the original IATs for
        # the XP loader; their sections must allow writes during binding.
        thunks = {struct.unpack_from("<I", descriptors, index + 16)[0]
                  for index in range(0, len(descriptors), 20)}
        for index, (va, virtual, _, size) in enumerate(sections):
            if any(va <= thunk < va + max(virtual, size) for thunk in thunks):
                flags = table + index * 40 + 36
                struct.pack_into("<I", data, flags, u32(flags) | 0x80000000)
    align = lambda number, unit: (number + unit - 1) // unit * unit
    section_alignment, file_alignment = u32(opt + 32), u32(opt + 36)
    rva = align(max(va + max(size, virtual) for va, virtual, _, size in sections), section_alignment)
    raw = align(len(data), file_alignment)
    descriptor_end = len(descriptors) + 40
    ilt, iat = descriptor_end, descriptor_end + 8
    dll_name = descriptor_end + 16
    symbol = align(dll_name + len(b"FBWFLIB.dll\0"), 2)
    payload = bytearray(symbol + len(b"\0\0SramCompatInitialize\0"))
    payload[:len(descriptors)] = descriptors
    struct.pack_into("<IIIII", payload, len(descriptors), rva + ilt, 0, 0, rva + dll_name, rva + iat)
    struct.pack_into("<I", payload, ilt, rva + symbol)
    struct.pack_into("<I", payload, iat, rva + symbol)
    payload[dll_name:dll_name + 12] = b"FBWFLIB.dll\0"
    payload[symbol:] = b"\0\0SramCompatInitialize\0"
    raw_size = align(len(payload), file_alignment)
    struct.pack_into("<8sIIIIIIHHI", data, header, b".m90sram", len(payload), rva,
                     raw_size, raw, 0, 0, 0, 0, 0xC0000040)
    struct.pack_into("<H", data, pe + 6, count + 1)
    struct.pack_into("<I", data, opt + 8, u32(opt + 8) + raw_size)
    struct.pack_into("<I", data, opt + 56, align(rva + len(payload), section_alignment))
    struct.pack_into("<I", data, opt + 64, 0)  # checksum of original no longer applies
    struct.pack_into("<II", data, opt + 104, rva, descriptor_end)
    struct.pack_into("<II", data, opt + 96 + 11 * 8, 0, 0)  # discard bound import cache
    data.extend(bytes(raw - len(data)))
    data.extend(payload)
    data.extend(bytes(raw_size - len(payload)))
    return bytes(data)


def update(root: Path, proxy: Path, *, check_only: bool = False) -> str:
    from graphics_update import inside
    root = root.resolve()
    replacement = proxy.read_bytes()
    if digest(replacement) != SRAM_HASH:
        raise ValueError("Unrecognized SRAM compatibility DLL")
    service = inside(root, SERVICE_PATH)
    backup = service.with_name(service.name + ".pre-m90-sram")
    inside(root, str(backup.relative_to(root)))
    targets = [inside(root, "NVRAM/FBWFLIB.dll"), inside(root, "WorkDir/FBWFLIB.dll")]
    # A CF image without this service program still gets the game's SRAM DLLs.
    present = service.is_file()
    changed = False
    if present:
        current = service.read_bytes()
        original = backup.read_bytes() if backup.exists() else current
        patched = patch_service(original)
        earlier = (patch_service(original, follow_up_deadline=False),
                   patch_service(original, longer_deadlines=False),
                   patch_service(original, writable_iat=False, longer_deadlines=False))
        if current not in (original, patched, *earlier):
            raise ValueError("Service differs from original and verified SRAM version")
        if current != original and not backup.exists():
            raise ValueError("Original service backup missing")
        targets.append(service.with_name("FBWFLIB.dll"))
        changed = current != patched
    for target in targets:
        if target.exists():
            actual = digest(target.read_bytes())
            if actual != SRAM_HASH and actual not in PREVIOUS_SRAM_HASHES:
                raise ValueError(f"Unrecognized SRAM DLL: {target}")
            changed |= actual != SRAM_HASH
        else:
            changed = True
        if target.with_name(target.name + ".m90-sram-new").exists():
            raise ValueError(f"Unfinished SRAM update: {target}")
    if not changed:
        return "Service SRAM already current"
    if check_only:
        return "Service SRAM update required"
    # Back up every existing file before changing any; do not touch SRAM data.
    if present and not backup.exists():
        with backup.open("xb") as stream:
            stream.write(original); stream.flush(); os.fsync(stream.fileno())
    old_files = [(target, target.read_bytes() if target.exists() else None) for target in targets]
    for target, previous in old_files:
        saved = target.with_name(target.name + ".pre-service-sram")
        inside(root, str(saved.relative_to(root)))
        if previous is not None and not saved.exists():
            with saved.open("xb") as stream:
                stream.write(previous); stream.flush(); os.fsync(stream.fileno())
    installed = []
    def replace(target, content):
        temporary = target.with_name(target.name + ".m90-sram-new")
        try:
            with temporary.open("xb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    try:
        for target, _ in old_files:
            replace(target, replacement); installed.append(target)
        # DLL is installed first, then add the import to the executable.
        if present:
            replace(service, patched)
    except Exception:
        for target, previous in reversed(old_files):
            if target in installed:
                if previous is None: target.unlink(missing_ok=True)
                else: replace(target, previous)
        raise
    return "Service SRAM updated; original executable and DLLs backed up"


def note(root: Path) -> str | None:
    """What to tell the operator about a service program that is not the verified one."""
    from graphics_update import inside
    service = inside(root.resolve(), SERVICE_PATH)
    if not service.is_file():
        return "Hinweis: Auf diesem CF-Image fehlt das bekannte Serviceprogramm; es wird nicht angepasst."
    backup = service.with_name(service.name + ".pre-m90-sram")
    original = (backup if backup.exists() else service).read_bytes()
    if digest(original) == SERVICE_HASH:
        return None
    patched = patch_service(original)[:len(original)]
    cabinets = {cabinet for _, cabinet, _ in SERVICE_DEADLINES} | {cabinet for cabinet, _ in FOLLOW_UP_DEADLINES}
    instructions = [b"\xBA" + struct.pack("<I", cabinet) for cabinet in sorted(cabinets)]
    extended = sum(original.count(instruction) - patched.count(instruction) for instruction in instructions)
    return ("Hinweis: Serviceprogramm-Version nicht verifiziert; es wurde wie das bekannte angebunden "
            f"({extended} von {len(SERVICE_DEADLINES)} Datenfristen verlängert).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("proxy", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(update(args.root, args.proxy, check_only=args.check_only))
    if not args.check_only and note(args.root):
        print(note(args.root))
