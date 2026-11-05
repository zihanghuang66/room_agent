#!/usr/bin/env bash
# Run only on the authorized Ubuntu Docker test host; no user login is mounted.
set -Eeuo pipefail
BASE=/home/huang/room-image-build/return_output
CONTAINER=room-codex-seal-validation-v010
IMAGE=room-litereality-agent:codex-seal-validation-v0.1.0
docker run --name "$CONTAINER" --network none -v "$BASE:/test" \
  --entrypoint /bin/bash room-litereality-agent:0.1.3-cuda124-r4 -lc '
set -euo pipefail
mkdir -p /test/codex-seal-validation-20261103/project/scripts
tar -xzf /test/room-codex-image-seal-v0.1.0.tar.gz -C /test/codex-seal-validation-20261103/project/scripts
ln -s /test/full-validation-final-20261102/room-litereality-full-v0.1.0 /test/codex-seal-validation-20261103/project/scripts/room-litereality-full-v0.1.0
ln -s /test/codex-runtime-repair-20261103/room-litereality-resume-v0.1.1 /test/codex-seal-validation-20261103/project/scripts/room-litereality-resume-v0.1.1
bash /test/codex-seal-validation-20261103/project/scripts/room-codex-image-seal-v0.1.0/install.sh --project-root /test/codex-seal-validation-20261103/project --offline-only
/usr/local/bin/room-codex-runtime-check
# An offline-only install must not be allowed to stop SSH or authorize a school snapshot.
if bash /test/codex-seal-validation-20261103/project/scripts/room-codex-image-seal-v0.1.0/prepare_save.sh > /test/codex-seal-validation-20261103/prepare-gate.log 2>&1; then
  echo "ERROR: offline install passed the real-execution gate"; exit 1
fi
grep -q "real execution verification must pass first" /test/codex-seal-validation-20261103/prepare-gate.log
'
docker commit "$CONTAINER" "$IMAGE"
# No /test or /yinxin bind mount is provided to this fresh container.
docker run --rm --network none --entrypoint /bin/bash "$IMAGE" -lc '
set -euo pipefail
test ! -e /test/codex-seal-validation-20261103/project/scripts/room-litereality-resume-v0.1.1
/usr/local/bin/room-codex-runtime-check
command -v codex
codex --version
source /opt/room/image/environment.sh
test "$ROOM_CODEX" = /opt/room/tools/codex-complete-0.164.0/bin/codex
test "$(command -v codex)" = /opt/room/tools/codex-complete-0.164.0/bin/codex
/opt/room/image/ssh-prepare.sh
/usr/sbin/sshd -t
service ssh start
/opt/room/envs/app/bin/python -c "import socket; s=socket.create_connection((\"127.0.0.1\",22),timeout=5); s.settimeout(5); banner=s.recv(128); assert banner.startswith(b\"SSH-2.0-\"),banner; print(\"LOCAL_SSH_BANNER_PASS\")"
echo FRESH_IMAGE_WITHOUT_PROJECT_MOUNT_PASS
'
