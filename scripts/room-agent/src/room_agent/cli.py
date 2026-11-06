"""JSON CLI for explicit, inspectable requests. All commands run in the foreground."""
import argparse
import json
import sys

from .service import Service


def main():
    parser = argparse.ArgumentParser(description='Room Agent: saved-source audit foundation')
    parser.add_argument('--state-root', required=True, help='Persistent local directory under return_output')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Create an immutable saved-source audit request')
    create.add_argument('--room', required=True)
    create.add_argument('--source', required=True, help='Directory with Room.py and its asset files')
    for name in ('run', 'resume', 'show', 'events'):
        command = commands.add_parser(name)
        command.add_argument('run_id')
        if name == 'resume':
            command.add_argument('--action', choices=['finish', 'cancel'])
        if name == 'events':
            command.add_argument('--after', type=int, default=0)
    commands.add_parser('list')
    args = parser.parse_args()
    service = Service(args.state_root)
    try:
        if args.command == 'create':
            result = {'run_id': service.create(args.room, args.source)}
        elif args.command in ('run', 'resume'):
            result = service.execute(args.run_id, action=getattr(args, 'action', None))
        elif args.command == 'show':
            result = service.show(args.run_id)
        elif args.command == 'events':
            result = service.events(args.run_id, after=args.after)
        else:
            result = service.list_runs()
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
