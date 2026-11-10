"""Loopback-only monitor. The foreground server owns every worker it launches."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import selectors
import time
import io
import signal
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .service import Service
from .storage import identifier


class WorkerManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.process = None
        self.reader = None
        self.lock = threading.Lock()

    @property
    def busy(self):
        return ((self.process is not None and self.process.poll() is None) or
                (self.reader is not None and self.reader.is_alive()))

    @staticmethod
    def _tee(process, stream, run_id):
        try:
            with stream, process.stdout:
                def emit(line):
                    stream.write(line)
                    try:
                        print('[' + run_id + '] ' + line.decode('utf-8', errors='replace').rstrip('\n'), flush=True)
                    except (BrokenPipeError, OSError, ValueError):
                        pass
                try:fd=process.stdout.fileno()
                except (AttributeError,io.UnsupportedOperation):
                    for line in iter(lambda:process.stdout.readline(65536),b''):emit(line)
                    return
                # A grandchild can inherit stdout after the direct CLI exits.
                # Drain for at most one second after exit; never retain queue
                # ownership forever waiting for that unrelated writer's EOF.
                exited_at=None
                with selectors.DefaultSelector() as selector:
                    selector.register(fd,selectors.EVENT_READ)
                    while True:
                        if process.poll() is not None:
                            if exited_at is None:exited_at=time.monotonic()
                            if time.monotonic()-exited_at>=1:break
                        if not selector.select(.1):continue
                        data=os.read(fd,65536)
                        if not data:break
                        emit(data)
        except OSError as exc:
            print('[' + run_id + '] 日志写入失败：' + str(exc), file=sys.stderr, flush=True)

    def start(self, run_id, action=None):
        identifier(run_id)
        with self.lock:
            if self.busy:
                raise RuntimeError('已有一个任务正在执行，请等待它结束或暂停')
            directory = self.root / 'runs' / run_id
            if not directory.is_dir():
                raise FileNotFoundError('unknown run')
            args = [sys.executable, '-u', '-m', 'room_agent', '--state-root', str(self.root)]
            args += ['resume' if action else 'run', run_id]
            args += ['--origin', 'web']
            if action:
                args += ['--action', action]
            stream = (directory / 'console.log').open('ab', buffering=0)
            try:
                self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, start_new_session=True)
            except BaseException:
                stream.close()
                raise
            self.reader = threading.Thread(target=self._tee, args=(self.process, stream, run_id), daemon=True)
            self.reader.start()
            return {'run_id': run_id, 'pid': self.process.pid}

    def close(self):
        with self.lock:
            if not self.busy:
                return
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
            except ProcessLookupError:
                self.process.wait(timeout=5)
            finally:
                if self.reader is not None:
                    self.reader.join(timeout=5)


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self):
        if hasattr(self, 'worker'):
            self.worker.close()
        super().server_close()


def _sources(project):
    from .saved_scene import discover_sources
    return discover_sources(project)


def make_server(state_root, project, *, host='127.0.0.1', port=8765,
                service=None, worker=None, source_discovery=None):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('web monitor requires a loopback bind address')
    server = MonitorServer(('127.0.0.1', port), Handler)
    server.root = Path(state_root).resolve()
    server.project = Path(project).resolve()
    server.service = service if service is not None else Service(server.root)
    try:
        if worker is None:
            from .task_queue import TaskQueue
            server.worker = TaskQueue(server.root, server.service, WorkerManager(server.root))
        else:
            server.worker = worker
    except BaseException:
        server.server_close()
        raise
    server.source_discovery = source_discovery or _sources
    server.token = secrets.token_urlsafe(32)
    return server


class Handler(BaseHTTPRequestHandler):
    server_version = 'RoomMonitor/0.2'

    def log_message(self, *_):
        # Console output belongs to workflow logs, not access/query strings.
        pass

    def _send(self, status, data, content_type='application/json; charset=utf-8', **headers):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in headers.items():
            self.send_header(key.replace('_', '-'), value)
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, mutate=False):
        host = self.headers.get('Host', '')
        parsed = urlsplit('http://' + host)
        try:
            allowed = (parsed.hostname in ('127.0.0.1', 'localhost') and
                       parsed.port == self.server.server_port and not parsed.username and
                       not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            allowed = False
        origin = self.headers.get('Origin')
        if not allowed or (origin and origin != 'http://' + host):
            raise PermissionError('只允许本机或 SSH 转发访问')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise PermissionError('cross-site request denied')
        if mutate and (origin != 'http://' + host or
                       not secrets.compare_digest(self.headers.get('X-Room-Token', ''), self.server.token)):
            raise PermissionError('页面会话校验失败，请刷新网页')

    def _run(self, run_id):
        identifier(run_id)
        return self._queue_status(self.server.service.show(run_id))

    def _queue_status(self, result):
        result = dict(result)
        if hasattr(self.server.worker, 'status'):
            result.update(self.server.worker.status(result['run_id']))
            if result.get('queue_status') in ('queued', 'dispatching'):
                result.update(execution_status=result['queue_status'], current_node='queue')
            elif result.get('queue_status') == 'running' and result['execution_status'] == 'created':
                result.update(execution_status='running', current_node='queue')
            elif result.get('queue_status') == 'failed' and result['execution_status'] not in ('completed', 'failed', 'running'):
                result.update(execution_status='failed', current_node='queue',
                              error={'node': 'queue', 'message': result.get('queue_error') or '任务未能启动'})
            elif result.get('queue_status') == 'interrupted' and result['execution_status'] == 'created':
                result.update(execution_status='interrupted', current_node='queue')
        return result

    def _artifact(self, run_id, supplied):
        self._run(run_id)
        root = self.server.root / 'runs' / run_id
        path = Path(supplied)
        if not path.is_absolute():
            if '..' in path.parts:
                raise ValueError('artifact path traversal denied')
            path = root / path
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise ValueError('artifact must belong to this run') from None
        cursor = self.server.root
        for part in ('runs', run_id, *relative.parts):
            if part in ('.', '..'):
                raise ValueError('artifact path traversal denied')
            cursor /= part
            if cursor.is_symlink():
                raise ValueError('symlink artifact denied')
        if not path.is_file():
            raise FileNotFoundError('artifact not found')
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.json', '.txt', '.log', '.md', '.glb', '.blend'):
            raise ValueError('artifact type is not exposed by the monitor')
        return path

    def _file(self, path, *, attachment=False):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('download not found')
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if attachment:
            # Filename uses only ASCII fallback. Never interpolate arbitrary path text into headers.
            self.send_header('Content-Disposition', 'attachment; filename="room-return.tar.gz"')
        self.end_headers()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                self.wfile.write(chunk)

    def do_GET(self):
        try:
            self._guard()
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == '/':
                return self._send(200, Path(__file__).with_name('dashboard.html').read_bytes(), 'text/html; charset=utf-8')
            if path == '/api/config':
                return self._send(200, {'token': self.server.token})
            if path == '/api/runs':
                return self._send(200, [self._queue_status(r) for r in self.server.service.list_runs()])
            if path == '/api/versions':
                return self._send(200, self.server.service.versions())
            if path == '/api/sources':
                return self._send(200, self.server.source_discovery(self.server.project))
            if path == '/api/scans':
                from .full_scan import discover_scans
                return self._send(200, discover_scans(self.server.project))
            if path.startswith('/api/runs/'):
                parts = path.split('/')
                run_id = parts[3]
                status = self._run(run_id)
                if len(parts) == 4:
                    return self._send(200, status)
                if len(parts) == 5 and parts[4] == 'events':
                    after = int(query.get('after', ['0'])[0])
                    if after < 0:
                        raise ValueError('invalid cursor')
                    return self._send(200, self.server.service.events(run_id, after=after))
                if len(parts) == 5 and parts[4] == 'log':
                    run_root = self.server.root / 'runs' / run_id
                    logfile = run_root / 'console.log'
                    stage_logs = [p for p in (run_root / 'logs').glob('*.log') if not p.is_symlink()]
                    latest = max(stage_logs, key=lambda p: p.stat().st_mtime_ns) if stage_logs else None
                    limit = 32000 if latest else 65536
                    data = b''
                    if logfile.exists():
                        logfile = self._artifact(run_id, 'console.log')
                        with logfile.open('rb') as stream:
                            stream.seek(max(0, logfile.stat().st_size - limit))
                            data = stream.read(limit)
                    if latest:
                        latest = self._artifact(run_id, latest)
                        data += ('\n\n--- 最新节点日志：' + latest.name + ' ---\n').encode()
                        with latest.open('rb') as stream:
                            stream.seek(max(0, latest.stat().st_size - limit))
                            data += stream.read(limit)
                    return self._send(200, data, 'text/plain; charset=utf-8')
            if path == '/artifact':
                file = self._artifact(query.get('run_id', [''])[0], query.get('path', [''])[0])
                return self._file(file)
            if path == '/return':
                run_id = query.get('run_id', [''])[0]
                status = self._run(run_id)
                if (status['execution_status'] in ('running', 'queued', 'dispatching') or status.get('worker_finalizing') or
                        not hasattr(self.server.worker, 'status') and self.server.worker.busy):
                    raise RuntimeError('请等待任务结束或暂停后下载返回包')
                return self._file(self.server.service.package_return(run_id), attachment=True)
            self._send(404, {'error': 'not found'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        try:
            self._guard(mutate=True)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON request required')
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 65536:
                raise ValueError('request too large')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('JSON object required')
            path = urlsplit(self.path).path
            if path == '/api/edit-runs':
                run = self.server.service.create_edit(data.get('parent_run_id'), data.get('instruction'))
                return self._send(201, {'run_id': run})
            if path == '/api/full-runs':
                archive, scene = data.get('scan_archive'), data.get('scene')
                prepare = data.get('prepare_only', False)
                if not isinstance(archive, str) or not isinstance(scene, str) or type(prepare) is not bool:
                    raise ValueError('请选择扫描文件、填写房间名称并指定检查或重建')
                run = self.server.service.create_full(archive, scene, self.server.project, prepare_only=prepare)
                return self._send(201, {'run_id': run})
            if path == '/api/runs':
                source = data.get('source_run')
                if not isinstance(source, str) or not source:
                    raise ValueError('请选择一个已有运行目录')
                batches, seconds = data.get('max_batches', 1), data.get('repair_seconds', 900)
                if type(batches) is not int or not 0 <= batches <= 3 or type(seconds) is not int or not 120 <= seconds <= 1800:
                    raise ValueError('max_batches 0..3; repair_seconds 120..1800')
                run = self.server.service.create_review(source, max_batches=batches, repair_seconds=seconds)
                return self._send(201, {'run_id': run})
            parts = path.split('/')
            if len(parts) == 5 and parts[:3] == ['', 'api', 'runs'] and parts[4] == 'cancel-queue':
                self._run(parts[3])
                return self._send(200, self.server.worker.cancel(parts[3]))
            if len(parts) == 5 and parts[:3] == ['', 'api', 'runs'] and parts[4] in ('start', 'resume'):
                run_id = parts[3]
                self._run(run_id)
                action = data.get('action') if parts[4] == 'resume' else None
                if action not in (None, 'repair', 'finish'):
                    raise ValueError('invalid action')
                return self._send(202, self.server.worker.start(run_id, action))
            self._send(404, {'error': 'not found'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._error(exc)

    def _error(self, exc):
        code = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, FileNotFoundError) else 409 if isinstance(exc, RuntimeError) else 400
        self._send(code, {'error': type(exc).__name__, 'message': str(exc)})


def serve(state_root, project, host='127.0.0.1', port=8765):
    server = make_server(state_root, project, host=host, port=port)
    print(f'ROOM_WEB_URL: http://127.0.0.1:{server.server_port}', flush=True)
    print('任务按提交顺序排队。浏览器关闭不会中断任务；停止服务会保留队列并停止自己的工作进程。', flush=True)
    old_handler = signal.getsignal(signal.SIGTERM)
    def stop(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print('正在停止工作进程并保留检查点……', flush=True)
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, old_handler)
