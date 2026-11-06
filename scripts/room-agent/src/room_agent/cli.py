"""JSON CLI for explicit, inspectable requests. All commands run in the foreground."""
import argparse
import json
import sys
from pathlib import Path
import signal

from .service import Service


def main():
    parser = argparse.ArgumentParser(description='Room Agent: saved-source audit foundation')
    parser.add_argument('--state-root', required=True, help='Persistent local directory under return_output')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Create an immutable saved-source audit request')
    create.add_argument('--room', required=True)
    create.add_argument('--source', required=True, help='Directory with Room.py and its asset files')
    review = commands.add_parser('create-review')
    review.add_argument('source_run')
    review.add_argument('--max-batches', type=int, default=1)
    review.add_argument('--repair-seconds', type=int, default=900)
    serve = commands.add_parser('serve')
    serve.add_argument('--project', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8765)
    commands.add_parser('check-storage')
    for name in ('run', 'resume', 'show', 'events', 'pack'):
        command = commands.add_parser(name)
        command.add_argument('run_id')
        if name == 'resume':
            command.add_argument('--action', choices=['finish', 'cancel', 'repair'])
        if name == 'events':
            command.add_argument('--after', type=int, default=0)
    commands.add_parser('list')
    args = parser.parse_args()
    service = Service(args.state_root)
    def stop(sig, frame):
        raise KeyboardInterrupt('Worker interrupted by signal ' + str(sig))
    signal.signal(signal.SIGTERM, stop)
    try:
        if args.command == 'serve':
            from .web import serve
            serve(args.state_root, args.project, port=args.port)
            return
        if args.command == 'check-storage':
            from .preflight import storage_probe
            result = storage_probe(Path(args.state_root) / 'checks/storage')
        elif args.command == 'create-review':
            result = {'run_id': service.create_review(args.source_run, max_batches=args.max_batches, repair_seconds=args.repair_seconds)}
        elif args.command == 'create':
            result = {'run_id': service.create(args.room, args.source)}
        elif args.command in ('run', 'resume'):
            result = service.execute(args.run_id, action=getattr(args, 'action', None))
        elif args.command == 'show':
            result = service.show(args.run_id)
        elif args.command == 'events':
            result = service.events(args.run_id, after=args.after)
        elif args.command == 'pack':
            result = {'return_tar': str(service.package_return(args.run_id))}
        else:
            result = service.list_runs()
    except BaseException as exc:
        if not isinstance(exc, (Exception, KeyboardInterrupt)):
            raise
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        if args.command in ('run', 'resume'):
            try:
                print('RETURN_TAR: ' + str(service.package_return(args.run_id)), flush=True)
            except Exception as pack_error:
                print('RETURN_PACK_ERROR: ' + str(pack_error), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command in ('run', 'resume') and result.get('operation') == 'saved_scene_review':
        try:
            print('RETURN_TAR: ' + str(service.package_return(args.run_id)), flush=True)
        except Exception as exc:
            print('RETURN_PACK_ERROR: ' + str(exc), file=sys.stderr)
