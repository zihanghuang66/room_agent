"""Resume only final acceptance, preserving the completed author/material/QC work."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

from litereality_agent.pipeline.context import RunContext
from litereality_agent.pipeline.result import StageResult, StageStatus
from litereality_agent.pipeline.runner import PipelineRunner
from litereality_agent.pipeline.stages import STAGES
from litereality_agent.pipeline.realism_authoring.acceptance import finish


def resume_acceptance(context, options):
    previous = json.loads(context.state_path.read_text())['stages']['author']
    details = previous.get('details', {})
    # Reaching the old acceptance function proves its preceding required passes returned success.
    if details.get('session', {}).get('session_ok') is not True or not details.get('acceptance'):
        raise RuntimeError('Cannot skip authoring: previous run did not reach acceptance after successful author/polish passes')
    report = finish(context, options)
    result = StageResult('author', StageStatus.COMPLETED if report.get('accepted') else StageStatus.FAILED,
        artifacts={'room': str(context.authored_room),
                   'acceptance': str(context.preview_dir / 'author_acceptance.json')},
        details={'session': details['session'], 'acceptance': report,
                 'resumed_acceptance_only': True, 'previous_polish_passes_retained': True})
    if not report.get('accepted'):
        result.error = ('check execution failed: ' if report.get('status') == 'check_failed'
                        else 'needs_repair: final support/visual acceptance not passed; current source retained; ') + str(report.get('error', 'see review'))
    return result


def main():
    capture, output = map(Path, sys.argv[1:])
    context = RunContext(scan='room_002', capture_dir=capture,
                         scene_dir=output/'room_002', output_root=output)
    stages = tuple(replace(stage, run=resume_acceptance) if stage.name == 'author' else stage for stage in STAGES)
    result = PipelineRunner(stages=stages).run_stage(context, 'author', strict=True,
        options=dict(provider='codex', resume_acceptance=True, repair_rounds=2, repair_steps=40, repair_seconds=600))
    out = Path(os.environ['ROOM_RUN']) / 'stage_author.json'
    out.write_text(json.dumps(result.to_dict(), indent=2))
    if not result.ok: raise RuntimeError(result.error)
    print('STAGE_AUTHOR_ACCEPTANCE_PASS; completed author/material/object/QC work retained', flush=True)


if __name__ == '__main__': main()
