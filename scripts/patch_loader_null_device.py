#!/usr/bin/env python3
"""Patch the loader's null device-path crash without changing valid paths.

The original routine dereferences its optional string object as ``ptr + 4``
even when device enumeration left ``ptr`` as NULL.  The trampoline below
returns the routine's normal failure value for NULL and executes the original
CreateFileW path byte-for-byte for every non-NULL pointer.

The routine is found by its instructions, not by the loader's version: a
loader that contains it gets the same patch, one that does not is copied
unchanged. Loaders built with frame pointers load the pointer after the
arguments; there the check precedes the last argument.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
from pathlib import Path


# The loader of the verified CF image and its patched form.
ORIGINAL_SHA256 = "2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1"
PATCHED_SHA256 = "d5dd84e59c59a24af4f1dfdd486882bfc6fa777e0dcc22fa3ae7314999ab3aeb"
# push esi; four CreateFileW arguments; mov esi,ecx; mov eax,[esi+28h]
ROUTINE_HEAD = bytes.fromhex("56 6a 00 6a 00 6a 03 6a 00 8b f1 8b 46 28")
PATCH_EXPECTED = bytes.fromhex("6a 00 68 00 00 00 c0")   # push 0; push 0xC0000000
ROUTINE_CALL = bytes.fromhex("83 c0 04 50 ff 15")        # add eax,4; push eax; call [CreateFileW]
# cmp eax,-1; mov [esi],eax; jne ...; the routine's own failure exit follows.
ROUTINE_CHECK = bytes.fromhex("83 f8 ff 89 06 75")
ROUTINE_FAILURE = bytes.fromhex("33 c0 5e c3")           # xor eax,eax; pop esi; ret
# The same routine built with a frame pointer: push ebp; mov ebp,esp; push ecx;
# mov [ebp-4],ecx; five arguments; push 0xC0000000; mov eax,[ebp-4];
# mov ecx,[eax+member]; add ecx,4; push ecx; call [CreateFileW]
FRAME_HEAD = bytes.fromhex("55 8b ec 51 89 4d fc 6a 00 6a 00 6a 03 6a 00 6a 00")
FRAME_EXPECTED = bytes.fromhex("68 00 00 00 c0")         # push 0xC0000000
FRAME_LOAD = bytes.fromhex("8b 45 fc 8b 48")             # mov eax,[ebp-4]; mov ecx,[eax+member]
FRAME_CALL = bytes.fromhex("83 c1 04 51 ff 15")          # add ecx,4; push ecx; call [CreateFileW]
FRAME_CAVE = 0x80                                        # behind the room of the other trampolines
FRAME_CAVE_STEP = 0x20
CAVE_ALIGNMENT = 16
# Room behind the code for this trampoline and the idle-wait trampolines.
CAVE_ROOM = 0x100


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_section(data: bytes, offset: int) -> tuple[int, int, int, int] | None:
    """Header offset, raw pointer, raw size and virtual size of the section holding a file offset."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if pe + 24 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        return None
    count = struct.unpack_from("<H", data, pe + 6)[0]
    table = pe + 24 + struct.unpack_from("<H", data, pe + 20)[0]
    for index in range(count):
        header = table + index * 40
        if header + 40 > len(data):
            return None
        virtual_size, _, raw_size, raw = struct.unpack_from("<IIII", data, header + 8)
        if raw <= offset < raw + raw_size:
            return header, raw, raw_size, virtual_size
    return None


def frame_sites(data: bytes) -> list[tuple[int, int]]:
    """Patch site and member offset of every frame-pointer build of the routine."""
    found = []
    start = data.find(FRAME_HEAD + FRAME_EXPECTED + FRAME_LOAD)
    while start >= 0:
        load = start + len(FRAME_HEAD) + len(FRAME_EXPECTED) + len(FRAME_LOAD)
        if data[load + 1:load + 1 + len(FRAME_CALL)] == FRAME_CALL and data[load] < 0x80:
            found.append((start + len(FRAME_HEAD), data[load]))
        start = data.find(FRAME_HEAD + FRAME_EXPECTED + FRAME_LOAD, start + 1)
    return found


def frame_plan(data: bytes) -> tuple[list[tuple[int, int, int]], int, int] | None:
    """(site, member, trampoline offset) of each routine, section header and new virtual size."""
    sites = frame_sites(data)
    if not sites or FRAME_CAVE + len(sites) * FRAME_CAVE_STEP > CAVE_ROOM:
        return None
    section = code_section(data, sites[0][0])
    if section is None:
        return None
    header, raw, raw_size, virtual_size = section
    cave = raw + (virtual_size + CAVE_ALIGNMENT - 1) // CAVE_ALIGNMENT * CAVE_ALIGNMENT
    if virtual_size > raw_size or cave + CAVE_ROOM > raw + raw_size:
        return None
    if any(data[raw + virtual_size:raw + raw_size]):
        return None  # the padding behind the code is in use
    if any(not raw <= site < raw + raw_size for site, _ in sites):
        return None
    return ([(site, member, cave + FRAME_CAVE + index * FRAME_CAVE_STEP)
             for index, (site, member) in enumerate(sites)], header, raw_size)


def frame_trampoline(site: int, member: int, cave: int) -> bytes:
    check = bytes.fromhex("8b 45 fc") + bytes((0x83, 0x78, member, 0x00)) + bytes.fromhex(
        "75 06"            # jne: the pointer is set
        "33 c0"            # return FALSE
        "8b e5 5d"         # leave the frame; that discards the five arguments
        "c3")
    return check + FRAME_EXPECTED + jump(cave + len(check) + len(FRAME_EXPECTED), site + len(FRAME_EXPECTED))


def plan(data: bytes) -> tuple[int, int, int, int] | None:
    """Patch site, trampoline offset, section header and new virtual size; None: not this routine."""
    signature = ROUTINE_HEAD + PATCH_EXPECTED + ROUTINE_CALL
    start = data.find(signature)
    if start < 0 or data.find(signature, start + 1) >= 0:
        return None
    after_call = start + len(signature) + 4
    if data[after_call:after_call + len(ROUTINE_CHECK)] != ROUTINE_CHECK:
        return None
    if ROUTINE_FAILURE not in data[after_call:after_call + 40]:
        return None
    site = start + len(ROUTINE_HEAD)
    section = code_section(data, site)
    if section is None:
        return None
    header, raw, raw_size, virtual_size = section
    cave = raw + (virtual_size + CAVE_ALIGNMENT - 1) // CAVE_ALIGNMENT * CAVE_ALIGNMENT
    if virtual_size > raw_size or cave + CAVE_ROOM > raw + raw_size:
        return None
    if any(data[raw + virtual_size:raw + raw_size]):
        return None  # the padding behind the code is in use
    return site, cave, header, raw_size


def jump(source: int, destination: int) -> bytes:
    return b"\xe9" + struct.pack("<i", destination - (source + 5))


def trampoline(site: int, cave: int) -> bytes:
    return (bytes.fromhex(
        "85 c0"            # test eax,eax
        "74 0c"            # jz null_path
        "6a 00"            # original push 0 (share mode)
        "68 00 00 00 c0")  # original push 0xC0000000 (access)
        + jump(cave + 11, site + len(PATCH_EXPECTED))  # back to the original add eax,4
        + bytes.fromhex(
        "83 c4 10"         # null_path: discard four prior CreateFileW arguments
        "33 c0"            # return FALSE
        "5e"               # restore ESI saved by the routine
        "c3"))             # return to caller


def patched(source: bytes) -> bytes:
    """The loader with the trampoline, or the unchanged loader when the routine is not in it."""
    found = plan(source)
    if found is None:
        if digest(source) == ORIGINAL_SHA256:
            raise ValueError("patch-site bytes do not match the verified loader")
        frames = frame_plan(source)
        if frames is None:
            return source
        routines, header, virtual_size = frames
        output = bytearray(source)
        for site, member, cave in routines:
            output[site:site + len(FRAME_EXPECTED)] = jump(site, cave)
            code = frame_trampoline(site, member, cave)
            output[cave:cave + len(code)] = code
        struct.pack_into("<I", output, header + 8, virtual_size)
        return bytes(output)
    site, cave, header, virtual_size = found
    output = bytearray(source)
    output[site:site + len(PATCH_EXPECTED)] = jump(site, cave) + b"\x90\x90"
    code = trampoline(site, cave)
    output[cave:cave + len(code)] = code
    struct.pack_into("<I", output, header + 8, virtual_size)
    if digest(source) == ORIGINAL_SHA256 and digest(output) != PATCHED_SHA256:
        raise ValueError("patched verified loader differs from its verified form")
    return bytes(output)


def trampoline_offset(data: bytes) -> int | None:
    """Where a patched loader keeps the trampoline; the idle-wait patch builds behind it."""
    head = data.find(ROUTINE_HEAD + b"\xe9")
    while head >= 0:
        site = head + len(ROUTINE_HEAD)
        if data[site + 5:site + 7] == b"\x90\x90" and data[site + 7:site + 7 + len(ROUTINE_CALL)] == ROUTINE_CALL:
            cave = site + 5 + struct.unpack_from("<i", data, site + 1)[0]
            if 0 <= cave < len(data) and data[cave:cave + len(trampoline(site, cave))] == trampoline(site, cave):
                return cave
        head = data.find(ROUTINE_HEAD + b"\xe9", head + 1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--verify", action="store_true",
                        help="only check that DESTINATION is the patched form of SOURCE")
    args = parser.parse_args()

    source = args.source.read_bytes()
    try:
        output = patched(source)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.verify:
        if args.destination.read_bytes() != output:
            raise SystemExit(f"{args.destination} is not the patched form of {args.source}")
        return 0

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    temporary.write_bytes(output)
    os.replace(temporary, args.destination)
    print(f"source_sha256={digest(source)}")
    print(f"patched_sha256={digest(output)}")
    if output == source:
        print("Hinweis: Diese Loader-Version enthält die bekannte Geräte-Routine nicht; "
              "der Loader bleibt unverändert.")
    elif digest(source) != ORIGINAL_SHA256:
        print("Hinweis: Loader-Version nicht verifiziert; die bekannte Geräte-Routine wurde angepasst.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
