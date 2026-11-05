"""Explicit, resumable weight download into the persistent project mount."""
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

from reports import archive, create, redact, save
from runtime import initialize


def main():
    folder = create('room_image_models')
    result = {'status': 'failed', 'snapshots': {}, 'inference_tested': False}
    try:
        initialize()
        from huggingface_hub import snapshot_download
        pins = json.loads(Path(__file__).with_name('pins.json').read_text())
        weights = Path(os.environ['LITEREALITY_WEIGHTS'])
        for name, item in pins['models'].items():
            print(f'Downloading {name}: {item["repo"]} @ {item["revision"]}', flush=True)
            path = Path(snapshot_download(item['repo'], revision=item['revision'],
                                          allow_patterns=item.get('allow_patterns')))
            result['snapshots'][name] = {'repo': item['repo'], 'revision': item['revision'], 'path': str(path)}
            link = weights / name
            if link.is_symlink():
                if link.resolve() != path.resolve():
                    raise RuntimeError(f'Existing {link} points to different weights; not replacing it')
            elif link.exists():
                raise RuntimeError(f'Existing model directory conflicts with pinned download: {link}')
            else:
                link.symlink_to(path)
            if 'model_sha256' in item:
                h = hashlib.sha256()
                with (path / 'model.safetensors').open('rb') as f:
                    for block in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(block)
                if h.hexdigest() != item['model_sha256']:
                    raise RuntimeError('DINOv3 model checksum mismatch')
        local = weights / 'trellis2_4b_local'
        local.mkdir(exist_ok=True)
        src = weights / 'trellis'
        config = json.loads((src / 'pipeline.json').read_text())
        config['args']['image_cond_model']['args']['model_name'] = str(weights / 'dinov3')
        checkpoint = local / 'ckpts'
        if checkpoint.is_symlink():
            if checkpoint.resolve() != (src / 'ckpts').resolve():
                raise RuntimeError('Existing TRELLIS checkpoint link differs; refusing to replace')
        elif checkpoint.exists():
            raise RuntimeError('Existing TRELLIS checkpoint directory conflicts')
        else:
            checkpoint.symlink_to(src / 'ckpts')
        # Only the local launcher config is changed; original downloaded model is untouched.
        save(local / 'pipeline.json', config)
        result['status'] = 'downloaded_not_inference_validated'
        save(weights / 'room-models-v0.1.0.json', result)
    except Exception:
        error = redact(traceback.format_exc())
        (folder / 'error.log').write_text(error)
        print(error, flush=True)
    finally:
        save(folder / 'result.json', result)
        archive(folder)
    return 0 if result['status'] == 'downloaded_not_inference_validated' else 1


if __name__ == '__main__':
    sys.exit(main())
