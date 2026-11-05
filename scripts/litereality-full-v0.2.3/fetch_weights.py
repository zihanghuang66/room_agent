"""Resume downloads of fixed revisions; use the existing persistent HF cache."""
import json
import os
from pathlib import Path
from huggingface_hub import snapshot_download
from common import digest, save

weights = Path(os.environ['LITEREALITY_WEIGHTS'])
weights.mkdir(parents=True, exist_ok=True)
pins = json.loads(Path(__file__).with_name('pins.json').read_text())['models']
result = {}
for name, item in pins.items():
    print('MODEL ' + name + ' @ ' + item['revision'], flush=True)
    target = Path(snapshot_download(item['repo'], revision=item['revision'], max_workers=2))
    link = weights / name
    if link.is_symlink():
        if link.resolve() != target.resolve():
            raise RuntimeError('Existing weights differ from the pinned revision: ' + str(link))
    elif link.exists():
        raise RuntimeError('Refusing to replace existing model directory: ' + str(link))
    else:
        link.symlink_to(target)
    if item.get('model_sha256') and digest(target / 'model.safetensors') != item['model_sha256']:
        raise RuntimeError('DINOv3 checkpoint SHA256 mismatch')
    result[name] = dict(repo=item['repo'], revision=item['revision'], snapshot=str(target))
local = weights / 'trellis2_4b_local'
local.mkdir(exist_ok=True)
config = json.loads((weights / 'trellis/pipeline.json').read_text())
config['args']['image_cond_model']['args']['model_name'] = str(weights / 'dinov3')
checkpoint = local / 'ckpts'
if checkpoint.is_symlink():
    if checkpoint.resolve() != (weights / 'trellis/ckpts').resolve():
        raise RuntimeError('Conflicting TRELLIS checkpoint link')
elif checkpoint.exists():
    raise RuntimeError('Refusing to replace existing TRELLIS checkpoint directory')
else:
    checkpoint.symlink_to(weights / 'trellis/ckpts')
save(local / 'pipeline.json', config)
save(Path(os.environ['ROOM_RUN']) / 'weights.json', dict(status='downloaded', models=result,
    inference_tested=False))
print('PINNED_WEIGHTS_READY', flush=True)
