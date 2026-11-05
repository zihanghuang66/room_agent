"""Fetch three unchanged NVIDIA wheels from PyPI, comparing both official hashes."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

from download_artifacts import download

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / 'return_output/gpu-wheelhouse'
OUT.mkdir(parents=True, exist_ok=True)
lock = (HERE / 'gpu-requirements.lock').read_text().replace('\\\n', ' ')


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href')


items = []
for name in ('nvidia-cudnn-cu12', 'nvidia-cublas-cu12', 'nvidia-cufft-cu12'):
    line = next(s for s in lock.splitlines() if s.startswith(name + '=='))
    version = line.split('==', 1)[1].split()[0]
    hashes = re.findall(r'sha256:([0-9a-f]{64})', line)
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as r:
        metadata = json.load(r)
    choices = [x for x in metadata['urls'] if x['filename'].endswith('_x86_64.whl') and 'manylinux' in x['filename']]
    assert len(choices) == 1, choices
    item = choices[0]
    expected = item['digests']['sha256']
    assert expected in hashes, f'{name}: PyPI hash differs from the existing lock'
    index = f'https://download.pytorch.org/whl/cu124/{name}/'
    with urllib.request.urlopen(index, timeout=60) as r:
        parser = Links()
        parser.feed(r.read().decode())
    matches = [u for u in parser.links if urllib.parse.unquote(urllib.parse.urlsplit(u).path).endswith('/' + item['filename'])]
    assert matches, f'{name}: no identical filename on the PyTorch index'
    assert any(urllib.parse.parse_qs(urllib.parse.urlsplit(u).fragment).get('sha256') == [expected] for u in matches), f'{name}: PyTorch and PyPI bytes differ'
    items.append({'name': name, 'version': version, 'filename': item['filename'],
                  'url': item['url'], 'sha256': expected, 'size': item['size'],
                  'pytorch_index': index, 'same_hash_on_both_official_sources': True})
    print('MATCHING_OFFICIAL_HASH', name, expected, flush=True)

with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [pool.submit(download, item, OUT / item['filename']) for item in items]
    for future in futures:
        future.result()
(OUT / 'receipt.json').write_text(json.dumps(items, indent=2) + '\n')
print('VERIFIED_WHEELHOUSE_READY', OUT, flush=True)
