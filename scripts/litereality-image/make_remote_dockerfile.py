"""Derive the independent-host recipe, preserving the published school recipe."""
import base64
import hashlib
import shlex
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = (HERE / 'Dockerfile').read_text()
old = '    apt-get update && apt-get install -y --no-install-recommends'
assert source.count(old) == 1
new = '''    sed -i -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \\
           -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' /etc/apt/sources.list && \\
    apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 update && apt-get install -y --no-install-recommends'''
result = source.replace(old, new)
gpu_old = 'RUN python3.10 /opt/room/image/install.py gpu-python'
assert result.count(gpu_old) == 1
installer = base64.b64encode((HERE / 'install_cached_gpu.py').read_bytes()).decode()
command = 'import base64; exec(base64.b64decode(' + repr(installer) + '))'
result = result.replace(gpu_old,
    'RUN --mount=type=cache,id=room-gpu-uv-v013,target=/opt/room/uv-build-cache,sharing=locked '
    '--mount=type=bind,from=gpuwheels,target=/opt/room/wheelhouse,ro '
    'UV_NO_CACHE=0 UV_CACHE_DIR=/opt/room/uv-build-cache UV_LINK_MODE=copy '
    'UV_CONCURRENT_DOWNLOADS=2 UV_OFFLINE=1 python3.10 -c ' + shlex.quote(command))
native_end = 'RUN python3.10 /opt/room/image/install.py o-voxel'
assert result.count(native_end) == 1
result = result.replace(native_end, native_end + '\n\n' +
    'RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 update && '
    'apt-get install -y --no-install-recommends bubblewrap && '
    'bwrap --version > /opt/room/build-info/bubblewrap-version.txt && '
    'rm -rf /var/lib/apt/lists/*')
old_cmd = 'CMD ["sleep", "infinity"]'
assert result.count(old_cmd) == 1
result = result.replace(old_cmd, 'CMD ["/usr/local/bin/room-sshd"]')
out = HERE / 'remote-build' 
out.mkdir(exist_ok=True)
(out / 'Dockerfile').write_text(result)
(out / '.dockerignore').write_text('**\n!Dockerfile\n')
(out / 'provenance.json').write_text(json.dumps({
    'source_recipe': 'v0.1.3',
    'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'remote_recipe_sha256': hashlib.sha256(result.encode()).hexdigest(),
    'changes': ['r3: start room-sshd by default; runtime host keys and public-key-only authentication retained', 'Install bubblewrap required by the standalone Codex binary; verified missing by sandbox probe', 'Ubuntu APT mirror changed to HTTPS TUNA; signature verification unchanged',
                'GPU dependency download concurrency limited to 2 after repeated retries',
                'Remaining uncached wheels prefetched from PyPI for CPython 3.10 / x86_64, all original hashes enforced',
                'OpenCV and SciPy prefetched from PyPI with hashes verified against the original lock',
                'Three NVIDIA wheels prefetched from PyPI with hashes verified against PyTorch and the lock',
                'BuildKit uv cache retained outside the image; hashes still required; installed files copied'],
    'actual_base': 'nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04@sha256:0a1cb6e7bd047a1067efe14efdf0276352d5ca643dfd77963dab1a4f05a003a4',
}, indent=2) + '\n')
print(out / 'Dockerfile')
