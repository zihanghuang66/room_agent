"""Recover the verified cuDNN wheel using bounded HTTPS range requests."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

root = Path(__file__).resolve().parent.parent / 'return_output/gpu-wheelhouse'
parser = argparse.ArgumentParser()
parser.add_argument('--package', choices=['cudnn', 'opencv'], default='cudnn')
args = parser.parse_args()
if args.package == 'cudnn':
    name, version = 'nvidia-cudnn-cu12', '9.1.0.70'
    filename = 'nvidia_cudnn_cu12-9.1.0.70-py3-none-manylinux2014_x86_64.whl'
    expected = '165764f44ef8c61fcdfdfdbe769d687e06374059fbb388b6c89ecb0e28793a6f'
else:
    name, version = 'opencv-python-headless', '4.11.0.86'
    filename = 'opencv_python_headless-4.11.0.86-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
    expected = '0e0a27c19dd1f40ddff94976cfe43066fbbe9dfbb2ec1907d66c19caef42a57b'
with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
    metadata = json.load(response)
item = next(x for x in metadata['urls'] if x['filename'] == filename)
assert item['digests']['sha256'] == expected
parts = root / (args.package + '-parts')
parts.mkdir(exist_ok=True)
size = item['size']
chunk = 8 * 1024 * 1024

def fetch(start):
    end = min(start + chunk, size) - 1
    path = parts / str(start)
    if path.exists() and path.stat().st_size == end-start+1:
        return start
    for attempt in range(3):
        temp = path.with_suffix('.part')
        result = subprocess.run(['curl','--fail','--silent','--show-error','--location',
            '--proto','=https','--proto-redir','=https','--range',f'{start}-{end}',
            '--connect-timeout','20','--max-time','120','--output',str(temp),
            '--write-out','%{http_code}', item['url']], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout == '206' and temp.stat().st_size == end-start+1:
            temp.replace(path)
            return start
        print('RETRY', start, attempt, result.returncode, result.stderr[-200:], flush=True)
    raise RuntimeError(f'Range failed: {start}')

starts = list(range(0, size, chunk))
with ThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(fetch, s) for s in starts]
    for count, future in enumerate(as_completed(futures), 1):
        future.result()
        print('RANGE_OK', count, len(starts), flush=True)
target = root / item['filename']
combined = target.with_suffix('.combined')
digest = hashlib.sha256()
with combined.open('wb') as output:
    for start in starts:
        data = (parts / str(start)).read_bytes()
        digest.update(data)
        output.write(data)
assert combined.stat().st_size == size and digest.hexdigest() == expected
combined.replace(target)
print('FULL_SHA256_OK', expected, flush=True)
