"""One bounded diagnostic Codex session; inspect shell and patch results independently."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import sys


def inspect_file(path,expected):
    result={'exists':path.exists(),'is_symlink':path.is_symlink(),'content_matches':False}
    try:
        info=path.lstat();result.update(mode=oct(stat.S_IMODE(info.st_mode)),uid=info.st_uid,gid=info.st_gid,size=info.st_size)
        if stat.S_ISREG(info.st_mode) and info.st_size<4096:
            result['content_matches']=path.read_text().strip()==expected
    except (OSError,UnicodeError) as exc:result['error_type']=type(exc).__name__
    return result


def trace_message(message,providers,redact):
    records=[]
    if getattr(message,'raw',None) is not None:
        records.append({'kind':'raw_event','event':redact(json.dumps(message.raw,default=str))})
    for block in getattr(message,'content',[]):
        if isinstance(block,providers.ToolUseBlock):
            records.append({'kind':'tool_use','id':block.id,'name':block.name,'input':redact(json.dumps(block.input,default=str))})
        elif isinstance(block,providers.ToolResultBlock):
            records.append({'kind':'tool_result','id':block.tool_use_id,'is_error':block.is_error,'content':redact(str(block.content))})
        elif isinstance(block,providers.TextBlock):records.append({'kind':'text','text':redact(block.text)})
    return records


def directory_info(work):
    info=work.stat()
    result={'path':str(work),'realpath':str(work.resolve()),'uid':info.st_uid,'gid':info.st_gid,
        'mode':oct(stat.S_IMODE(info.st_mode)),'process_uid':os.getuid(),'process_gid':os.getgid(),
        'os_access_writable':os.access(work,os.W_OK),'host_cwd':str(Path.cwd())}
    mountinfo=Path('/proc/self/mountinfo')
    if mountinfo.is_file():
        candidates=[]
        for line in mountinfo.read_text().splitlines():
            fields=line.split();cut=fields.index('-');mount=fields[4].replace('\\040',' ')
            if str(work)==mount or str(work).startswith(mount.rstrip('/')+'/'):
                candidates.append({'mount_point':mount,'mount_options':fields[5],'filesystem':fields[cut+1]})
        if candidates:result['mount']=max(candidates,key=lambda x:len(x['mount_point']))
    return result


async def run_probe(report):
    from litereality_agent.agent import providers
    from litereality_agent.agent.room_codex import redact,session_diagnostics
    work=report/'write_workspace';work.mkdir()
    nonce=secrets.token_hex(12);state=report/'probe_nonce.txt';state.write_text(nonce)
    result={'model':'gpt-6-astra','scope':'synthetic_write_diagnostics_only','directory':directory_info(work)}
    host=work/'host_write_probe.txt'
    host.write_text(nonce);result['host_write']=inspect_file(host,nonce);host.unlink()
    prompt=f'''This is a file-write diagnostic in {work}. Stay inside this directory for all file edits.
1. Call the local MCP tool get_probe_value once. Its returned string is the value for BOTH files below. Do not read its backing file or files outside this test directory.
2. Use a shell tool and the Python interpreter {shlex.quote(sys.executable)} to print the working directory, effective UID/GID, and attempt writing the returned string to the absolute path {work/'shell_result.txt'}. Show the real exception if it fails. Do not use apply_patch for this file.
3. Independently use your patch/edit tool (apply_patch if available) to create {work/'patch_result.txt'} with the same string. Do not use a shell to create this second file. Continue this test even if step 2 failed.
4. Read back both files if possible. Report actual success/failure for each operation. Never report DONE or success when a write failed. Do not request elevated permissions or change the sandbox, permissions, ownership, or mounts. Do not explore other directories.'''
    spec=providers.SessionSpec(prompt=prompt,cwd=work,read_only=False,timeout_seconds=240,step_budget=24,
        setting_sources=(),stdio_mcp={'roomprobe':{'command':sys.executable,
            'args':[str(Path(__file__).with_name('probe_mcp.py')),str(state)]}})
    terminal=None;records=[];mcp_ids=set();successful_ids=set()
    try:
        with (report/'tool_events.jsonl').open('w') as log:
            async for message in providers.resolve(override='codex').run(spec):
                for item in trace_message(message,providers,redact):
                    records.append(item);log.write(json.dumps(item,ensure_ascii=False)+'\n');log.flush()
                    if item['kind']=='tool_use' and 'get_probe_value' in item['name']:mcp_ids.add(item['id'])
                    if item['kind']=='tool_result' and not item['is_error']:successful_ids.add(item['id'])
                if isinstance(message,providers.SessionResult):terminal=message
    except Exception as exc:result['exception']={'type':type(exc).__name__,'message':redact(str(exc))}
    result['diagnostics']=session_diagnostics(terminal)
    result['mcp_called']=bool(mcp_ids);result['mcp_succeeded']=bool(mcp_ids & successful_ids)
    result['shell_file']=inspect_file(work/'shell_result.txt',nonce)
    result['patch_file']=inspect_file(work/'patch_result.txt',nonce)
    result['failed_tool_results']=[item for item in records if item['kind']=='tool_result' and item['is_error']]
    result['session_completed']=bool(terminal and not terminal.is_error and not terminal.stopped)
    result['status']='passed' if result['session_completed'] and result['mcp_succeeded'] and result['shell_file']['content_matches'] and result['patch_file']['content_matches'] else 'incomplete'
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    result=asyncio.run(run_probe(args.report.resolve()))
    (args.report/'write_probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
