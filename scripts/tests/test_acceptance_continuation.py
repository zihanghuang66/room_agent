"""Continuation cannot skip unfinished authoring or turn a failed gate into success."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'litereality-resume-v0.1.2'
sys.path[:0] = [str(SOURCE), str(Path(os.environ['ROOM_TEST_APP'])/'src')]
import finish_stage


class ContinuationTests(unittest.TestCase):
    def run_case(self, previous, accepted):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); room=root/'room';room.mkdir();(room/'Room.py').write_text('# preserve existing authored scene\n')
            state=root/'pipeline.json';state.write_text(json.dumps({'stages':{'author':previous}}))
            context=SimpleNamespace(state_path=state,authored_room=room,preview_dir=root/'room_preview')
            with patch.object(finish_stage,'finish',return_value=dict(accepted=accepted,status='accepted' if accepted else 'needs_repair')):
                result=finish_stage.resume_acceptance(context,{})
            self.assertEqual((room/'Room.py').read_text(),'# preserve existing authored scene\n')
            return result

    def test_missing_completed_passes_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'Cannot skip authoring'):
            self.run_case({'details':{}},True)

    def test_failed_gate_is_still_failed(self):
        r=self.run_case({'details':{'session':{'session_ok':True},'acceptance':{'accepted':False}}},False)
        self.assertFalse(r.ok)

    def test_only_actual_pass_marks_completed(self):
        r=self.run_case({'details':{'session':{'session_ok':True},'acceptance':{'accepted':False}}},True)
        self.assertTrue(r.ok)
        self.assertTrue(r.details['resumed_acceptance_only'])


if __name__ == '__main__': unittest.main()
