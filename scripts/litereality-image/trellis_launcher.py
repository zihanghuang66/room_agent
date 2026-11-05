"""Use the prepared pinned local pipeline instead of resolving model main again."""
import os
from pathlib import Path
import runpy
import sys

model = Path(os.environ['LITEREALITY_WEIGHTS']) / 'trellis2_4b_local'
if '--model' not in sys.argv:
    if not (model / 'pipeline.json').is_file():
        raise SystemExit('Pinned TRELLIS weights missing; run room-fetch-models first.')
    sys.argv.extend(['--model', str(model)])
runpy.run_module('litereality_agent.models.trellis.inference', run_name='__main__')
