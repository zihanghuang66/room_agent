#!/usr/bin/env bash
# Run from this bundle's scripts directory on an x86_64 Linux Docker host.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/.." && pwd)
mkdir -p "$ROOT/return_output"
exec > >(tee -a "$ROOT/return_output/prepare-and-build.log") 2>&1
finish() {
    code=$?
    trap - EXIT
    printf 'stage=%s\nexit_code=%s\n' "$phase" "$code" > "$ROOT/return_output/prepare.status"
    if (( code != 0 )); then
        printf 'ERROR: exit=%s; logs: %s/return_output\n' "$code" "$ROOT"
    fi
    python3 "$HERE/package_build_report.py" || printf 'WARNING: diagnostic archive could not be created\n'
    exit "$code"
}
phase=prepare
trap finish EXIT
export HTTP_PROXY=${ROOM_BUILD_PROXY:-http://127.0.0.1:17897}
export HTTPS_PROXY="$HTTP_PROXY"
bash "$HERE/prepare_remote_base.sh"
python3 "$HERE/fetch_cudnn_ranges.py"
python3 "$HERE/prepare_gpu_wheels.py"
python3 "$HERE/fetch_cudnn_ranges.py" --package opencv
python3 "$HERE/prepare_extra_gpu_wheels.py"
python3 "$HERE/prepare_remaining_gpu_wheels.py"
phase=build-and-export
bash "$HERE/build_remote.sh"
phase=verify-export
python3 "$HERE/verify_export.py"
phase=complete
