"""Verify source-only packaging and room_004 launch without inference or raw data."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

SCRIPTS=Path(__file__).resolve().parents[1]
FULL=SCRIPTS/'litereality-full-v0.3.1'
BATCH=SCRIPTS/'litereality-batch-review-v0.1.0'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def test_both_payloads_preserve_audited_application():
    for pkg in (FULL,BATCH):
        assert digest(pkg/'app.tar.gz')=='8748b08b2ed7790a7b63f389a523a86707b23b8adb6522810b40e401f194fc94'
        with tarfile.open(pkg/'app.tar.gz') as tar:
            assert len(tar.getmembers())==250
            assert all(m.mtime==0 and not m.issym() for m in tar.getmembers())

def test_current_manifests_match_all_delivered_files():
    full=json.loads((FULL/'package_manifest.json').read_text())
    for name,sha in full.items():
        assert digest(FULL/Path(name).relative_to(Path(name).parts[0]))==sha
    batch=json.loads((BATCH/'package_manifest.json').read_text())
    for name,sha in batch['files'].items():assert digest(BATCH/name)==sha

def test_source_launcher_validates_scan_and_emits_return_archive(tmp_path):
    scan=tmp_path/'test_04.tar'
    with tarfile.open(scan,'w') as tar:
        for name in ('room.usdz','frame_00000.json','frame_00000.jpg','depth_00000.png'):
            blob=b'offline fixture';member=tarfile.TarInfo('capture/'+name)
            member.size=len(blob);tar.addfile(member,io.BytesIO(blob))
    proc=subprocess.run(['bash',str(SCRIPTS/'run_room_004.sh'),'full','--project-root',str(tmp_path),
        '--scan-archive',str(scan),'--prepare-only'],env=dict(os.environ,ROOM_APP_PYTHON=sys.executable),
        capture_output=True,text=True)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert 'PREPARE_ONLY_PASS' in proc.stdout and 'RETURN_TAR:' in proc.stdout
    runs=list((tmp_path/'return_output/artifacts').iterdir());assert len(runs)==1
    assert json.loads((runs[0]/'input.json').read_text())['scene']=='room_004_test_04'
    assert json.loads((runs[0]/'result.json').read_text())['full_run_completed'] is False

def test_review_requires_explicit_run():
    proc=subprocess.run(['bash',str(SCRIPTS/'run_room_004.sh'),'review'],capture_output=True,text=True)
    assert proc.returncode==2 and 'explicit existing run' in proc.stderr
