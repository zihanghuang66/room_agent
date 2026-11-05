"""Build-node base/network diagnosis. No packages, credentials or GPU required."""
import concurrent.futures
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys
import urllib.request


def command(args):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=15, stdin=subprocess.DEVNULL)
        return {'returncode': p.returncode, 'output': p.stdout[-2500:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'error_type': type(exc).__name__}


def endpoint(item):
    name, url = item
    try:
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'room-base-probe/0.1.0'})
        with urllib.request.urlopen(req, timeout=12, context=ssl.create_default_context()) as response:
            return name, {'https_ok': True, 'status': response.status}
    except Exception as exc:
        # No request headers, proxy URLs or environment contents in the report.
        reason = getattr(exc, 'reason', exc)
        data = {'https_ok': False, 'error_type': type(exc).__name__, 'reason_type': type(reason).__name__}
        if isinstance(reason, ssl.SSLCertVerificationError):
            data['certificate_error'] = reason.verify_message
        elif getattr(exc, 'code', None):
            data['http_status'] = exc.code
        return name, data


def collect():
    result = {'probe_only': True, 'litereality_installed': False, 'gpu_tested': False,
              'architecture': platform.machine(), 'glibc': platform.libc_ver(),
              'python': sys.version, 'python_executable': sys.executable,
              'os_release': Path('/etc/os-release').read_text() if Path('/etc/os-release').is_file() else None}
    result['commands'] = {name: command([name, '--version']) if shutil.which(name) else {'missing': True}
                          for name in ('nvcc', 'gcc', 'g++', 'cmake', 'python3', 'curl', 'git')}
    result['python_candidates'] = {p: command([p, '--version']) for p in
                                   ('/usr/bin/python3', '/usr/bin/python3.10', '/opt/conda/bin/python') if Path(p).is_file()}
    result['python_packages'] = {}
    for name in ('torch', 'torchvision', 'numpy', 'pip'):
        try:
            result['python_packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['python_packages'][name] = None
    result['cuda_paths'] = {str(p): str(p.resolve()) for p in Path('/usr/local').glob('cuda*')}
    result['download_tools'] = {name: bool(shutil.which(name)) for name in ('apt-get', 'wget', 'curl')}
    # HEAD only: verify certificates and reachability without downloading archives/models.
    endpoints = {
        'github_api': 'https://api.github.com/repos/LiteReality/LiteReality-Agent',
        'github_codeload': 'https://codeload.github.com/LiteReality/LiteReality-Agent/tar.gz/28ffe1fd4a1b03c93f1877a33a8bc9b806f72fe1',
        'pypi': 'https://pypi.org/simple/uv/',
        'pytorch_wheels': 'https://download.pytorch.org/whl/cu124/torch/',
        'blender': 'https://download.blender.org/release/Blender5.1/blender-5.1.2-linux-x64.tar.xz',
        'nodejs': 'https://nodejs.org/dist/v22.23.4/SHASUMS256.txt',
        'nvidia_toolkit': 'https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/Packages.gz',
        'huggingface': 'https://huggingface.co/api/models/microsoft/TRELLIS.2-4B',
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        result['https_endpoints'] = dict(pool.map(endpoint, endpoints.items()))
    result['note'] = 'Successful probe build is not a usable LiteReality image. HEAD success does not prove large downloads succeed.'
    return result


if __name__ == '__main__':
    report = collect()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path('/opt/room-base-probe/report.json').write_text(text + '\n')
    print('ROOM_BASE_PROBE_BEGIN\n' + text + '\nROOM_BASE_PROBE_END', flush=True)
