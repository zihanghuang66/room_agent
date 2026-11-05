#!/usr/bin/env bash
# Mac: run after export_raw.sh completes and the r4 archive is expanded as .part.
set -Eeuo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../../.." && pwd)
DEST="$ROOT/return_output/images"
HOST=huang@192.168.3.66
REMOTE=/home/huang/room-image-build/return_output
FILE=room-litereality-agent-0.1.3-cuda124-r5.docker.tar
test ! -e "$DEST/$FILE.gz"
status=$(ssh -o BatchMode=yes "$HOST" "cat $REMOTE/ssh-platform-r5/export.status")
printf '%s\n' "$status"
printf '%s\n' "$status" | grep -qx 'stage=complete'
printf '%s\n' "$status" | grep -qx 'exit_code=0'
# Normal rsync temp-file mode preserves the old layer data used as a delta basis.
rsync -avP --stats --ignore-times -e 'ssh -o BatchMode=yes' \
    "$HOST:$REMOTE/$FILE" "$DEST/$FILE.part"
scp -q -o BatchMode=yes "$HOST:$REMOTE/$FILE.sha256" "$DEST/"
cd "$DEST"
read -r expected_hash expected_name < "$FILE.sha256"
[[ "$expected_name" == "$FILE" && "$expected_hash" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$expected_hash" "$FILE.part" | shasum -a 256 -c -
printf 'RAW_DOCKER_ARCHIVE_VERIFIED; compressing locally\n'
gzip -1n < "$FILE.part" > "$FILE.gz.part"
gzip -t "$FILE.gz.part"
mv "$FILE.gz.part" "$FILE.gz"
shasum -a 256 "$FILE.gz" > "$FILE.gz.sha256"
# Only this script's verified intermediate is removed; r4 is retained.
rm "$FILE.part"
printf 'IMAGE_READY %s/%s.gz\n' "$DEST" "$FILE"
