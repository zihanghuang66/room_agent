"""Collect capabilities and unauthenticated HTTPS reachability, never credentials."""
from concurrent.futures import ThreadPoolExecutor
import os
import platform
import shutil
import subprocess
import urllib.request
import urllib.error

ENDPOINTS={
    'pypi':'https://pypi.org/simple/uv/',
    'huggingface':'https://huggingface.co/api/models/IDEA-Research/grounding-dino-tiny',
    'github':'https://api.github.com/repos/LiteReality/LiteReality-Agent',
    'openai':'https://api.openai.com/v1/models',
    'anthropic':'https://api.anthropic.com/v1/models',
    'gemini':'https://generativelanguage.googleapis.com/v1beta/models',
}


def endpoint(item):
    name,url=item
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'Room-connectivity-probe'})
        with urllib.request.urlopen(request,timeout=12) as response:status=response.status
        result=dict(http_status=status,http_response_received=True)
    except urllib.error.HTTPError as exc:
        result=dict(http_status=exc.code,http_response_received=True)
    except Exception as exc:
        # No raw headers, response bodies, proxy URLs, tokens or exception messages.
        result=dict(http_response_received=False,error_type=type(exc).__name__)
    result['authentication_tested']=False
    result['model_call_made']=False
    return name,result


def collect(network=True):
    result=dict(platform=platform.platform(),python=platform.python_version(),
        tools={name:bool(shutil.which(name)) for name in ('nvidia-smi','nvcc','gcc','g++','git','uv','blender','claude','codex')},
        credential_environment_present={name:bool(os.environ.get(name)) for name in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY')},
        note='Presence is not authenticated access. HTTP 401/403 only establishes an HTTP response, not usable model service.')
    for name,command in [('gpu',['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader']),
                         ('cuda_compiler',['nvcc','--version'])]:
        if shutil.which(command[0]):
            try:
                p=subprocess.run(command,capture_output=True,text=True,timeout=15)
                result[name]=dict(returncode=p.returncode,stdout=p.stdout[:6000])
            except Exception as exc:result[name]=dict(error_type=type(exc).__name__)
    if network:
        with ThreadPoolExecutor(max_workers=6) as pool:result['https']=dict(pool.map(endpoint,ENDPOINTS.items()))
    else:result['https']='skipped'
    return result
