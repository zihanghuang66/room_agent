"""Package-owned Skill, explicitly loaded by bounded planner/worker roles."""
import hashlib
from pathlib import Path

SKILL = Path(__file__).resolve().parent/'skills/spatial-edit/SKILL.md'

def skill_digest():return hashlib.sha256(SKILL.read_bytes()).hexdigest()

def skill_text(request=None):
    data=SKILL.read_bytes()
    expected=(request or {}).get('edit_contract',{}).get('skill_sha256')
    if expected and expected!=hashlib.sha256(data).hexdigest():
        raise RuntimeError('Edit Skill identity changed; use the matching release for this request')
    return data.decode()
