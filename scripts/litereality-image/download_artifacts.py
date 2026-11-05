"""HTTPS artifact download with fixed hashes and bounded, explicit mirror fallback."""
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def download(item, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = item['sha256']
    urls = list(dict.fromkeys([item['url']] + item.get('fallback_urls', [])))
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Artifact sources must be credential-free HTTPS URLs')
    if target.is_file() and digest(target) == expected:
        print(f'[download] verified existing artifact: {target.name}', flush=True)
        return
    part = target.with_name(target.name + '.part')
    attempts = []
    for url in urls:
        part.unlink(missing_ok=True)
        print(f'[download] {target.name} source={url}', flush=True)
        cmd = ['curl', '--fail', '--location', '--silent', '--show-error',
               '--proto', '=https', '--proto-redir', '=https',
               '--retry', '3', '--retry-max-time', '1800', '--connect-timeout', '30',
               '--max-time', '1800', '--output', str(part), '--write-out', '%{http_code}', url]
        try:
            done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, timeout=1900, check=False)
            attempts.append({'url': url, 'curl_exit': done.returncode, 'http_status': done.stdout.strip()})
        except subprocess.TimeoutExpired:
            part.unlink(missing_ok=True)
            attempts.append({'url': url, 'error': 'download_timeout'})
            print('[download] timeout; trying next configured source', flush=True)
            continue
        if done.returncode != 0:
            part.unlink(missing_ok=True)
            print('[download] source failed: ' + json.dumps(attempts[-1]), flush=True)
            print(done.stderr[-1200:], flush=True)
            continue
        if not part.is_file():
            raise RuntimeError('curl reported success but produced no artifact')
        actual = digest(part)
        if actual != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError(f'Checksum mismatch for {target.name}: expected={expected}, actual={actual}; refusing extraction')
        part.replace(target)
        print(f'[download] SHA256 OK: {target.name} {actual}', flush=True)
        return
    raise RuntimeError('All configured artifact sources failed: ' + json.dumps(attempts) +
                       '; download failed before extraction. TLS verification was not disabled.')
