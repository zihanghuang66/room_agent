"""Build the minimal Blender/acceptance continuation; reuse the verified v0.1.1 CLI."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
spec = importlib.util.spec_from_file_location('archive_builder', SCRIPTS / 'litereality-full/build.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


def main():
    base = SCRIPTS / 'litereality-resume-v0.1.1'
    pins = json.loads((base / 'repair_pins.json').read_text())
    payload = base / 'app.tar.gz'
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == pins['app_sha256']
    with tarfile.open(payload) as t:
        files = {m.name: t.extractfile(m).read() for m in t if m.isfile()}
    changes = {}
    def replace(name, old, new):
        key = 'src/litereality_agent/' + name
        text = files[key].decode()
        if text.count(old) != 1: raise RuntimeError('Pinned patch context changed: ' + name)
        changes.setdefault(key, {})['before'] = hashlib.sha256(files[key]).hexdigest()
        files[key] = text.replace(old, new).encode()
        changes[key]['after'] = hashlib.sha256(files[key]).hexdigest()
    resolver = '    from litereality_agent.room_ops.paths import find_blender\n    return find_blender()'
    replace('room_ops/rendering/room_render/render_vs_capture.py',
        '    d = os.environ.get("LITEREALITY_BLENDER", "")\n    b = Path(d) / "blender" if d else Path("blender")\n    return str(b)', resolver)
    replace('agent/author.py',
        '    d = os.environ.get("LITEREALITY_BLENDER", "")\n    if d:\n        return str(Path(d) / "blender")\n    return shutil.which("blender") or "blender"', resolver)
    replace('pipeline/scene_init/reconstruct/flow.py',
        '    if bdir:\n        env["PATH"] = f"{bdir}:{env.get(\'PATH\', \'\')}"',
        '    if bdir:\n        bdir = str(Path(bdir).parent) if Path(bdir).is_file() else bdir\n        env["PATH"] = f"{bdir}:{env.get(\'PATH\', \'\')}"')
    replace('room_ops/rendering/room_render/render_vs_capture.py',
        '    cmd = [_blender_bin(), "-b", "--python", str(scr), "--",',
        '    cmd = [_blender_bin(), "-b", "-t", "4", "--python-exit-code", "1", "--python", str(scr), "--",')
    replace('room_ops/rendering/room_render/render_vs_capture.py',
        '    pairs = _make_pairs(a.scan, render_dir, pairs_dir)\n',
        '    pairs = _make_pairs(a.scan, render_dir, pairs_dir)\n'
        '    if not pairs:\n        raise SystemExit("[rvc] no successful render/capture pairs; refusing an empty success")\n')
    replace('pipeline/realism_authoring/acceptance.py',
        '    except Exception as exc:\n        report["error"] = f"{type(exc).__name__}: {exc}"',
        '    except Exception as exc:\n        report["status"] = "check_failed"\n        report["error"] = f"{type(exc).__name__}: {exc}"')
    replace('pipeline/realism_authoring/acceptance.py',
        '        if report["accepted"] or number == rounds or rc:',
        '        if report["accepted"] or number == rounds or rc or report.get("status") == "check_failed":')
    for n,b in files.items():
        if n.endswith('.py'): ast.parse(b, n)
    files['room_resume_v012_patch.json'] = json.dumps(dict(version='0.1.2', changes=changes),indent=2).encode()
    app = builder.archive(files); (HERE / 'app.tar.gz').write_bytes(app)
    pins['base_app_sha256'] = pins['app_sha256']; pins['app_sha256'] = hashlib.sha256(app).hexdigest()
    (HERE / 'repair_pins.json').write_text(json.dumps(pins,indent=2))
    names = ['app.tar.gz','repair_pins.json','capture_paths.py','refresh_seed.py','runtime.py',
             'resume.py','finish_stage.py','blender_paths.py','run_room_002.sh','使用说明.md']
    bundle = {n:(HERE/n).read_bytes() for n in names}
    bundle['package_manifest.json'] = json.dumps({n:hashlib.sha256(b).hexdigest() for n,b in bundle.items()},indent=2).encode()
    target = SCRIPTS/'packages/room-litereality-resume-v0.1.2.tar.gz'
    target.write_bytes(builder.archive({'room-litereality-resume-v0.1.2/'+n:b for n,b in bundle.items()}))
    h=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name+'.sha256').write_text(h+'  '+target.name+'\n')
    print(target,target.stat().st_size,h)


if __name__ == '__main__': main()
