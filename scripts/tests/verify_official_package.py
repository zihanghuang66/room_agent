"""Verify the deliverable archive in a clean extraction, without model/network calls."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[2]
VERSION=os.environ.get('ROOM_VERIFY_VERSION','0.3.0')
PACKAGE=ROOT/f'scripts/packages/room-litereality-full-v{VERSION}.tar.gz'
REPORT=ROOT/('return_output/reconstruction_reports/litereality_upstream_audit/v'+VERSION.replace('.','')+'_tests')
REPORT.mkdir(parents=True,exist_ok=True)
NAMES=['test_official_flow_package.py','test_dependency_probe.py','test_codex_transport_retry.py',
       'test_codex_runtime_resume.py','test_imagegen_retry.py','test_imagegen_resume.py',
       'test_new_scan_full.py','test_room003_full.py','test_existing_codex_runtime.py',
       'test_litereality_capture_repair.py','test_blender_resume.py','test_reference_audit.py']
if VERSION=='0.3.1': NAMES += ['test_wall_facade.py','test_wall_resume.py']
report={'package':str(PACKAGE),'sha256':hashlib.sha256(PACKAGE.read_bytes()).hexdigest(),
        'tests':[],'gpu_full_run_tested':False,'paid_model_calls_tested':False}
with tempfile.TemporaryDirectory(prefix='room-official-package-') as tmp:
    target=Path(tmp)
    with tarfile.open(PACKAGE) as tar:
        for m in tar:
            assert m.isfile() and m.mtime==0 and '..' not in Path(m.name).parts and not Path(m.name).is_absolute()
        tar.extractall(target,filter='data')
    package=target/f'room-litereality-full-v{VERSION}'
    for name,sha in json.loads((package/'package_manifest.json').read_text()).items():
        assert hashlib.sha256((target/name).read_bytes()).hexdigest()==sha,name
    with tarfile.open(package/'app.tar.gz') as tar:tar.extractall(package/'app',filter='data')
    for p in package.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
    for p in package.glob('*.sh'):subprocess.run(['bash','-n',str(p)],check=True)
    env=dict(os.environ,ROOM_TEST_PACKAGE=str(package),ROOM_TEST_APP=str(package/'app'),PYTHONDONTWRITEBYTECODE='1',PYTHONWARNINGS='ignore::ResourceWarning')
    for name in NAMES:
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/tests'/name)],capture_output=True,text=True,env=env,cwd=ROOT)
        output=proc.stdout+proc.stderr
        (REPORT/(name+'.log')).write_text(output)
        match=re.search(r'Ran (\d+) tests?',output)
        item=dict(file=name,returncode=proc.returncode,count=int(match[1]) if match else 0)
        report['tests'].append(item)
        print(name,item,flush=True)
        if proc.returncode: print(output,flush=True)
report['passed']=all(t['returncode']==0 for t in report['tests'])
report['test_count']=sum(t['count'] for t in report['tests'])
(REPORT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
print('DELIVERABLE',report['passed'],'TESTS',report['test_count'])
sys.exit(0 if report['passed'] else 1)
