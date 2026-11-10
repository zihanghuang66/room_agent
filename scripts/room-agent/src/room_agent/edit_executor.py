"""Child revision execution reuses build/render adapters, never reconstruction."""
import os
from pathlib import Path
from .scene_executor import SceneExecutor
from .saved_scene import request_source_hashes, legacy_root
from .storage import atomic_json, read_json, digest
from .processes import run_process


class EditExecutor(SceneExecutor):
    def verify(self):
        super().verify()
        # Parent edits must never leak into a child through assets or absolute references.
        for key in ('source','seed'):
            if request_source_hashes(self.request, key) != self.request[key+'_files']:
                raise ValueError('Parent scene integrity changed; original version must remain immutable')

    def _external(self,name,state,key):
        if name in ('baseline_build','baseline_render'):
            translated=dict(state)
            if name=='baseline_render':
                for field in ('room','preview','round_dir','build_stamp'):
                    translated[field]=state['baseline_'+field]
            result,paths=super()._external('build' if name=='baseline_build' else 'render',translated,key)
            if name=='baseline_render':paths=[Path(result['compare'])]
            return {'baseline_'+k:v for k,v in result.items()},paths
        if name in ('preflight','build','geometry','render'):
            result,paths=super()._external(name,state,key)
            if name=='render':paths=[Path(result['compare'])]
            return result,paths
        # Stable attempts permit a review group checkpoint or consumed edit to survive driver termination.
        attempt=self.work/'attempts'/key/'stable'
        attempt.mkdir(parents=True,exist_ok=True)
        payload={'request':self.request,'state':state,'stage':name,'attempt':str(attempt),'legacy':str(legacy_root())}
        path=attempt/'request.json'
        previous = read_json(path) if path.exists() else payload
        identity = lambda value: {k:v for k,v in value.items() if k != 'legacy'}
        # Engine compatibility verifies released code separately. Its installation path
        # is not scene input and necessarily changes when extracting a patch release.
        if identity(previous) != identity(payload):raise ValueError('Edit stage input identity changed')
        completed=attempt/'stage_result.json'
        receipt=attempt/'completed.json'
        if completed.exists() and receipt.exists():
            saved=read_json(receipt)
            if saved['input_sha256'] not in (digest(identity(payload)), digest(previous)):raise ValueError('Edit stage completion identity changed')
            self._verify_receipt(saved)
            result=read_json(completed)
            return result['state'],result['outputs']
        atomic_json(path,payload)
        env=dict(os.environ,PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1');env.pop('PYTHONPATH',None)
        driver=Path(__file__).with_name('scene_driver.py')
        run_process([self.request['app_python'],'-u',str(driver),str(path)],self.directory/'logs'/(key+'.log'),
                    seconds={'inspect_current':420,'edit':1020,'evidence':900,'review':14400,'finalize':180}[name],env=env,
                    pass_fds=getattr(self,'lock_fds',()))
        result=read_json(completed)
        atomic_json(receipt,dict(input_sha256=digest(identity(payload)),outputs=self._outputs([completed,*result['outputs']]),trees=self._trees(result['outputs'])))
        return result['state'],result['outputs']
