"""Pinned local model, single GPU worker, explicit first-run resolution."""
import os
from pathlib import Path
import runpy
import sys

model = Path(os.environ['LITEREALITY_WEIGHTS']) / 'trellis2_4b_local'
if not (model / 'pipeline.json').is_file():
    raise SystemExit('Pinned TRELLIS weights are not ready')
if '--model' not in sys.argv:
    sys.argv += ['--model', str(model)]
if '--pipeline-type' not in sys.argv:
    sys.argv += ['--pipeline-type', os.environ.get('ROOM_TRELLIS_RESOLUTION', '512')]
runpy.run_module('litereality_agent.models.trellis.inference', run_name='__main__')
