"""Pinned Codex CLI text planner. Runtime contract tested against a local fake model.

No API key extraction and no alternative remote inference endpoint. The actual turn
uses the CLI's existing login. The contract probe uses an empty temporary home.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading

FLAGS=('shell_tool','unified_exec','code_mode','code_mode_host','code_mode_only','apps','plugins','hooks',
       'multi_agent','multi_agent_v2','browser_use','browser_use_external','computer_use','image_generation',
       'view_image','goals','sleep_tool','workspace_dependencies','skill_search','memories','shell_snapshot','daemon_auto_start')
from planner_contract import OUTPUT_SCHEMA, bound_schema

EXPECTED_VERSION='codex-cli 0.164.0'
MODEL='gpt-6-astra'
_processes=set()
_mutex=threading.Lock()
_probe_mutex=threading.Lock()
_verified=None
_closing=False


def command(binary,cwd,catalog):
    args=[str(binary),'exec','--ignore-user-config','--ignore-rules','--strict-config','--ephemeral',
          '--skip-git-repo-check','--sandbox','read-only','--json','--cd',str(cwd),
          '-c','approval_policy="never"','-c','web_search="disabled"','-c','project_doc_max_bytes=0',
          '-c','tools.update_plan.enabled=false','-c','model_reasoning_effort="low"',
          '-c','model_catalog_json='+json.dumps(str(catalog)),'-m',MODEL]
    for flag in FLAGS:args+=['--disable',flag]
    return args


def run(args,*,prompt='',timeout=120,env=None):
    with _mutex:
        if _closing:raise RuntimeError('Server stopping; planning interrupted')
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,env=env,start_new_session=True)
        _processes.add(process)
    try:
        try:out,err=process.communicate(prompt,timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Codex planning timed out. No task was started; retry by sending a new message.') from None
        if process.returncode:
            # Do not expose arbitrary runtime logs, URLs or authentication material to the browser.
            hint=' Check Codex login and network in the same container.'
            if 'unknown' in err.lower() or 'config' in err.lower():hint=' Codex configuration/runtime is not supported by this planner.'
            raise RuntimeError('Codex planner exit='+str(process.returncode)+'.'+hint)
        return out,err
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=5)
        with _mutex:_processes.discard(process)


def close():
    global _closing
    with _mutex:
        _closing=True
        for process in list(_processes):
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass


def parse_events(output):
    complete=False;messages=[]
    for line in output.splitlines():
        try:e=json.loads(line)
        except ValueError:continue
        typ=e.get('type','');item=e.get('item',{})
        if typ=='turn.failed' or typ=='error' and not str(e.get('message','')).startswith('Reconnecting...'):raise RuntimeError('Codex planning failed; check login/network. No task started.')
        if typ in ('item.started','item.completed'):
            if item.get('type') not in ('agent_message','reasoning','error'):
                raise RuntimeError('Planner returned an unexpected tool event; result rejected')
            if typ=='item.completed' and item.get('type')=='agent_message':messages.append(item.get('text',''))
            if item.get('type')=='error' and not str(item.get('message','')).startswith('Code Mode is unavailable because code-mode host is disabled.'):
                raise RuntimeError('Codex planner reported an error; result rejected')
        if typ=='turn.completed':complete=True
    if not complete or not messages:raise RuntimeError('Codex did not return a completed planning response')
    try:return json.loads(messages[-1])
    except ValueError:raise RuntimeError('Codex returned invalid plan JSON; no task started. Please send again.') from None


def executable():
    supplied=os.environ.get('ROOM_PLANNER_CODEX')
    known=Path('/opt/room/tools/codex-complete-0.164.0/bin/codex')
    candidate=supplied or (str(known) if known.is_file() else shutil.which('codex'))
    if not candidate:raise RuntimeError('Codex CLI not found; install the existing packaged 0.164.0 runtime')
    return str(Path(candidate).resolve())


def probe(binary):
    """No external model/auth used. Reject unexpected tools BEFORE emitting attack calls."""
    with tempfile.TemporaryDirectory(prefix='spatial-planner-check-') as directory:
        root=Path(directory);(root/'home').mkdir()
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','SYSTEMROOT','TMPDIR')}
        env.update(HOME=str(root),CODEX_HOME=str(root/'home'),NO_PROXY='127.0.0.1,localhost')
        version,_=run([binary,'--version'],env=env,timeout=10)
        if version.strip()!=EXPECTED_VERSION:raise RuntimeError('Planner requires verified Codex 0.164.0; found '+version.strip()[:100])
        catalog,_=run([binary,'debug','models','--bundled'],env=env,timeout=15)
        if not any(m.get('slug')==MODEL for m in json.loads(catalog).get('models',[])):
            raise RuntimeError('Bundled planning model missing')
        catalog_file=root/'catalog.json';catalog_file.write_text(catalog)
        # Local fixture proves image input survives the exact no-tool CLI configuration.
        import struct,zlib
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        image_file=root/'evidence.png'
        image_file.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00'+b'\xff\xff\xff'*2+b'\x00'+b'\xff\xff\xff'*2))+chunk(b'IEND',b''))
        marker=root/'must-not-exist'
        attacks=[('exec_command',json.dumps({'cmd':'touch '+str(marker)})),
                 ('apply_patch','*** Begin Patch\n*** Add File: '+str(marker)+'\n+forbidden\n*** End Patch'),
                 ('shell',json.dumps({'command':['touch',str(marker)]}))]
        seen=[];bad=[];inputs=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):self.send_response(404);self.end_headers()
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(body.get('tools') or []);inputs.append(body.get('input'))
                if seen[-1]:bad.append('Planner exposes tools')
                i=len(seen)-1
                item={'type':'message','id':'msg_1','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{"probe":"ok"}'}]}
                if not bad and i<len(attacks):
                    name,args=attacks[i]
                    item={'type':'function_call','id':'fc_'+str(i),'call_id':'call_'+str(i),'name':name,'arguments':args}
                events=[{'type':'response.created','response':{'id':'resp_'+str(i),'status':'in_progress','output':[]}},
                        {'type':'response.output_item.added','output_index':0,'item':item},
                        {'type':'response.output_item.done','output_index':0,'item':item},
                        {'type':'response.completed','response':{'id':'resp_'+str(i),'status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for event in events:self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            args=command(binary,root,catalog_file)+['--image',str(image_file),'-c','model_provider="spatial_probe"',
                '-c','model_providers.spatial_probe.name="Spatial local contract test"',
                '-c','model_providers.spatial_probe.wire_api="responses"',
                '-c','model_providers.spatial_probe.base_url="http://127.0.0.1:'+str(server.server_port)+'/v1"']
            output,_=run(args,prompt='Return JSON only.',env=env,timeout=40)
            if bad or len(seen)!=len(attacks)+1 or marker.exists() or parse_events(output)!={'probe':'ok'}:
                raise RuntimeError('Codex no-tool planning contract failed; planning is disabled')
            if 'input_image' not in json.dumps(inputs[0]):raise RuntimeError('Codex image-input contract failed; inspection disabled')
            outputs={item.get('call_id'):item.get('output') for batch in inputs for item in (batch or []) if item.get('type')=='function_call_output'}
            for i,(name,_) in enumerate(attacks):
                accepted=('unsupported call: '+name,'aborted') if name=='apply_patch' else ('unsupported call: '+name,)
                if outputs.get('call_'+str(i)) not in accepted:
                    raise RuntimeError('Codex did not reject all probe calls; planning disabled')
        finally:server.shutdown();server.server_close()
        return catalog


def ensure_runtime():
    global _verified
    binary=executable();stat=Path(binary).stat();identity=(binary,stat.st_size,stat.st_mtime_ns)
    with _probe_mutex:
        if _verified is None or _verified[0]!=identity:
            catalog=probe(binary)
            _verified=(identity,catalog)
            print('CONVERSATION_BOUNDARY_PASS: Codex 0.164.0; zero tools; image input verified; unsolicited commands/patch rejected',flush=True)
        return binary,_verified[1]


def plan(prompt,images=(),context=None):
    binary,catalog=ensure_runtime()
    with tempfile.TemporaryDirectory(prefix='spatial-planner-turn-') as directory:
        root=Path(directory);path=root/'catalog.json';path.write_text(catalog)
        # Existing CODEX_HOME/auth are used here. No credential copy or persistent config changes.
        schema=root/'response.schema.json';schema.write_text(json.dumps(bound_schema(context) if context is not None else OUTPUT_SCHEMA))
        args=command(binary,root,path)+['--output-schema',str(schema)]
        for image in images:args+=['--image',str(image)]
        output,_=run(args,prompt=prompt,timeout=120)
        return parse_events(output)


class Planner:
    """Production adapter binds each turn's schema without shared mutable state."""
    def __call__(self,prompt,images=()):
        return plan(prompt,images=images)

    def with_context(self,facts):
        from functools import partial
        return partial(plan,context=facts)


if __name__=='__main__':
    ensure_runtime()
