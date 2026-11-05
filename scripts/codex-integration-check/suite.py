"""Independent, bounded integration checks; never equate agent prose with an artifact."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
CORE_CHECKS=('host_fs','sandbox','mcp_local','capability_registry','text','vision','mcp_model','shell','patch','host_cpu','host_cuda','blender_agent')
RUNTIME_CHECKS=('host_fs','sandbox','shell','patch','host_cpu','host_cuda','blender_agent','eevee_gpu')


def verdict(completed,assertions):return 'passed' if completed and bool(assertions) and all(assertions.values()) else 'failed'
def overall_status(checks):return 'core_checks_passed' if all(checks.get(k,{}).get('status')=='passed' for k in CORE_CHECKS) else 'checks_incomplete'
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def exact_patch(value):
    return '*** Begin Patch\n*** Update File: seed.txt\n@@\n-BEFORE\n+'+value+'\n*** Add File: patch_result.txt\n+'+value+'\n*** End Patch'


def mcp_approval_blocked(events):
    for event in events:
        if event.get('kind')!='raw_event':continue
        try:
            item=json.loads(event.get('event','{}')).get('item',{})
            message=str((item.get('error') or {}).get('message',''))
            if item.get('type')=='mcp_tool_call' and item.get('status')=='failed' and 'requires approval' in message:return True
        except (ValueError,TypeError,AttributeError):continue
    return False


def render_artifacts(folder,mode):
    result={'passed':False}
    try:
        data=json.loads((folder/(mode+'.json')).read_text())
        with (folder/(mode+'.png')).open('rb') as f:png=f.read(8)==b'\x89PNG\r\n\x1a\n'
        valid=data.get('status')=='passed' and data.get('mode')==mode and png
        if mode=='cpu':
            with (folder/'test_cube.glb').open('rb') as f:valid=valid and f.read(4)==b'glTF'
            valid=valid and (folder/'test_cube.blend').is_file()
        result.update(passed=valid,details=data,software_renderer=any(v in str(data.get('renderer','')).lower() for v in ('llvmpipe','softpipe','software rasterizer')))
    except (OSError,ValueError):pass
    return result


def write_fixture(work):
    script=work/'write_fixture.py'
    script.write_text('import os,sys,json\nfrom pathlib import Path\n'
        +'root=Path(__file__).resolve().parent\nprint(json.dumps({"cwd":os.getcwd(),"uid":os.geteuid(),"gid":os.getegid()}))\n'
        +'temp=root/"shell_result.tmp"\nwith temp.open("w") as f:f.write(sys.argv[1]);f.flush();os.fsync(f.fileno())\n'
        +'temp.replace(root/"shell_result.txt")\nassert (root/"shell_result.txt").read_text()==sys.argv[1]\nprint("WRITE_READ_RENAME_OK")\n')
    return script


async def main_async(report,root,runtime_only=False):
    from PIL import Image,ImageDraw
    from litereality_agent.agent import providers
    from litereality_agent.agent.room_codex import parse_json,redact,session_diagnostics
    from helpers import inspect_file,trace_message,directory_info
    from graphics_probe import probe as graphics_probe
    summary={'checks':{},'model':'gpt-6-astra','model_sessions':0,'max_model_sessions':4 if runtime_only else 7,'complete_pipeline_ready':False,'runtime_only':runtime_only}
    checks=summary['checks'];workroot=report/'work';workroot.mkdir()
    def checkpoint():
        summary['status']=('runtime_checks_passed' if all(checks.get(k,{}).get('status')=='passed' for k in RUNTIME_CHECKS) else 'checks_incomplete') if runtime_only else overall_status(checks)
        save(report/'suite.json',summary)
    async def case(name,operation):
        print('CHECK '+name,flush=True);start=time.monotonic()
        try:
            data=await operation();data.setdefault('status','passed')
        except Exception as exc:
            data={'status':'failed','error_type':type(exc).__name__,'error':redact(str(exc))}
        data['seconds']=round(time.monotonic()-start,2);checks[name]=data;checkpoint()
        print(name+': '+data['status'],flush=True)
        return data
    def skip(name,reason):checks[name]={'status':'skipped','reason':reason};checkpoint()
    def folder(name):p=workroot/name;p.mkdir();return p
    async def session(name,work,prompt,*,images=(),mcp=None,read_only=False,timeout=180):
        summary['model_sessions']+=1
        if summary['model_sessions']>summary['max_model_sessions']:raise RuntimeError('Model session cap exceeded')
        spec=providers.SessionSpec(prompt='Diagnostic test: stay in the supplied workspace, never request elevated permissions, change permissions/mounts, or bypass the sandbox. Report actual failures honestly.\n'+prompt,
            cwd=work,image_paths=tuple(images),read_only=read_only,setting_sources=(),stdio_mcp=mcp or {},timeout_seconds=timeout,step_budget=24)
        terminal=None;events=[]
        with (report/(name+'_events.jsonl')).open('w') as log:
            async for message in providers.resolve(override='codex').run(spec):
                for record in trace_message(message,providers,redact):
                    events.append(record);log.write(json.dumps(record,ensure_ascii=False)+'\n');log.flush()
                if isinstance(message,providers.SessionResult):terminal=message
        detail=session_diagnostics(terminal);save(report/(name+'_session.json'),detail)
        done=bool(terminal and not terminal.is_error and not terminal.stopped)
        return done,terminal.result if terminal else '',events,detail
    def used(events,name):return any(e['kind']=='tool_use' and name in e['name'] for e in events)
    def successful_mcp(events):
        ids={e['id'] for e in events if e['kind']=='tool_use' and 'get_probe_data' in e['name']}
        return any(e['kind']=='tool_result' and e['id'] in ids and not e['is_error'] for e in events)
    def command(args):return ' '.join(shlex.quote(str(a)) for a in args)
    async def process(name,args,cwd,timeout,env=None):
        with (report/(name+'.log')).open('w') as f:
            proc=await asyncio.create_subprocess_exec(*map(str,args),cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT)
            try:code=await asyncio.wait_for(proc.wait(),timeout)
            except asyncio.TimeoutError:proc.kill();await proc.wait();return {'status':'failed','error':'timeout'}
        return {'status':'passed' if code==0 else 'failed','exit_code':code}

    async def host_fs():
        work=folder('host_fs');script=write_fixture(work)
        result=await process('host_fs',[sys.executable,script,'HOST_FS_OK'],work,30)
        result.update(directory=directory_info(work),file=inspect_file(work/'shell_result.txt','HOST_FS_OK'))
        result['status']=verdict(result.get('exit_code')==0,{'file':result['file']['content_matches']});return result
    await case('host_fs',host_fs)
    async def sandbox_case():
        from sandbox_probe import probe
        return await asyncio.to_thread(probe,report,workroot)
    await case('sandbox',sandbox_case)
    try:summary['graphics']=graphics_probe()
    except Exception as exc:summary['graphics']={'error_type':type(exc).__name__}
    save(report/'graphics_details.json',summary['graphics'])
    summary['external_readiness']={
        'image_api_key_present':{k:bool(os.environ.get(k)) for k in ('OPENAI_API_KEY','GEMINI_API_KEY')},
        'model_interpreters':{k:{'configured':bool(os.environ.get(k)),'file_exists':bool(os.environ.get(k) and Path(os.environ[k]).is_file())} for k in ('TRELLIS_PYTHON','GROUNDING_DINO_PYTHON','LITEREALITY_TRELLIS_PYTHON','LR_DINO_PYTHON')},
        'note':'Presence only: no image-generation API, checkpoint loading or GPU model inference is tested.'}
    checkpoint()
    vision=folder('vision');picture=vision/'card.png';im=Image.new('RGB',(320,200),'white');d=ImageDraw.Draw(im)
    d.rectangle((30,60,100,130),fill='red');d.ellipse((200,60,270,130),fill='blue');im.save(picture)
    expected={'left_shape':'square','left_color':'red','right_shape':'circle','right_color':'blue'}
    schema={'type':'object','required':list(expected),'additionalProperties':False,'properties':{k:{'type':'string'} for k in expected}}
    mcpwork=folder('mcp_model');nonce=secrets.token_hex(12);state=workroot/'mcp_state.json'
    save(state,{'nonce':nonce,'image_path':str(picture)})
    server_args=[str(HERE/'probe_mcp.py'),str(state)]
    async def stdio_check(registry=False):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        if registry:args=['-m','litereality_agent.agent.tools.mcp_server','--scene',str(mcpwork)]
        else:args=server_args
        params=StdioServerParameters(command=sys.executable,args=args,env={'PYTHONPATH':os.environ.get('PYTHONPATH',''),'LR_REPO_ROOT':os.environ.get('LR_REPO_ROOT','')})
        async def call():
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize();listed=await client.list_tools();names=[t.name for t in listed.tools]
                    if registry:return {'status':'passed' if names else 'failed','tools':names,'invocation_tested':False}
                    annotations={t.name:(t.annotations.model_dump() if t.annotations else None) for t in listed.tools}
                    output=await client.call_tool('get_probe_data',{})
                    texts=[c.text for c in output.content if getattr(c,'type',None)=='text']
                    data=json.loads('\n'.join(texts))
                    return {'status':verdict(not output.isError,{'nonce':data.get('nonce')==nonce}),'tools':names,'annotations':annotations}
        return await asyncio.wait_for(call(),60)
    if not runtime_only:
        await case('mcp_local',lambda:stdio_check(False))
        await case('capability_registry',lambda:stdio_check(True))
    else:
        for name in ('mcp_local','capability_registry','text','vision','mcp_model'):skip(name,'Runtime-only retest; previously passed checks are not rerun or counted as newly passed.')

    async def text_case():
        done,text,events,detail=await session('text',folder('text'),'Reply only JSON {"value":"ROOM_SUITE_TEXT_OK"}. Do not call tools.',read_only=True)
        answer=parse_json(text);return {'status':verdict(done,{'json':answer=={'value':'ROOM_SUITE_TEXT_OK'}}),'answer':answer,'diagnostics':detail}
    if not runtime_only:await case('text',text_case)
    async def vision_case():
        done,text,events,detail=await session('vision',vision,'Describe the attached left/right colored shapes in lowercase English. Return only JSON matching '+json.dumps(schema),images=[picture],read_only=True)
        answer=parse_json(text,schema);return {'status':verdict(done,{'answer':answer==expected}),'answer':answer,'diagnostics':detail}
    if not runtime_only:await case('vision',vision_case)
    async def mcp_case():
        prompt='Call the local MCP get_probe_data tool once, view the image at the returned image_path with your image-viewing tool, and return only JSON {"nonce":the returned nonce,"left_color":color on left,"right_color":color on right}. Do not read the backing state file. Use lowercase English colors.'
        done,text,events,detail=await session('mcp_model',mcpwork,prompt,read_only=True,mcp={'roomprobe':{'command':sys.executable,'args':server_args}})
        if mcp_approval_blocked(events):
            return {'status':'blocked','reason':'MCP invocation requires approval and did not execute; this is not a model-login or filesystem-write verdict.',
                'assertions':{'mcp_result':False},'diagnostics':detail}
        answer=parse_json(text);assertions={'mcp_result':successful_mcp(events),'nonce':answer.get('nonce')==nonce,'colors':answer.get('left_color')=='red' and answer.get('right_color')=='blue'}
        return {'status':verdict(done,assertions),'assertions':assertions,'diagnostics':detail}
    if not runtime_only:await case('mcp_model',mcp_case)

    async def writes_case(name,work,shell=True,patch=True):
        value=secrets.token_hex(12);instructions=[]
        if shell:
            script=write_fixture(work);instructions.append('Use a shell tool to run exactly '+command([sys.executable,script,value])+'. Keep any real error output. Do not use patch/edit tools for shell_result.txt.')
        if patch:
            (work/'seed.txt').write_text('BEFORE\n')
            instructions.append('Independently invoke apply_patch with this exact patch. Each file appears once; do not delete and re-add seed.txt or generate an alternative patch. Do not use shell/Python to write these files. Continue even if shell failed.\n'+exact_patch(value))
        instructions.append('Read back the results when possible. Report each actual result, not just DONE. All paths are inside '+str(work))
        done,text,events,detail=await session(name,work,'\n'.join(instructions))
        assertions={};files={}
        if shell:
            files['shell']=inspect_file(work/'shell_result.txt',value);assertions.update(shell_tool=used(events,'Bash'),shell_file=files['shell']['content_matches'])
        if patch:
            files['patch']=inspect_file(work/'patch_result.txt',value);files['edited_seed']=inspect_file(work/'seed.txt',value)
            assertions.update(patch_tool=used(events,'Edit'),patch_file=files['patch']['content_matches'],edited_seed=files['edited_seed']['content_matches'])
        observations={}
        if shell:observations['shell']=used(events,'Bash')
        if patch:observations['patch']=used(events,'Edit')
        return {'status':verdict(done,assertions),'assertions':assertions,'files':files,'directory':directory_info(work),'diagnostics':detail,
            'tool_invocation_observed':observations,
            'interpretation':'An unobserved tool invocation is not proof that the filesystem rejects writes; inspect raw events separately from the model final message.'}
    if checks['sandbox']['status']=='passed':
        await case('shell',lambda:writes_case('shell',folder('shell'),shell=True,patch=False))
        await case('patch',lambda:writes_case('patch',folder('patch'),shell=False,patch=True))
        if any(checks[k]['status']!='passed' for k in ('shell','patch')):
            with tempfile.TemporaryDirectory(prefix='room-codex-suite-',dir='/tmp') as tmp:
                await case('tmp_compare',lambda:writes_case('tmp_compare',Path(tmp)))
        else:skip('tmp_compare','Project writes passed; no filesystem comparison is needed.')
    else:
        for name in ('shell','patch','tmp_compare'):skip(name,'Direct sandbox probe failed or is unsupported; resolve sandbox first. No model session was spent on this check.')

    blender=root/'return_output/tools/blender-5.1.2-linux-x64/blender'
    if blender.is_file():
        from graphics_repair import prepare,nvidia_render_ok
        async def host_render(mode,name,env=None):
            output=report/'renders'/name;output.mkdir(parents=True)
            execution=await process(name,[blender,'--background','--factory-startup','--python-exit-code','1','--python',HERE/'blender_smoke.py','--','--mode',mode,'--output',output],output,180,env=env)
            artifacts=render_artifacts(output,mode);status=verdict(execution.get('exit_code')==0,{'artifacts':artifacts['passed']})
            if status=='passed' and artifacts.get('software_renderer'):status='software_only'
            return {'status':status,'execution':execution,'artifacts':artifacts}
        for mode in ('cpu','cuda','eevee'):
            await case('host_'+mode,lambda mode=mode:host_render(mode,'host_'+mode))
        gpu_ok=checks['host_eevee']['status']=='passed' and nvidia_render_ok(checks['host_eevee'].get('artifacts',{}))
        if not gpu_ok:
            candidate_env=None
            async def repair_candidate():
                nonlocal candidate_env
                data,candidate_env=await asyncio.to_thread(prepare,report,summary['graphics'])
                save(report/'graphics_repair.json',data);return data
            await case('graphics_repair',repair_candidate)
            if candidate_env:
                await case('host_eevee_repaired',lambda:host_render('eevee','host_eevee_repaired',candidate_env))
                await case('host_cuda_repaired',lambda:host_render('cuda','host_cuda_repaired',candidate_env))
                gpu_ok=(checks['host_eevee_repaired']['status']=='passed' and nvidia_render_ok(checks['host_eevee_repaired'].get('artifacts',{})) and checks['host_cuda_repaired']['status']=='passed')
                if gpu_ok:
                    updates=checks['graphics_repair']['environment_updates']
                    activation=report/'activate_graphics.sh'
                    activation.write_text('# Verified for the current host driver only; retest after changing containers.\n'+''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in updates.items()))
                    summary['graphics_activation']=str(activation)
        checks['eevee_gpu']={'status':'passed' if gpu_ok else 'blocked','reason':'Actual NVIDIA renderer verified (and CUDA regression after candidate repair).' if gpu_ok else 'NVIDIA EEVEE is still unverified; inspect library diagnostics and render logs. Software EEVEE is reported separately.'};checkpoint()
        if checks['shell']['status']=='passed' and checks['host_cpu']['status']=='passed':
            async def agent_render():
                work=folder('blender_agent');script=work/'blender_smoke.py';shutil.copyfile(HERE/'blender_smoke.py',script)
                output=work/'render';output.mkdir()
                cmd=command([blender,'--background','--factory-startup','--python-exit-code','1','--python',script,'--','--mode','cpu','--output',output])
                done,text,events,detail=await session('blender_agent',work,'Use a shell tool to run exactly '+cmd+' . Do not modify the supplied script. Inspect the exit code and generated files; report failures honestly.',timeout=240)
                artifacts=render_artifacts(output,'cpu');target=report/'renders'/'blender_agent';target.mkdir()
                for name in ('cpu.json','cpu.png','test_cube.glb'):
                    source=output/name
                    if source.is_file() and not source.is_symlink() and source.stat().st_size<5*1024**2:shutil.copyfile(source,target/name)
                return {'status':verdict(done,{'shell_tool':used(events,'Bash'),'artifacts':artifacts['passed']}),'artifacts':artifacts,'diagnostics':detail}
            await case('blender_agent',agent_render)
        else:skip('blender_agent','Requires successful project shell write and host CPU render; other checks have completed.')
    else:
        for name in ('host_cpu','host_cuda','host_eevee','blender_agent','eevee_gpu'):skip(name,'Installed Blender binary is missing.')
    summary['remaining']=['GPU EEVEE must be judged from renderer, not just a successful image.',
        'Image-generation API and Grounding DINO/TRELLIS inference are not tested by this package.',
        'Full room reconstruction remains untested.']
    summary['finished']=True
    checkpoint();return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--project-root',type=Path,required=True);p.add_argument('--runtime-only',action='store_true');args=p.parse_args()
    result=asyncio.run(main_async(args.report.resolve(),args.project_root.resolve(),args.runtime_only))
    print('SUITE STATUS: '+result['status'],flush=True)
    return 0 if result['status'] in ('core_checks_passed','runtime_checks_passed') else 1

if __name__=='__main__':raise SystemExit(main())
