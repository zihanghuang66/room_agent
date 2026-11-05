"""Versioned overlay: retain the audited original app; include official offline CLI."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
spec = importlib.util.spec_from_file_location('original_builder', SCRIPTS / 'litereality-full/build.py')
original = importlib.util.module_from_spec(spec); spec.loader.exec_module(original)
RUNTIME_SHA = '3ec55053109608d5038f7e50750f7d25f768dfd740fef071aa710e2559b63aac'


def main():
    payload = SCRIPTS / 'litereality-full/app.tar.gz'
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == '29d3b7ccbf7b3f673b327b35883cb2fd70350bcf4480975b9a8005587b776aa5'
    with tarfile.open(payload) as t:
        files = {x.name: t.extractfile(x).read() for x in t if x.isfile()}
    key = 'src/litereality_agent/agent/providers/codex.py'
    text = files[key].decode()
    replacements = [
        ('        if shutil.which("codex") is None:',
         '        executable = os.environ.get("ROOM_CODEX") or shutil.which("codex")\n'
         '        if executable is None:'),
        ('            "codex",\n            "exec",', '            executable,\n            "exec",'),
        ('    runtime_names = {"TMPDIR",', '    runtime_names = {"ROOM_CODEX", "PATH", "TMPDIR",'),
        ('                item = event.get("item") or {}',
         '                item = event.get("item") or {}\n'
         '                # A completed error ITEM is not a successful tool/session.\n'
         '                # In 0.164.0 a missing Code Mode host appears here before turn.started.\n'
         '                if event.get("type") == "item.completed" and item.get("type") == "error":\n'
         '                    stream_errors.append(event)\n'
         '                    terminal_error = pending_error = True\n'
         '                    stopped = "Codex runtime error: " + str(item.get("message") or item)[:2000]\n'
         '                    spec.log(stopped)\n'
         '                    yield AgentMessage(content=[TextBlock(text=stopped)], raw=event)\n'
         '                    break'),
    ]
    for old, new in replacements:
        if text.count(old) != 1:
            raise ValueError('Pinned adapter context changed: ' + old)
        text = text.replace(old, new)
    ast.parse(text); files[key] = text.encode()
    files['room_resume_patch.json'] = json.dumps(dict(version='0.1.1',
        base_app_sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
        modified={key: hashlib.sha256(files[key]).hexdigest()}), indent=2).encode()
    app = original.archive(files)
    (HERE / 'app.tar.gz').write_bytes(app)
    runtime = SCRIPTS.parent / 'return_output/artifacts/codex_runtime_repair_20261103/codex-package-x86_64-unknown-linux-musl.tar.gz'
    data = runtime.read_bytes()
    assert hashlib.sha256(data).hexdigest() == RUNTIME_SHA
    pins = dict(app_sha256=hashlib.sha256(app).hexdigest(), runtime_sha256=RUNTIME_SHA,
        runtime_version='0.164.0', runtime_target='x86_64-unknown-linux-musl',
        runtime_url='https://github.com/openai/codex/releases/download/rust-v0.164.0/codex-package-x86_64-unknown-linux-musl.tar.gz')
    (HERE / 'repair_pins.json').write_text(json.dumps(pins, indent=2) + '\n')
    names = ['capture_paths.py', 'refresh_seed.py', 'resume.py', 'run_room_002.sh',
             'runtime.py', 'execution_smoke.py', 'mcp_probe.py', 'repair_pins.json', 'app.tar.gz', '使用说明.md']
    bundle = {n: (HERE / n).read_bytes() for n in names}
    bundle['codex-runtime.tar.gz'] = data
    bundle['package_manifest.json'] = json.dumps({n: hashlib.sha256(d).hexdigest()
        for n, d in bundle.items()}, indent=2).encode()
    output = SCRIPTS / 'packages/room-litereality-resume-v0.1.1.tar.gz'
    output.write_bytes(original.archive({'room-litereality-resume-v0.1.1/' + n: d for n, d in bundle.items()}))
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(sha + '  ' + output.name + '\n')
    print(output, output.stat().st_size, sha)


if __name__ == '__main__':
    main()
