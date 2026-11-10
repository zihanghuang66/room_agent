"""Isolated existing-app subprocesses and verified, reusable stage receipts."""
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .saved_scene import file_hash, tree_hashes, source_tree_hashes, legacy_root, engine_hash, capture_names, request_source_hashes
from .storage import atomic_json, read_json, digest
from .processes import run_process


class SceneExecutor:
    def __init__(self, request, directory, journal):
        self.request, self.directory, self.journal = request, Path(directory), journal
        self.work = self.directory / 'work'

    def verify(self):
        from .engine_compatibility import verify_engine
        verify_engine(self.request['engine_sha256'],engine_hash(),'saved_scene')
        for receipt in (self.directory / 'receipts').glob('*.json'):
            self._verify_receipt(read_json(receipt))
        if capture_names(self.request['capture']) != set(self.request['capture_files']):
            raise ValueError('Original capture integrity changed: frame set differs')
        for name, expected in self.request['capture_files'].items():
            p = Path(self.request['capture']) / name
            if not p.is_file() or file_hash(p) != expected:
                raise ValueError('Original capture integrity changed: ' + name)

    def _verify_receipt(self, receipt):
        for relative, data in receipt.get('trees', {}).items():
            root = self.directory / relative
            current = tree_hashes(root)
            current = {k: v for k, v in current.items() if k not in data['excluded']}
            if current != data['files']:
                raise ValueError('Stage directory integrity failed: ' + relative)
        for relative, sha in receipt['outputs'].items():
            p = self.directory / relative
            if not p.resolve().is_relative_to(self.directory.resolve()) or p.is_symlink() or not p.is_file() or file_hash(p) != sha:
                raise ValueError('Stage artifact integrity failed: ' + relative)

    def _outputs(self, paths):
        files = {}
        for root in map(Path, paths):
            if root.is_dir():
                files.update({str((root / rel).relative_to(self.directory)): sha for rel, sha in tree_hashes(root).items()})
            else:
                files[str(root.relative_to(self.directory))] = file_hash(root)
        return files

    def _trees(self, paths):
        trees = {}
        for root in map(Path, paths):
            if root.is_dir():
                # These are new geometry-node outputs, not modifications to the build's render assets.
                excluded = ['Room.portable.blend', 'Room.portable.blend1',
                            'texture_relocation.json', 'reopen_verification.json'] if root.name == 'room_preview' else []
                trees[str(root.relative_to(self.directory))] = {'excluded': excluded,
                    'files': {k: v for k, v in tree_hashes(root).items() if k not in excluded}}
        return trees

    def step(self, name, state):
        self.verify()
        number = 0 if name in ('snapshot', 'preflight') else state.get('round', 0)
        key = f'r{number:02d}_{name}'
        receipt_path = self.directory / 'receipts' / (key + '.json')
        # State includes source/build identities and previous outputs; receipt belongs to one exact node input.
        state_input = {k: v for k, v in state.items() if k not in ('execution_status', 'next_action')}
        fingerprint = digest({'request': self.request, 'stage': name, 'state': state_input})
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            if receipt['input_sha256'] != fingerprint:
                raise ValueError('Stage input fingerprint differs: ' + key)
            self._verify_receipt(receipt)
            self.journal.emit('artifact_reused', node=name, round=number)
            return receipt['result']
        if name == 'snapshot':
            result, paths = self._snapshot()
        else:
            result, paths = self._external(name, state, key)
        atomic_json(receipt_path, {'input_sha256': fingerprint, 'result': result,
                                  'outputs': self._outputs(paths), 'trees': self._trees(paths)})
        return result

    def _snapshot(self):
        target = self.work / 'initial'
        if target.exists():
            if (tree_hashes(target / 'room') != self.request['source_files'] or
                    tree_hashes(target / 'seed') != self.request['seed_files']):
                raise ValueError('Snapshot artifact integrity failed')
        else:
            needed = sum((Path(self.request[key]) / p).stat().st_size
                         for key in ('source', 'seed') for p in self.request[key + '_files'])
            if shutil.disk_usage(self.directory).free < needed + 512 * 1024**2:
                raise RuntimeError('Insufficient free disk space for immutable source snapshots')
            temp = self.work / ('initial.tmp-' + uuid4().hex)
            for key, leaf in [('source', 'room'), ('seed', 'seed')]:
                if request_source_hashes(self.request, key) != self.request[key + '_files']:
                    raise ValueError('Original saved input changed; create a new request')
                for relative in self.request[key + '_files']:
                    dest = temp / leaf / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(Path(self.request[key]) / relative, dest)
                if tree_hashes(temp / leaf) != self.request[key + '_files']:
                    raise ValueError('Snapshot integrity mismatch')
            temp.rename(target)
        return {'artifact_path': str(self.work), 'revision_id': self.request['run_id'],
                'active_source': str(target / 'room'), 'seed': str(target / 'seed')}, [target]

    def _external(self, name, state, key):
        attempts = self.work / 'attempts' / key
        attempts.mkdir(parents=True, exist_ok=True)
        # A killed editing session may have saved useful source. Reassess that copy; never silently re-edit it.
        if name == 'repair':
            saved = sorted(attempts.glob('*/scenes/*/realism_authoring/editing/batch_*/room/Room.py'))
            if saved:
                source = saved[-1].parent
                return {'active_source': str(source), 'repair_termination': 'interrupted'}, [source]
        attempt = attempts / uuid4().hex
        attempt.mkdir()
        payload = {'request': self.request, 'state': state, 'stage': name,
                   'attempt': str(attempt), 'legacy': str(legacy_root())}
        payload_path = attempt / 'request.json'
        atomic_json(payload_path, payload)
        python = self.request['app_python']
        if not Path(python).is_file():
            raise RuntimeError('Existing app Python missing: ' + python)
        driver = Path(__file__).with_name('scene_driver.py')
        log = self.directory / 'logs' / (key + '.log')
        log.parent.mkdir(exist_ok=True)
        seconds = self.request['repair_seconds'] + 120 if name == 'repair' else {'preflight': 180, 'build': 1800,
            'geometry': 1200, 'evidence': 900, 'render': 3600, 'review': 900}[name]
        env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        # Never pass the orchestration venv PYTHONPATH into the existing application environment.
        env.pop('PYTHONPATH', None)
        run_process([python, '-u', str(driver), str(payload_path)], log, seconds=seconds, env=env,
                    pass_fds=getattr(self, 'lock_fds', ()))
        result = read_json(attempt / 'stage_result.json')
        return result['state'], result['outputs']
