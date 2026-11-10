import json
from pathlib import Path
import pytest
from room_agent.engine_compatibility import verify_engine
from room_agent.scan_executor import engine_hash
from room_agent.saved_scene import engine_hash as saved_engine


def test_release_compatibility_is_exact_not_a_blanket_bypass():
    p=Path(__file__).resolve().parents[1]/'engine_compatibility.json'
    policy=json.loads(p.read_text())
    for kind,current in [('full_scan',engine_hash()),('saved_scene',saved_engine())]:
        assert policy['engines'][kind]['current']==current
        previous=policy['engines'][kind]['previous'][0]
        verify_engine(previous,current,kind)
        with pytest.raises(ValueError):verify_engine('unknown old code',current,kind)
        with pytest.raises(ValueError):verify_engine(previous,'modified current code',kind)
