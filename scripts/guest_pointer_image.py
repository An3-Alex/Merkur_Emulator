"""Disable Windows mouse delivery in a stopped M202 working copy.

Keep i8042/keyboard and all touch/serial services intact. Save original mouse
service values and the hive before changing only Mouclass Start values.
"""
from pathlib import Path
import json
import shutil
import struct
import sys


def mouse_starts(hive):
    result = {}
    for control in hive.node_children(hive.root()):
        name = hive.node_name(control)
        if not name.startswith('ControlSet'):
            continue
        services = hive.node_get_child(control, 'Services')
        node = hive.node_get_child(services, 'Mouclass') if services else 0
        if not node:
            continue
        value = hive.node_get_value(node, 'Start')
        kind, data = hive.value_value(value)
        if kind != 4 or len(data) != 4:
            raise ValueError(f'Invalid Mouclass Start: {name}')
        result[name] = (node, struct.unpack('<I', data)[0])
    if not result:
        raise ValueError('Windows Mouclass service missing')
    return result


def disable(root: Path):
    import hivex
    system = root / 'WINDOWS/system32/config/SYSTEM'
    previous = mouse_starts(hivex.Hivex(str(system)))
    if all(value == 4 for _, value in previous.values()):
        return 'M202_WINDOWS_MOUSE_DISABLED already-disabled'
    backup = root / 'NVRAM/m202-pointer-backup'
    backup.mkdir(exist_ok=True)
    saved = backup / 'SYSTEM'
    if not saved.exists():
        shutil.copy2(system, saved)
        (backup / 'mouse-starts.json').write_text(json.dumps(
            {name: value for name, (_, value) in previous.items()}, indent=2))
    pending = backup / 'SYSTEM.mouse-disabled'
    shutil.copy2(system, pending)
    hive = hivex.Hivex(str(pending), write=True)
    for node, _ in mouse_starts(hive).values():
        hive.node_set_value(node, {'key': 'Start', 't': 4,
                                   'value': struct.pack('<I', 4)})
    hive.commit(None)
    del hive
    verified = mouse_starts(hivex.Hivex(str(pending)))
    if not all(value == 4 for _, value in verified.values()):
        raise ValueError('Mouse service verification failed')
    shutil.copyfile(pending, system)
    if system.read_bytes() != pending.read_bytes():
        raise ValueError('Mouse hive copy failed')
    return 'M202_WINDOWS_MOUSE_DISABLED controls=' + ','.join(verified)


if __name__ == '__main__':
    print(disable(Path(sys.argv[1])))
