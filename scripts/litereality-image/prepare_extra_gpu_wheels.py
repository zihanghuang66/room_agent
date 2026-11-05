"""Prefetch locked Python 3.10 Linux wheels from their official PyPI releases."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import urllib.request
from download_artifacts import download

here = Path(__file__).resolve().parent
out = here.parent / 'return_output/gpu-wheelhouse'
lock = (here / 'gpu-requirements.lock').read_text().replace('\\\n', ' ')
items = []
for name, tag in [('opencv-python-headless', 'cp37-abi3'), ('scipy', 'cp310-cp310')]:
    line = next(s for s in lock.splitlines() if s.startswith(name + '=='))
    version = line.split('==', 1)[1].split()[0]
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as r:
        data = json.load(r)
    choices = [x for x in data['urls'] if tag in x['filename'] and 'manylinux' in x['filename'] and x['filename'].endswith('x86_64.whl')]
    assert len(choices) == 1
    item = choices[0]
    digest = item['digests']['sha256']
    assert digest in re.findall(r'sha256:([0-9a-f]{64})', line)
    items.append({'name':name,'version':version,'filename':item['filename'], 'url':item['url'], 'sha256':digest, 'size':item['size'], 'hash_verified_against_original_lock':True})
with ThreadPoolExecutor(max_workers=2) as pool:
    for future in [pool.submit(download, item, out / item['filename']) for item in items]:
        future.result()
(out / 'extra-receipt.json').write_text(json.dumps(items,indent=2)+'\n')
print('EXTRA_WHEELS_READY',flush=True)
