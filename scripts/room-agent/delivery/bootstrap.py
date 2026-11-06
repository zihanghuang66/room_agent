"""Standard-library bootstrap: verify code bundle and report setup failures."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import tarfile
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['verify', 'report'])
    parser.add_argument('path', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--status', default='unknown')
    args = parser.parse_args()
    if args.mode == 'verify':
        manifest = json.loads((args.path / 'release_manifest.json').read_text())
        for relative, expected in manifest['files'].items():
            p = args.path / relative
            if p.is_symlink() or not p.is_file() or sha(p) != expected:
                raise RuntimeError('Package file checksum mismatch: ' + relative)
        print('PACKAGE_CHECK_PASS ' + manifest['version'])
        return
    args.path.mkdir(parents=True, exist_ok=True)
    (args.path / 'bootstrap_result.json').write_text(json.dumps({'status': args.status,
        'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'note': 'Bootstrap/runtime service diagnostics, not scene quality acceptance'}, indent=2))
    target = args.output / (args.path.name + '.tar.gz')
    with target.open('wb') as raw, gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for p in sorted(args.path.iterdir()):
                if not p.is_file() or p.is_symlink() or p.suffix not in ('.json', '.txt', '.log'):
                    continue
                info = tarfile.TarInfo(args.path.name + '/' + p.name)
                info.size, info.mode, info.mtime = p.stat().st_size, 0o644, 0
                with p.open('rb') as stream:
                    archive.addfile(info, stream)
    print('RETURN_TAR: ' + str(target), flush=True)


if __name__ == '__main__':
    main()
