#!/usr/bin/env bash
# Called before QEMU starts; only its selected M202 working image is mounted.
set -euo pipefail
[[ $# -eq 1 ]] || exit 2
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
source "$(dirname -- "$0")/image_partition.sh"
mount_dir=$(mktemp -d /tmp/m202-pointer.XXXXXX)
loop_device=
mounted=0
cleanup() {
    if (( mounted )); then sync; umount "$mount_dir"; fi
    if [[ -n "$loop_device" ]]; then losetup -d "$loop_device"; fi
    rmdir "$mount_dir"
}
trap cleanup EXIT
loop_device=$(image_loop_device "$1" rw)
ntfs-3g -o big_writes "$loop_device" "$mount_dir"
mounted=1
python3 "$script_dir/guest_pointer_image.py" "$mount_dir"
