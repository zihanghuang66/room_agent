"""Build the pinned, isolated full-room upload package; no model/network calls."""
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
VERSION = '0.1.0'
BASE = SCRIPTS / 'vendor/litereality-agent-28ffe1f-room-codex-v0.1.2'


def archive(files):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', filename='', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith('.sh') else 0o644
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def build():
    files = {p.relative_to(BASE).as_posix(): p.read_bytes() for p in (BASE / 'src').rglob('*')
             if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}
    files.update({name: (BASE / name).read_bytes() for name in ('LICENSE', 'models.env', 'pyproject.toml', 'room_codex_patch.json')})
    changes = {}
    def replace(name, old, new):
        key = 'src/litereality_agent/' + name
        text = files[key].decode()
        if text.count(old) != 1:
            raise ValueError('Pinned patch context differs: ' + key)
        changes.setdefault(key, {}).setdefault('before', hashlib.sha256(files[key]).hexdigest())
        files[key] = text.replace(old, new).encode()
        changes[key]['after'] = hashlib.sha256(files[key]).hexdigest()

    replace('agent/providers/codex.py', '''            *(
                ["--sandbox", "read-only", "-c", 'approval_policy="never"']
                if spec.read_only
                else ["--sandbox", "workspace-write", "-c", 'approval_policy="never"']
            ),''', '''            # Authorized school-container boundary; no nested bwrap namespaces.
            "--sandbox", "danger-full-access", "-c", 'approval_policy="never"',''')
    replace('pipeline/scene_init/ingest/references/image_gen.py',
            '    sheet_path = Path(sheet_path)\n    out_path = Path(out_path)',
            '    from room_image_backend import generate_reference as codex_generate\n'
            '    return codex_generate(sheet_path, prompt, out_path, model=model, force=force, response_dir=response_dir)\n\n'
            '    sheet_path = Path(sheet_path)\n    out_path = Path(out_path)')
    for rel, old, new in [
        ('object_references.py', '                status = "error_fallback"',
         '                raise RuntimeError("Codex reference failed; placeholder disabled") from exc\n                status = "error_fallback"'),
        ('opening_references.py', '                        status, error = "error", str(exc)',
         '                        raise RuntimeError("Codex opening reference failed") from exc\n                        status, error = "error", str(exc)'),
        ('chair_clusters.py', '        cluster["reference_status"] = "error"',
         '        raise RuntimeError("Codex chair reference failed") from exc\n        cluster["reference_status"] = "error"'),
    ]:
        replace('pipeline/scene_init/ingest/references/' + rel, old, new)
    # A configured local interpreter must not be diverted to a stale Modal login.
    replace('models/registry.py', '    if settings.modal_configured():',
            '    if not settings.trellis_python and settings.modal_configured():')
    replace('pipeline/scene_init/flow.py',
            '    if not references_current:\n        args.force_image_generation = True',
            '    # Codex backend checks evidence+prompt hashes per reference, including partial resumes.\n'
            '    # A missing whole-stage stamp must not regenerate successful images.\n'
            '    if not references_current:\n        pass')
    replace('agent/providers/codex.py',
            '    env_vars = sorted(k for k in os.environ if k.startswith(("LITEREALITY_", "LR_")))',
            '    runtime_names = {"TMPDIR", "XDG_CACHE_HOME", "HF_HOME", "HUGGINGFACE_HUB_CACHE",\n'
            '                     "TORCH_HOME", "TORCH_EXTENSIONS_DIR", "TRITON_CACHE_DIR", "MPLCONFIGDIR",\n'
            '                     "CUDA_VISIBLE_DEVICES", "CUDA_HOME", "FLEX_GEMM_AUTOTUNE_CACHE_PATH",\n'
            '                     "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PYTHONDONTWRITEBYTECODE"}\n'
            '    env_vars = sorted(k for k in os.environ if k.startswith(("LITEREALITY_", "LR_")) or k in runtime_names)')
    for name, data in files.items():
        if name.endswith('.py'):
            ast.parse(data, filename=name)
    files['room_full_patch.json'] = json.dumps(dict(version=VERSION, base='room-codex-v0.1.2',
        upstream='28ffe1fd4a1b03c93f1877a33a8bc9b806f72fe1', changes=changes), indent=2).encode()
    payload = archive(files)
    (HERE / 'app.tar.gz').write_bytes(payload)
    (HERE / 'app.sha256').write_text(hashlib.sha256(payload).hexdigest() + '\n')
    for src, dst in [('litereality-noapi/common.py', 'common.py'),
                     ('codex-imagegen-check/probe.py', 'image_probe.py'),
                     ('litereality-image/pins.json', 'pins.json'),
                     ('litereality-image/check_extensions.py', 'check_extensions.py'),
                     ('codex-integration-check/blender_smoke.py', 'blender_smoke.py')]:
        (HERE / dst).write_bytes((SCRIPTS / src).read_bytes())
    names = ['app.tar.gz', 'app.sha256', 'common.py', 'image_probe.py', 'pins.json',
             'check_extensions.py', 'blender_smoke.py', 'room_image_backend.py',
             'fetch_weights.py', 'trellis_launcher.py', 'stage.py', 'preflight.py',
             'verify_export.py', 'run.py', 'run_room_002.sh', '使用说明.md']
    bundle = {f'room-litereality-full-v{VERSION}/' + n: (HERE / n).read_bytes() for n in names}
    manifest = {n: hashlib.sha256(data).hexdigest() for n, data in bundle.items()}
    bundle[f'room-litereality-full-v{VERSION}/package_manifest.json'] = json.dumps(manifest, indent=2).encode()
    output = SCRIPTS / f'packages/room-litereality-full-v{VERSION}.tar.gz'
    output.write_bytes(archive(bundle))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(digest + '  ' + output.name + '\n')
    print(output, digest, output.stat().st_size)


if __name__ == '__main__':
    build()
