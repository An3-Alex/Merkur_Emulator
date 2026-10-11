#!/usr/bin/env python3
"""Cabinet software the setup builds on: the verified versions and notes for others.

CF cards of the same cabinet differ mostly in their games. The setup performs
the same steps for every card; a file that is not the verified version is
reported, not refused.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

GAME_HASH = "27c4553927397b1e8443d6caea12e5b4e7282e4948b67b85c80c4db0d1d0427d"
# Path in the CF image, verified SHA-256, what it is.
ORIGINALS = (
    ("WINDOWS/system32/Cgos.dll", "480703586ea6f5bdc9ae3d8aa7bb47f03fa4d8234b48a3f2abc92356fb76a14e",
     "Board-Bibliothek"),
    ("WINDOWS/explorer.exe", "2fb4233b541431a1b940ed5af6f11096b7fd5846316e3c4e55bd0e9a7b37a5c1", "Loader"),
    ("WINDOWS/system32/fbwflib.dll", "17dc9581c25b9c77d2d4368c3923f83e414d6be9a8acf675871ff2ce6df28263",
     "Schreibfilter-Bibliothek"),
)
# The verified card keeps the game program in both folders, others only in one.
GAME_DIRECTORIES = ("NVRAM", "WorkDir")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def note(path: Path, verified: set[str], label: str) -> str | None:
    """None for a verified file; otherwise the line shown to the operator."""
    if not path.is_file():
        raise ValueError(f"missing file: {path}")
    actual = sha256(path)
    if actual in verified:
        return None
    return f"Hinweis: {label} ({path.name}) ist eine nicht verifizierte Version ({actual[:16]}…)."


def game_programs(root: Path) -> list[Path]:
    """The game program of this card, from every folder that holds one."""
    found = [root / directory / "game.exe" for directory in GAME_DIRECTORIES]
    found = [path for path in found if path.is_file()]
    if not found:
        raise ValueError(f"missing file: {root / GAME_DIRECTORIES[-1] / 'game.exe'}")
    return found


def original_notes(root: Path) -> list[str]:
    """Notes for an untouched CF image; a missing file means it is not such an image."""
    notes = []
    checks = [(root / relative, verified, label) for relative, verified, label in ORIGINALS]
    checks += [(path, GAME_HASH, "Spielprogramm") for path in game_programs(root)]
    for path, verified, label in checks:
        line = note(path, {verified}, label)
        if line is not None and line not in notes:
            notes.append(line)
    return notes


# At every start the game writes the list it computes over its data (file
# sizes per folder and more) to NVRAM and compares it with the list on the
# card.  A card whose game data were changed later keeps its old list and
# stops with SYSTEM ERROR 28.
CHECK_LIST = "WorkDir/CheckSumAdpConfigTool.txt"
CHECK_COMPUTED = "NVRAM/CheckTemp.txt"


def _check_entries(path: Path) -> list[tuple[bytes, bytes]] | None:
    """(label, value) of every line, or None when this is no complete list."""
    if not path.is_file():
        return None
    entries = [line.rpartition(b": ") for line in path.read_bytes().split(b"\r\n") if line]
    complete = entries and entries[-1][0].startswith(b"GesamtCheckSumme")
    if not complete or any(not label or not value.strip().isdigit() for label, _, value in entries):
        return None
    return [(label, value) for label, _, value in entries]


def check_list_pending(root: Path) -> bool:
    """The game computed a complete list with other values than the card's list holds."""
    computed, listed = _check_entries(root / CHECK_COMPUTED), _check_entries(root / CHECK_LIST)
    return (computed is not None and listed is not None and computed != listed
            and [label for label, _ in computed] == [label for label, _ in listed])


def adapt_check_list(root: Path) -> None:
    """Give the working copy the list the game computed; the card's list is kept beside it."""
    listed, saved = root / CHECK_LIST, root / (CHECK_LIST + ".pre-m90")
    if not saved.exists():
        saved.write_bytes(listed.read_bytes())
    listed.write_bytes((root / CHECK_COMPUTED).read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="mounted CF image")
    arguments = parser.parse_args()
    try:
        lines = original_notes(arguments.root)
    except ValueError as exc:
        raise SystemExit(str(exc))
    for line in lines:
        print(line)
    if lines:
        print("Hinweis: Diese CF-Karte ist nicht in der Liste der geprüften Kombinationen; "
              "sie wird wie die geprüfte eingerichtet.")
