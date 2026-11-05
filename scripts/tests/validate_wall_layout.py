"""Replay saved real scene data locally, never mutate the returned evidence."""
import hashlib,json,os,shutil,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
app=Path(os.environ['ROOM_TEST_APP']).resolve();sys.path.insert(0,str(app/'src'))
from litereality_agent.pipeline.scene_init.layout import adapter
from litereality_agent.pipeline.scene_init.layout.stage import run_layout
from litereality_agent.pipeline.scene_init.layout.validation import require_valid_layout
from litereality_agent.pipeline.scene_init.layout.adjust import check
source=next((root/'return_output/environment_reports/room_004_20261105T012846Z').rglob('layout_validation.json')).parent
output=root/'return_output/environment_reports/wall_facade_v031'
output.mkdir(parents=True,exist_ok=True)
working=output/'actual_scene_data'
if working.exists():shutil.rmtree(working)
shutil.copytree(source,working)
before=adapter.shell_from_scene_data(working)['objects']
source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.pkl')}
result=run_layout('generic_replay',scene_data_dir=working,use_agent=False)
validation=require_valid_layout('generic_replay',result,scene_data_dir=working)
after=adapter.shell_from_scene_data(working)['objects']
assert before==after, 'The fix must not move/resize any saved object to clear a fabricated slab'
assert validation['passed'] and validation['error_count']==0
assert source_hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.pkl')}
report={'passed':True,'layout':validation,'all_saved_objects_unchanged':True,'source_untouched':True,
        'agent_calls':0,'no_raw_depth_or_full_reconstruction_tested':True}
(output/'actual_replay.json').write_text(json.dumps(report,indent=2)+'\n')
(output/'shell.json').write_text(json.dumps(adapter.shell_from_scene_data(working),indent=2)+'\n')
print('ACTUAL_LAYOUT_REPLAY_PASS',output)
