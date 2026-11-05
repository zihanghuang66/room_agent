"""Install hash-identical NVIDIA wheels from the read-only build context."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

wheelhouse = Path('/opt/room/wheelhouse')
lock = Path('/opt/room/image/gpu-requirements.lock')
original = lock.read_text()
modified = original
receipt = json.loads((wheelhouse / 'receipt.json').read_text())
expected_names = {'nvidia-cudnn-cu12', 'nvidia-cublas-cu12', 'nvidia-cufft-cu12'}
assert {r['name'] for r in receipt} == expected_names
extra = json.loads((wheelhouse / 'extra-receipt.json').read_text())
assert {r['name'] for r in extra} == {'opencv-python-headless', 'scipy'}
receipt.extend(extra)
remaining = json.loads((wheelhouse / 'remaining-receipt.json').read_text())
assert not ({r['name'] for r in remaining} & {r['name'] for r in receipt})
receipt.extend(remaining)
assert len({r['name'] for r in receipt}) == len(receipt)
for item in receipt:
    filename = item['filename']
    assert Path(filename).name == filename and filename.endswith('.whl')
    wheel = wheelhouse / filename
    digest = hashlib.sha256()
    with wheel.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    assert digest.hexdigest() == item['sha256']
    logical_line = next(line for line in original.replace('\\\n', ' ').splitlines()
                        if line.startswith(item['name'] + '=='))
    assert 'sha256:' + item['sha256'] in logical_line
    pattern = r'(?m)^' + re.escape(item['name'] + '==' + item['version']) + r'(?=\s|$)'
    modified, count = re.subn(pattern, item['name'] + ' @ ' + wheel.as_uri(), modified)
    assert count == 1
try:
    lock.write_text(modified)
    subprocess.run(['python3.10', '/opt/room/image/install.py', 'gpu-python'], check=True)
finally:
    lock.write_text(original)
Path('/opt/room/build-info/gpu-wheel-sources.json').write_text(json.dumps(receipt, indent=2) + '\n')
