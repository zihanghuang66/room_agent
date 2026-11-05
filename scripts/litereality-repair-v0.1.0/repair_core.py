"""Standard-library guards for isolated, checkpointed repair and exact-build acceptance."""
import hashlib
import json
import math
from pathlib import Path
import shutil

SCENE='room_003_test_03'
TARGETS=('office_chairs','vanity_stool','vanity','bookcase','materials','layout_and_openings')


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def tree_hashes(root):
    root=Path(root)
    return {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and not any(x.startswith('.') for x in p.relative_to(root).parts)}


def step_done(state,name,room):
    rec=state.get('steps',{}).get(name,{})
    return rec.get('complete') is True and rec.get('source_after')==tree_hashes(room)


def build_fingerprint(room,preview):
    names=('Room.glb','Room.blend','Room.portable.blend','room_layout.json','manifest.json')
    return {'source':tree_hashes(room),'build':{n:digest(Path(preview)/n) for n in names}}


def review_pass(data):
    score=data.get('score')
    return (data.get('pass') is True and type(score) in (int,float) and math.isfinite(score)
        and score>=8 and data.get('issues')==[] and isinstance(data.get('targets'),dict)
        and all(data['targets'].get(k) is True for k in TARGETS))


def transient_network(text):
    return any(t in str(text).lower() for t in ('request timed out','routing discovery timed out',
        'connection reset','connection refused','address not available','stream disconnected',
        'temporary failure in name resolution','error sending request','websocket','http 502','http 503'))


def clone_tree(src,dst,old_root,new_root):
    """Independent copies, no hard links; reject links escaping the original run."""
    src,dst,old_root,new_root=map(Path,(src,dst,old_root,new_root))
    def copy_dir(s,d,ancestors):
        real=s.resolve()
        if not real.is_relative_to(old_root.resolve()) or real in ancestors:
            raise ValueError('Unsafe or cyclic scene link: '+str(s))
        d.mkdir(parents=True,exist_ok=False)
        for p in sorted(s.iterdir()):
            if p.name in ('__pycache__','.git','.codex'):continue
            if not p.resolve().is_relative_to(old_root.resolve()):raise ValueError('External scene link: '+str(p))
            target=d/p.name
            if p.is_dir():copy_dir(p,target,ancestors|{real})
            elif p.is_file():
                shutil.copy2(p,target)
                if p.resolve().suffix in ('.py','.json','.md','.txt'):
                    try:target.write_text(target.read_text().replace(str(old_root),str(new_root)))
                    except UnicodeDecodeError:pass
            else:raise ValueError('Broken scene entry: '+str(p))
    copy_dir(src,dst,set())


def identities(room):
    data=json.loads((Path(room)/'manifest.json').read_text())
    return {a['object']:{k:a.get(k) for k in ('source','kind','maps_to_prim','represents_prims')} for a in data['assets']}


def retained_assets(before,room):
    """Keep input neural meshes intact; wrappers may repair or replace bad geometry at build time."""
    room=Path(room)
    return (before['identities']==identities(room) and
        all((room/p).is_file() and digest(room/p)==h for p,h in before['neural_sources'].items()))
