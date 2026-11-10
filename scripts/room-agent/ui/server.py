"""Session UI and explicit confirmation adapter; execution workers stay unchanged."""
import sys
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from display import build_presentation
from diagnostics import collect_for_service
from room_agent import web


class DiagnosticWorker(web.WorkerManager):
    def _tee(self, process, stream, run_id):
        super()._tee(process, stream, run_id)
        try:
            process.wait(timeout=10)
            path=collect_for_service(web.Service(self.root),run_id)
            print('['+run_id+'] DIAGNOSTICS_TAR: '+str(path),flush=True)
        except Exception as exc:
            # A packaging problem must never rewrite reconstruction status.
            print('['+run_id+'] DIAGNOSTICS_WARNING: '+str(exc),flush=True)


class PresentationHandler(web.Handler):
    def _sessions(self):
        from session_edits import SessionEdits
        return SessionEdits(self.server.service, self.server.worker,
                            status_reader=lambda run: web.Handler._run(self, run))

    def _conversation(self):
        # Stateless adapter; transcripts and locks are persisted, not handler-local.
        if hasattr(self.server, 'conversation'):
            return self.server.conversation
        from conversation import Conversation
        sessions=self._sessions()
        return Conversation(self.server.service, status_reader=sessions.project, context_reader=sessions.context)

    def do_POST(self):
        parts = urlsplit(self.path).path.split('/')
        if urlsplit(self.path).path=='/api/edit-runs':
            try:
                self._guard(mutate=True)
                raise RuntimeError('Describe the edit in the existing conversation, then use Confirm & run.')
            except Exception as exc:return self._error(exc)
        if len(parts) != 5 or parts[1:3] != ['api', 'runs'] or parts[4] not in ('conversation','confirm-plan','session-action'):
            return super().do_POST()
        try:
            self._guard(mutate=True)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON request required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                raise ValueError('Conversation request too large')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('JSON object required')
            if parts[4]=='conversation':return self._send(200, self._conversation().send(parts[3], data))
            sessions=self._sessions();sessions.conversation=self._conversation()
            result=sessions.confirm(parts[3],data) if parts[4]=='confirm-plan' else sessions.action(parts[3],data)
            return self._send(200,result)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Durable transcript remains available through GET.
        except Exception as exc:
            return self._error(exc)

    def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path in ('/api/runs','/api/versions'):
            try:
                self._guard();sessions=self._sessions()
                if parsed.path=='/api/runs':return self._send(200,sessions.workspace())
                rows=[r for r in self.server.service.versions() if not self.server.service._load(r['run_id'])[0].get('session_id')]
                return self._send(200,rows)
            except Exception as exc:return self._error(exc)
        parts=parsed.path.split('/')
        if len(parts)==4 and parts[1:3]==['api','runs']:
            try:
                self._guard()
                request,_=self.server.service._load(parts[3])
                return self._send(200,self._run(parts[3]) if request.get('session_id') else self._sessions().project(parts[3]))
            except Exception as exc:return self._error(exc)
        if parsed.path=='/diagnostics':
            try:
                self._guard()
                run_id=parse_qs(parsed.query).get('run_id',[''])[0]
                status=self._sessions().project(run_id) if not self.server.service._load(run_id)[0].get("session_id") else self._run(run_id)
                if status.get('execution_status') in {'running','queued','dispatching'} or status.get('worker_finalizing'):
                    raise RuntimeError('Wait until the task stops before collecting diagnostics')
                root=self.server.root/'runs'/run_id
                if root.is_symlink() or root.parent.is_symlink():raise ValueError('symlink run denied')
                from diagnostics import collect_session
                path=collect_session(self.server.service,run_id,status.get("session"))
                return self._send(200,path.read_bytes(),'application/gzip',Content_Disposition='attachment; filename="'+path.name+'"')
            except Exception as exc:
                return self._error(exc)
        parts = parsed.path.split('/')
        if len(parts) == 5 and parts[1:3] == ['api', 'runs'] and parts[4] == 'conversation':
            try:
                self._guard()
                return self._send(200, self._conversation().read(parts[3]))
            except Exception as exc:
                return self._error(exc)
        if len(parts) != 5 or parts[1:3] != ['api', 'runs'] or parts[4] != 'presentation':
            return super().do_GET()
        try:
            self._guard()
            status = self._run(parts[3])
            root = self.server.root / 'runs' / parts[3]
            if root.is_symlink() or root.parent.is_symlink():
                raise ValueError('symlink run denied')
            request,_=self.server.service._load(parts[3])
            if request.get('session_id'):return self._send(200,build_presentation(root,status))
            from session_edits import session_presentation
            return self._send(200,session_presentation(self._sessions(),parts[3]))
        except PermissionError as exc:
            return self._send(403, {'error': str(exc)})
        except (ValueError, FileNotFoundError) as exc:
            return self._send(400, {'error': str(exc)})
        except Exception:
            return self._send(500, {'error': 'Unable to read room previews; see task files.'})


def main():
    web.Handler = PresentationHandler
    web.WorkerManager = DiagnosticWorker
    from room_agent.cli import main as cli_main
    try:
        cli_main()
    finally:
        from planner_cli import close
        close()


if __name__ == '__main__':
    main()
