"""Build a versioned source-only bundle with deterministic member times."""
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

VERSION = '0.10.2'
NAME = 'room-agent-stage4b-v' + VERSION


def main():
    own = Path(__file__).resolve().parents[1]
    project = own.parent.parent
    legacy = own.parent / 'litereality-batch-review-v0.1.0'
    mapping = {}
    for path in own.rglob('*'):
        relative = path.relative_to(own)
        if (not path.is_file() or path.is_symlink() or any(p.startswith('.') or p == '__pycache__' or p.endswith('.egg-info') for p in relative.parts)
                or relative.parts[0] == 'delivery'):
            continue
        if path.suffix in ('.py', '.html', '.md', '.toml', '.lock') or relative.as_posix()=='engine_compatibility.json':
            mapping['room-agent/' + relative.as_posix()] = path
    for path in (own / 'delivery').iterdir():
        if path.name != 'build_delivery.py' and path.is_file():
            mapping[path.name] = path
    for path in legacy.rglob('*'):
        relative = path.relative_to(legacy)
        if (not path.is_file() or path.is_symlink() or '__pycache__' in relative.parts or relative.parts[0] == 'tests'):
            continue
        if relative.parts[0] == 'app' or path.suffix in ('.py', '.md', '.toml', '.env', '.sha256') or path.name == 'LICENSE':
            mapping['litereality-batch-review-v0.1.0/' + relative.as_posix()] = path
    # Full runner remains its pinned, self-contained legacy package; do not include weights/binaries.
    full = own.parent / 'litereality-full-v0.3.1'
    manifest_path = full / 'package_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name, expected in manifest.items():
        rel = Path(*Path(name).parts[1:])
        if not rel.parts or '..' in rel.parts:
            raise ValueError('Unsafe full backend manifest path')
        path = full / rel
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Build the pinned full backend first: ' + str(path))
        mapping['litereality-full-v0.3.1/' + rel.as_posix()] = path
    mapping['litereality-full-v0.3.1/package_manifest.json'] = manifest_path
    files = {name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in sorted(mapping.items())}
    manifest = json.dumps({'version': VERSION, 'files': files}, indent=2).encode()
    output = project / 'return_output' / (NAME + '.tar.gz')
    output.parent.mkdir(exist_ok=True)
    import io
    with output.open('wb') as raw, gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name, p in sorted(mapping.items()):
                info = tarfile.TarInfo(NAME + '/' + name)
                info.size, info.mode, info.mtime = p.stat().st_size, 0o755 if p.suffix == '.sh' else 0o644, 0
                with p.open('rb') as stream:
                    archive.addfile(info, stream)
            info = tarfile.TarInfo(NAME + '/release_manifest.json')
            info.size, info.mode, info.mtime = len(manifest), 0o644, 0
            archive.addfile(info, io.BytesIO(manifest))
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(checksum + '  ' + output.name + '\n')
    print(output)
    print(json.dumps({'files': len(mapping)+1, 'bytes': output.stat().st_size, 'sha256': checksum}))


if __name__ == '__main__':
    main()
