"""Explicit release-to-release compatibility, not a blanket fingerprint bypass."""
import json
from pathlib import Path


def verify_engine(previous, current, kind):
    if previous == current:
        return
    policy_path = Path(__file__).resolve().parents[2] / 'engine_compatibility.json'
    try:
        policy = json.loads(policy_path.read_text())
        scope = policy['engines'][kind]
        compatible = (policy['schema'] == 1 and scope['current'] == current
                      and previous in scope['previous'])
    except (OSError, ValueError, KeyError, TypeError):
        compatible = False
    if not compatible:
        raise ValueError('Execution code version changed; no verified compatibility policy for this release')
