import http.client
import json
from pathlib import Path
import threading

import pytest


class FakeService:
    def __init__(self, root):
        self.root = root
        self.created = []

    def list_runs(self):
        return [self.show('run1')]

    def show(self, run_id):
        if run_id != 'run1':
            raise FileNotFoundError(run_id)
        return {'run_id': run_id, 'room_id': 'room_004', 'execution_status': 'waiting_for_input',
                'quality_status': 'not_passed', 'operation': 'saved_scene_review',
                'stages': [{'node': 'render', 'status': 'completed', 'seconds': 2.0}]}

    def create_review(self, source_run, **kwargs):
        self.created.append((source_run, kwargs))
        return 'run1'

    def events(self, run_id, after=0):
        return [{'seq': 2, 'kind': 'node_completed'}] if after < 2 else []

    def package_return(self, run_id):
        path = self.root.parent / 'room_return.tar.gz'
        path.write_bytes(b'archive fixture')
        return path


class FakeWorker:
    def __init__(self):
        self.calls = []
        self.busy = False

    def start(self, run_id, action=None):
        if self.busy:
            raise RuntimeError('another worker is running')
        self.calls.append((run_id, action))
        self.busy = True
        return {'pid': 123, 'run_id': run_id}

    def close(self):
        pass


@pytest.fixture
def web(tmp_path):
    from room_agent.web import make_server
    root = tmp_path / 'state'
    (root / 'runs' / 'run1').mkdir(parents=True)
    fake = FakeService(root)
    worker = FakeWorker()
    server = make_server(root, tmp_path, port=0, service=fake, worker=worker,
                         source_discovery=lambda p: [{'path': '/saved/run', 'scene': 'room_004', 'label': 'existing'}])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, fake, worker
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(web, path, method='GET', body=None, headers=None):
    server = web[0]
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    encoded = json.dumps(body).encode() if body is not None else None
    heads = dict(headers or {})
    if method == 'POST':
        heads.setdefault('Content-Type', 'application/json')
    conn.request(method, path, body=encoded, headers=heads)
    reply = conn.getresponse()
    result = reply.status, dict(reply.getheaders()), reply.read()
    conn.close()
    return result


def auth(web):
    status, _, data = request(web, '/api/config')
    assert status == 200
    return {'X-Room-Token': json.loads(data)['token'],
            'Origin': 'http://127.0.0.1:' + str(web[0].server_port)}


def test_web_module_is_available():
    import importlib.util
    assert importlib.util.find_spec('room_agent.web') is not None


def test_index_and_read_only_status_endpoints(web):
    assert 'Spatial Agent'.encode() in request(web, '/')[2]
    assert json.loads(request(web, '/api/runs')[2])[0]['quality_status'] == 'not_passed'
    assert json.loads(request(web, '/api/sources')[2])[0]['scene'] == 'room_004'
    assert json.loads(request(web, '/api/runs/run1/events?after=2')[2]) == []


def test_rejects_foreign_host_and_cross_origin_posts(web):
    assert request(web, '/', headers={'Host': 'evil.example'})[0] == 403
    assert request(web, '/api/runs', 'POST', {'source_run': '/saved/run'})[0] == 403
    headers = auth(web)
    headers['Origin'] = 'https://evil.example'
    assert request(web, '/api/runs', 'POST', {'source_run': '/saved/run'}, headers)[0] == 403
    assert not web[1].created


def test_creates_request_without_starting_worker(web):
    result = request(web, '/api/runs', 'POST', {'source_run': '/saved/run', 'max_batches': 1,
                                             'repair_seconds': 900}, auth(web))
    assert result[0] == 201
    assert json.loads(result[2])['run_id'] == 'run1'
    assert web[1].created == [('/saved/run', {'max_batches': 1, 'repair_seconds': 900})]
    assert web[2].calls == []


def test_worker_actions_are_explicit_and_busy_is_reported(web):
    headers = auth(web)
    result = request(web, '/api/runs/run1/resume', 'POST', {'action': 'repair'}, headers)
    assert result[0] == 202
    assert web[2].calls == [('run1', 'repair')]
    assert request(web, '/api/runs/run1/start', 'POST', {}, headers)[0] == 409
    assert request(web, '/api/runs/run1/resume', 'POST', {'action': 'shell'}, headers)[0] == 400


def test_artifact_serving_rejects_traversal_and_symlinks(web, tmp_path):
    root = web[1].root / 'runs' / 'run1'
    (root / 'image.png').write_bytes(b'PNG fixture')
    assert request(web, '/artifact?run_id=run1&path=image.png')[2] == b'PNG fixture'
    (tmp_path / 'secret.txt').write_text('secret')
    (root / 'link.txt').symlink_to(tmp_path / 'secret.txt')
    assert request(web, '/artifact?run_id=run1&path=../../../secret.txt')[0] == 400
    assert request(web, '/artifact?run_id=run1&path=link.txt')[0] == 400
    assert request(web, '/artifact?run_id=../run1&path=image.png')[0] == 400


def test_logs_are_tailed_and_unknown_runs_are_not_served(web):
    log = web[1].root / 'runs' / 'run1' / 'console.log'
    log.write_text('x' * 100000 + '\nlast line\n')
    result = request(web, '/api/runs/run1/log')
    assert result[0] == 200
    assert len(result[2]) <= 65536
    assert result[2].endswith(b'last line\n')
    assert request(web, '/api/runs/missing')[0] == 404


def test_log_endpoint_includes_latest_stage_without_unbounded_payload(web):
    root = web[1].root / 'runs' / 'run1'
    (root / 'console.log').write_text('console start\n')
    (root / 'logs').mkdir()
    (root / 'logs' / 'r00_build.log').write_text('x' * 100000 + '\nBlender progress\n')
    result = request(web, '/api/runs/run1/log')
    assert result[0] == 200
    assert b'console start' in result[2]
    assert b'r00_build.log' in result[2]
    assert result[2].endswith(b'Blender progress\n')
    assert len(result[2]) <= 65536


def test_return_download_is_only_available_without_active_worker(web):
    result = request(web, '/return?run_id=run1')
    assert result[0] == 200
    assert 'attachment' in result[1]['Content-Disposition']
    web[2].busy = True
    assert request(web, '/return?run_id=run1')[0] == 409


def test_only_loopback_binding_is_allowed(tmp_path):
    from room_agent.web import make_server
    with pytest.raises(ValueError, match='loopback'):
        make_server(tmp_path / 'state', tmp_path, host='0.0.0.0')


def test_symlinked_runs_parent_cannot_escape_state_root(web, tmp_path):
    root = web[1].root
    outside = tmp_path / 'outside'
    (root / 'runs').rename(outside)
    (outside / 'run1' / 'secret.txt').write_text('outside secret')
    (root / 'runs').symlink_to(outside, target_is_directory=True)
    assert request(web, '/artifact?run_id=run1&path=secret.txt')[0] == 400


def test_real_http_cli_checkpoint_round_trip(tmp_path, monkeypatch, capsys):
    import time
    from room_agent.service import Service
    from room_agent.web import make_server

    source = tmp_path / 'source'
    source.mkdir()
    original = b'bed_width = 1.82\n'
    (source / 'Room.py').write_bytes(original)
    root = tmp_path / 'return_output' / 'agent_state'
    run_id = Service(root).create('room_web', source)
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1] / 'src'))
    server = make_server(root, tmp_path, port=0, source_discovery=lambda p: [])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = (server, server.service, server.worker)

    def wait_for(expected):
        deadline = time.monotonic() + 15
        last = None
        while time.monotonic() < deadline:
            response = request(context, '/api/runs/' + run_id)
            assert response[0] == 200, response[2]
            last = json.loads(response[2])
            if last['execution_status'] == expected and not server.worker.busy:
                return last
            if last['execution_status'] == 'failed':
                break
            time.sleep(0.05)
        log = (root / 'runs' / run_id / 'console.log').read_text()
        raise AssertionError(f'Expected {expected}, got {last}; console: {log}')

    try:
        headers = auth(context)
        response = request(context, '/api/runs/' + run_id + '/start', 'POST', {}, headers)
        assert response[0] == 202, response[2]
        waiting = wait_for('waiting_for_input')
        assert waiting['operation'] == 'saved_source_audit'
        assert waiting['quality_status'] == 'unreviewed'
        assert (root / 'runs' / run_id / 'checkpoints.sqlite').is_file()
        response = request(context, '/api/runs/' + run_id + '/resume', 'POST', {'action': 'finish'}, headers)
        assert response[0] == 202, response[2]
        completed = wait_for('completed')
        assert completed['quality_status'] == 'unreviewed'
        actions = [e for e in Service(root).events(run_id) if e['kind'] == 'action_received']
        assert actions[-1]['origin'] == 'web' and actions[-1]['action'] == 'finish'
        assert (source / 'Room.py').read_bytes() == original
        assert len(list(source.iterdir())) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    console = (root / 'runs' / run_id / 'console.log').read_text()
    assert '"execution_status": "completed"' in console
    terminal = capsys.readouterr().out
    assert '[' + run_id + ']' in terminal
    assert '"execution_status": "completed"' in terminal


def test_review_panel_preserves_missing_score_and_labels_support_findings():
    import shutil
    import subprocess
    html = (Path(__file__).resolve().parents[1] / 'src' / 'room_agent' / 'dashboard.html').read_text()
    assert 'function reviewPanel(status)' in html
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed only for this browser presentation unit check')
    function = html.split('function reviewPanel(status)', 1)[1].split('function paint(status)', 1)[0]
    program = '''
const assert = require('node:assert/strict');
class Element {
  constructor(){ this.children=[];this.text=''; }
  set textContent(v){this.text=String(v);this.children=[];}
  get textContent(){return this.text+this.children.map(x=>x.textContent).join('');}
  append(...values){this.children.push(...values);}
  appendChild(v){this.children.push(v);}
  replaceChildren(...values){this.text='';this.children=values;}
}
const qualityNames={};
const elements={reviewBadge:new Element(),reviewProgress:new Element(),reviewScore:new Element(),reviewSummary:new Element(),reviewIssues:new Element()};
const $=id=>elements[id];
const document={createElement:()=>new Element()};
''' + 'function reviewPanel(status)' + function + '''
reviewPanel({issues:[{priority:'support',object_ids:['Bed0'],defect:'脚部离地'}]});
assert.equal(elements.reviewScore.textContent,'—');
assert.match(elements.reviewSummary.textContent,/Independent visual review has not completed/);
assert.match(elements.reviewIssues.textContent,/Geometry/);
assert.match(elements.reviewIssues.textContent,/Bed0/);
assert.match(elements.reviewIssues.textContent,/脚部离地/);
reviewPanel({score:8.4,issues:[]});
assert.equal(elements.reviewScore.textContent,'8.4 / 10');
assert.doesNotMatch(elements.reviewIssues.textContent,/脚部离地/);
'''
    result = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_terminal_and_repair_labels_do_not_imply_a_failure():
    import shutil
    import subprocess
    html = (Path(__file__).resolve().parents[1] / 'src/room_agent/dashboard.html').read_text()
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed only for browser presentation tests')
    functions = 'function repairStatusText(status)' + html.split('function repairStatusText(status)', 1)[1].split('function paint(status)', 1)[0]
    program = "const assert=require('node:assert/strict');\n" + functions + '''
assert.equal(repairStatusText({repair_termination:'not_started'}),'No repair started');
assert.equal(repairStatusText({current_node:'repair',execution_status:'running'}),'Repair in progress');
assert.match(completionReasonText('user_finished'),/Ended by your choice/);
assert.match(completionReasonText('legacy_unspecified'),/not recorded by the older version/);
assert.match(completionReasonText('repair_timeout'),/Review completed after a repair timeout/);
'''
    result = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_full_scan_creation_and_listing_are_scoped_to_project(web, tmp_path):
    import tarfile
    import io
    raw = tmp_path / 'data/raw/room_new'
    raw.mkdir(parents=True)
    archive = raw / 'scan.tar'
    with tarfile.open(archive, 'w') as tf:
        info=tarfile.TarInfo('capture/file.txt');info.size=1
        tf.addfile(info,io.BytesIO(b'x'))
    calls=[]
    web[1].create_full=lambda archive,scene,project,prepare_only=False: (calls.append((archive,scene,project,prepare_only)) or 'run1')
    status,_,body=request(web,'/api/scans')
    assert status==200 and json.loads(body)[0]['path']==str(archive)
    status,_,_=request(web,'/api/full-runs','POST',{'scan_archive':str(archive),'scene':'room_new','prepare_only':True},auth(web))
    assert status==201 and calls[-1]==(str(archive),'room_new',tmp_path,True)
    status,_,_=request(web,'/api/full-runs','POST',{'scan_archive':str(archive),'scene':'room_new','prepare_only':'yes'},auth(web))
    assert status==400
