"""Reproducible isolated Codex fork of the exact upstream archive already installed."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

REVISION='28ffe1fd4a1b03c93f1877a33a8bc9b806f72fe1'
SOURCE_SHA='b8f70258dda704a38df8091dd4f96604dd4b7a9ab14e296ef4962c84489773b1'
FORK='litereality-agent-28ffe1f-room-codex-v0.1.2'
HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patch_source(root):
    pkg=root/'src/litereality_agent';changed={}
    def put(rel,text):
        p=root/rel;p.parent.mkdir(parents=True,exist_ok=True)
        if rel not in changed:changed[rel]={'before':sha(p) if p.exists() else None}
        p.write_text(text);changed[rel]['after']=sha(p)
    def replace(rel,old,new):
        text=(root/rel).read_text()
        if text.count(old)!=1:raise ValueError('Patch context mismatch: '+rel+' '+old[:70])
        put(rel,text.replace(old,new))
    def function(rel,name,body):
        text=(root/rel).read_text();tree=ast.parse(text)
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
        if len(nodes)!=1:raise ValueError('Function context mismatch: '+rel+' '+name)
        n=nodes[0];lines=text.splitlines(keepends=True)
        put(rel,''.join(lines[:n.lineno-1])+body.rstrip()+'\n'+''.join(lines[n.end_lineno:]))
    prefix='src/litereality_agent/'
    put(prefix+'agent/room_codex.py',(HERE/'bridge.py').read_text())
    put(prefix+'pipeline/scene_init/reconstruct/classify/classify_codex.py',(HERE/'classifier.py').read_text())
    put(prefix+'pipeline/scene_init/reconstruct/classify/classify_claude.py',
        '"""Legacy import alias; this fork always uses Codex."""\nfrom .classify_codex import classify_image\n')
    put(prefix+'pipeline/scene_init/reconstruct/classify/classification.py','''"""Codex-only classification in the Room fork."""
import os
DEFAULT_CLASSIFY_MODEL='codex'
def provider():
    selected=os.environ.get('LR_CLASSIFY_PROVIDER','codex').strip().lower()
    if selected!='codex':raise ValueError('This Room profile requires Codex classification')
    return selected
def classify_image(*args,**kwargs):
    provider()
    from .classify_codex import classify_image as classify
    return classify(*args,**kwargs)
''')
    base=prefix+'agent/providers/base.py'
    replace(base,'PROVIDERS = ("claude", "codex")','PROVIDERS = ("codex",)')
    replace(base,'DEFAULT_PROVIDER = "claude"','DEFAULT_PROVIDER = "codex"')
    function(base,'resolve','''def resolve(role="", override=None):
    provider_name(role,override)
    from litereality_agent.agent.providers.codex import CodexHarness
    return CodexHarness()''')
    replace(base,'    prompt: str\n    cwd: Path\n','    prompt: str\n    cwd: Path\n    image_paths: tuple[Path, ...] = ()\n')
    codex=prefix+'agent/providers/codex.py'
    replace(codex,'else ["--dangerously-bypass-approvals-and-sandbox"]',
        'else ["--sandbox", "workspace-write", "-c", \'approval_policy="never"\']')
    replace(codex,'            "--json",\n','            "--json",\n            "--ephemeral",\n            "--ignore-user-config",\n')
    replace(codex,'or set LR_AGENT_PROVIDER=claude.','and authenticate Codex before retrying.')
    replace(codex,'        cmd.append(_budgeted_prompt(spec, seconds))',
        '        for image_path in spec.image_paths:\n            cmd += ["--image", str(image_path)]\n        cmd += ["--", _budgeted_prompt(spec, seconds)]')
    replace(codex,'"  ⚠ codex produced no recognisable events — the --json event shape may have "\n                "changed; see agent/providers/codex.py:_normalise"',
        '"  ⚠ codex produced no recognisable events; inspect exit status and stderr. "\n                "This alone does not establish a JSON format change."')
    # Required MCP servers must fail visibly instead of silently continuing without tools.
    replace(codex,'        for key in ("command", "args"):',
        '        extra += ["-c", f"mcp_servers.{name}.required=true"]\n        for key in ("command", "args"):')
    replace(codex,'    return extra + [\n        "-c",',
        '    return extra + [\n        "-c", "mcp_servers.cap.required=true",\n        "-c",')
    replace(codex,'                blocks.append(ToolResultBlock(tool_use_id=cid, content="applied"))',
        '''                failed = item.get("status") != "completed"
                detail = _text_of(item.get("error") or item.get("aggregated_output"))
                blocks.append(ToolResultBlock(tool_use_id=cid,
                    content=detail or ("file change failed or incomplete" if failed else "applied"), is_error=failed))''')
    replace(codex,'content=_text_of(item.get("result") or item.get("output")),',
        'content=_text_of(item.get("error") or item.get("result") or item.get("output")),')
    replace(codex,'is_error=bool(item.get("exit_code")),',
        'is_error=item.get("status") == "failed" or item.get("exit_code") != 0,')
    replace(codex,'        terminal_error = False',
        '        terminal_error = False\n        pending_error = False\n        completed_turn = False\n        final_answer = False\n        stream_errors = []')
    replace(codex,'                    turns += 1\n                    usage = event.get("usage") or usage',
        '                    turns += 1\n                    completed_turn = True\n                    pending_error = False\n                    usage = event.get("usage") or usage')
    replace(codex,'                if event.get("type") in {"turn.failed", "error"}:\n                    terminal_error = True',
        '''                if event.get("type") in {"turn.failed", "error"}:
                    stream_errors.append(event)
                    pending_error = True
                    if event.get("type") == "turn.failed":
                        terminal_error = True
                item = event.get("item") or {}
                if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                    summary = str(item.get("text") or "")
                    final_answer = bool(summary.strip())''')
    replace(codex,'                for block in blocks:\n                    if isinstance(block, TextBlock) and block.text.strip():\n                        summary = block.text\n                if blocks:\n                    yield AgentMessage(content=blocks, raw=event)',
        '                # Preserve even unknown/error/terminal events for diagnosis.\n                yield AgentMessage(content=blocks, raw=event)')
    replace(codex,'        failed = terminal_error or (proc.returncode not in (0, None) and not stopped)',
        '        failed = terminal_error or pending_error or not completed_turn or not final_answer or proc.returncode != 0')
    replace(codex,'                "returncode": proc.returncode,',
        '                "returncode": proc.returncode,\n                "stream_errors": stream_errors,\n                "completed_turn": completed_turn,\n                "pending_error": pending_error,')
    put(prefix+'agent/providers/claude.py','''"""Claude execution is disabled in this isolated Codex fork."""
def build_capability_server(*args,**kwargs):
    raise RuntimeError('Use the Codex stdio MCP capability bridge')
class ClaudeHarness:
    def __init__(self):raise RuntimeError('Claude is disabled; no fallback is allowed')
''')
    refine=prefix+'pipeline/realism_authoring/author/refine_objects.py'
    function(refine,'build_server','''def build_server(*args,**kwargs):
    raise RuntimeError('This profile uses the existing Codex object_server stdio bridge')''')
    settings=prefix+'settings.py'
    text=(root/settings).read_text().replace('default="claude"','default="codex"').replace('or "claude"','or "codex"')
    text=text.replace('"claude-opus-5"','"gpt-6-astra"');put(settings,text)
    # Role overrides in a copied .env cannot accidentally select Claude in this fork.
    models=(root/'models.env').read_text().replace(':-claude}',':-codex}').replace('claude-opus-5','gpt-6-astra')
    split=models.index('# ── OpenAI —')
    models=('# Room Codex-only profile. LR_CODEX_MODEL selects the actual agent model.\n'
        '# Codex account limits still apply; monetary cost is not reported.\n'
        +'\n'.join(line for line in models[:split].splitlines() if line.startswith('export '))
        +'\n\n'+models[split:])
    put('models.env',models)
    chair=prefix+'pipeline/scene_init/ingest/references/chair_type_judge.py'
    function(chair,'available','''def available():
    import shutil
    return shutil.which('codex') is not None''')
    function(chair,'_run_agent','''async def _run_agent(montage_dir,names,model):
    from litereality_agent.agent.room_codex import ask,parse_json
    images=[montage_dir/(name+'.jpg') for name in names]
    prompt=_build_prompt(montage_dir,names).replace('with the Read tool','from the attached images')
    output=await ask(prompt,cwd=montage_dir,images=images,timeout=JUDGE_TIMEOUT)
    schema={'type':'object','required':['types'],'properties':{'types':{'type':'array','items':{
        'type':'object','required':['members'],'properties':{'members':{'type':'array','items':{'type':'string'}}}}}}}
    return parse_json(output,schema),None''')
    replace(chair,'round(cost, 4)',"'unknown' if cost is None else round(cost, 4)")
    replace(chair,'DEFAULT_JUDGE_MODEL = os.environ.get("LR_CHAIR_JUDGE_MODEL", "claude-opus-5")',
        'DEFAULT_JUDGE_MODEL = os.environ.get("LR_CODEX_MODEL", "gpt-6-astra")')
    # Keep existing deterministic bounds and conservative no-merge behavior in layout code.
    evidence=prefix+'pipeline/scene_init/layout/evidence.py'
    replace(evidence,"        done = subprocess.run(['claude', '-p', prompt, '--output-format', 'text', '--model', model,\n                               '--allowed-tools', 'Read'], capture_output=True, text=True, timeout=180)",
        "        from litereality_agent.agent.room_codex import run_text\n        done = run_text(prompt,cwd=scene_dir,images=[p for values in refs.values() for p in values],timeout=180)")
    replace(evidence,'except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired)',
        'except (OSError, ValueError, TypeError, AttributeError, RuntimeError, subprocess.TimeoutExpired)')
    agent=prefix+'pipeline/scene_init/layout/agent.py'
    replace(agent,'        done = subprocess.run(\n            ["claude", "-p", prompt, "--output-format", "text", "--model", model,\n             "--allowed-tools", "Read"],\n            capture_output=True, text=True, timeout=TIMEOUT)',
        '        from litereality_agent.agent.room_codex import run_text\n        done = run_text(prompt,cwd=batch_dir,images=context["reference_images"],timeout=TIMEOUT)')
    replace(agent,'except (subprocess.TimeoutExpired, OSError):','except (subprocess.TimeoutExpired, OSError, RuntimeError):')
    loop=prefix+'pipeline/scene_init/layout/agent_loop.py'
    replace(loop,'        done = subprocess.run(\n            ["claude", "-p", brief, "--output-format", "text", "--model", model,\n             "--allowed-tools", "Read", "Bash", "Glob"],\n            capture_output=True, text=True, timeout=TIMEOUT, cwd=str(session.parent))',
        '        from litereality_agent.agent.room_codex import run_text\n        done = run_text(brief,cwd=session,read_only=False,timeout=TIMEOUT)')
    replace(loop,'except subprocess.TimeoutExpired:','except (subprocess.TimeoutExpired, RuntimeError):')
    complete=prefix+'models/object_generation/articulated-glb-agent/.claude/skills/image-to-articulated-glb/scripts/completeness_check.py'
    function(complete,'_ask','''async def _ask(images):
    from litereality_agent.agent.room_codex import ask
    return await ask(PROMPT,cwd=images[0].resolve().parent,images=images,timeout=150)''')
    function(complete,'_parse','''def _parse(output):
    from litereality_agent.agent.room_codex import parse_json
    return parse_json(output,{'type':'object','required':['pass','missing','notes'],
        'properties':{'pass':{'type':'boolean'},'missing':{'type':'array','items':{'type':'string'}},'notes':{'type':'string'}}})''')
    text=(root/complete).read_text().replace('"pass": True','"pass": False').replace('data.setdefault("pass", True)','data.setdefault("pass", False)')
    put(complete,text)
    replace(complete,'    return 0 if report.get("pass", True) else 1','    return 2 if report.get("error") else (0 if report.get("pass", False) else 1)')
    generation=prefix+'models/object_generation/generate.py'
    # A provider failure must not masquerade as a successful completeness review.
    text=(root/generation).read_text().replace('{"pass": True, "missing": [], "notes": "no reference or renders"}',
        '{"pass": False, "missing": [], "notes": "no reference or renders", "error": "missing_evidence"}')
    text=text.replace('{"pass": True, "missing": [], "notes": f"completeness error: {type(e).__name__}"}',
        '{"pass": False, "missing": [], "notes": f"completeness error: {type(e).__name__}", "error": "judge_failed"}')
    put(generation,text)
    batch=prefix+'models/object_generation/articulated-glb-agent/batch_glb.py'
    replace(batch,'from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query',
        'from litereality_agent.agent import providers')
    function(batch,'run_one','''async def run_one(job,args):
    job.glb.parent.mkdir(parents=True,exist_ok=True)
    skill=PACKAGE_ROOT/'.claude/skills'/SKILL_NAME/'SKILL.md'
    prompt=skill.read_text()+'\\n\\n'+PROMPT_TEMPLATE.format(skill=SKILL_NAME,image=job.image,glb=job.glb)
    prompt+='\\nSkill helper scripts are in '+str(skill.parent/'scripts')
    spec=providers.SessionSpec(prompt=prompt,cwd=job.glb.parent,image_paths=(job.image,),
        read_roots=(job.glb.parent,),step_budget=args.max_turns,timeout_seconds=1800)
    complete=False
    async for message in providers.resolve(override='codex').run(spec):
        if isinstance(message,providers.SessionResult):
            complete=not message.is_error and not message.stopped
            job.log.append({'attempt':job.attempts,'is_error':not complete,'cost_usd':None,
                'result_tail':message.result[-300:]})
    if not complete:raise RuntimeError('Codex generation incomplete')''')
    text=(root/batch).read_text();start=text.index('    if not os.environ.get("ANTHROPIC_API_KEY"):');end=text.index('    sem = asyncio.Semaphore',start)
    text=text[:start]+'    print("Codex CLI profile; monetary cost is not reported.")\n\n'+text[end:]
    text=text.replace('cost_usd: float = 0.0','cost_usd: float | None = None').replace('${job.cost_usd:.2f}','$unknown')
    text=text.replace('total_cost = sum(j.cost_usd for j in jobs)','total_cost = None').replace('${total_cost:.2f}','$unknown')
    text=text.replace('round(j.cost_usd, 4)','None');put(batch,text)
    sanity='sanity.py';text=(root/sanity).read_text()
    start=text.index('        if picked["refine"] == "codex":');end=text.index('\n    if "providers" in wanted:',start)
    text=text[:start]+'        if picked["refine"] == "codex":\n            ok("Codex refinement uses object_server over stdio MCP")\n'+text[end:]
    start=text.index('        if os.environ.get("LR_CLASSIFY_PROVIDER", "claude").lower() != "claude":');end=text.index('\n    if "gen3d" in wanted:',start)
    text=text[:start]+'        ok("Room fork: classification and visual reasoning use Codex")\n'+text[end:]
    text=text.replace('setdefault("LR_CLASSIFY_PROVIDER", "claude")','setdefault("LR_CLASSIFY_PROVIDER", "codex")').replace('setdefault("HARNESS_VLM", "claude")','setdefault("HARNESS_VLM", "codex")')
    text=text.replace('or "claude").strip().lower()','or "codex").strip().lower()')
    text=text.replace('env=("LR_AGENT_PROVIDER", "claude", "set this to get the full-capability harness")','note="Room fork keeps Codex; step limits are hard stops and monetary cost is unknown"')
    put(sanity,text)
    # Public choices and recorded model defaults must agree with the actual provider.
    for p in pkg.rglob('*.py'):
        text=p.read_text()
        revised=text.replace('choices=["claude", "codex"]','choices=["codex"]')
        revised=revised.replace('= ("claude", "codex")','= ("codex",)')
        revised=revised.replace('"claude-opus-5"','"gpt-6-astra"')
        revised=revised.replace('os.environ.get("LR_CLASSIFY_PROVIDER", "claude")','os.environ.get("LR_CLASSIFY_PROVIDER", "codex")')
        revised=revised.replace('os.environ.get("HARNESS_VLM", "claude")','os.environ.get("HARNESS_VLM", "codex")')
        if revised!=text:put(p.relative_to(root).as_posix(),revised)
    for p in root.rglob('*.py'):compile(p.read_text(),str(p),'exec')
    (root/'room_codex_patch.json').write_text(json.dumps({'version':'0.1.2','upstream':REVISION,'builder_files':{n:sha(HERE/n) for n in ('build_fork.py','bridge.py','classifier.py')},'files':changed},indent=2)+'\n')
    return changed


def build(root):
    archive=root/'scripts/litereality-agent-prepare/upstream-source.tar.gz'
    if not archive.is_file() or sha(archive)!=SOURCE_SHA:raise ValueError('Pinned upstream archive missing or changed; install prepare v0.2.1 first')
    target=root/'scripts/vendor'/FORK
    if target.exists():
        manifest=json.loads((target/'room_codex_patch.json').read_text())
        if manifest.get('builder_files')!={n:sha(HERE/n) for n in ('build_fork.py','bridge.py','classifier.py')}:raise ValueError('Fork builder changed; use a new versioned fork directory')
        for rel,item in manifest['files'].items():
            if sha(target/rel)!=item['after']:raise ValueError('Existing fork modified: '+rel)
        return target
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='room-codex-',dir=target.parent) as tmp:
        work=Path(tmp)/'source';work.mkdir()
        with tarfile.open(archive) as tar:
            for m in tar.getmembers():
                parts=Path(m.name).parts
                if Path(m.name).is_absolute() or '..' in parts or not(m.isfile() or m.isdir()):raise ValueError('Unsafe upstream archive')
                if len(parts)<2:continue
                dest=work.joinpath(*parts[1:])
                if m.isdir():dest.mkdir(parents=True,exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(m) as src,dest.open('wb') as out:shutil.copyfileobj(src,out)
                    dest.chmod(0o755 if m.mode&0o111 else 0o644)
        patch_source(work);work.rename(target)
    return target


if __name__=='__main__':
    import sys
    print(build(Path(sys.argv[1]).resolve()))
