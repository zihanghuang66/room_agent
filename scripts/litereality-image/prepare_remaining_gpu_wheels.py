"""Prefetch uncached locked GPU-environment wheels for CPython 3.10 / Linux x86_64."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse

here = Path(__file__).resolve().parent
out = here.parent / 'return_output/gpu-wheelhouse'
lines = (here / 'gpu-requirements.lock').read_text().replace('\\\n', ' ').splitlines()
locked = {}
for line in lines:
    match = re.match(r'([\w-]+)==([^\s]+)', line)
    if match:
        locked[match[1]] = (match[2], re.findall(r'sha256:([0-9a-f]{64})', line))

def open_https(request, timeout=60):
    for attempt in range(4):
        try:return urllib.request.urlopen(request,timeout=timeout)
        except OSError:
            if attempt==3:raise
            time.sleep(1)

def metadata(name):
    version, hashes = locked[name]
    if name in ('torch', 'torchvision', 'xformers'):
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.urls=[]
            def handle_starttag(self,tag,attrs):
                if tag=='a':self.urls.extend(v for k,v in attrs if k=='href')
        index=f'https://download.pytorch.org/whl/cu124/{name}/'
        with open_https(index,timeout=60) as r:
            parser=Links();parser.feed(r.read().decode())
        choices=[]
        for link in parser.urls:
            url=urllib.parse.urljoin(index,link)
            split=urllib.parse.urlsplit(url)
            digest=urllib.parse.parse_qs(split.fragment).get('sha256',[''])[0]
            filename=urllib.parse.unquote(split.path.rsplit('/',1)[-1])
            if digest not in hashes or not filename.endswith('.whl'):continue
            wheel_name,wheel_version,_,wheel_tags=parse_wheel_filename(filename)
            if str(wheel_version)!=version or not any(t in rank for t in wheel_tags):continue
            clean=urllib.parse.urlunsplit(split._replace(fragment=''))
            request=urllib.request.Request(clean,headers={'Range':'bytes=0-0'})
            with open_https(request,timeout=60) as r:
                size=int(r.headers['Content-Range'].rsplit('/',1)[-1]) if r.status==206 else int(r.headers['Content-Length'])
            choices.append({'filename':filename,'url':clean,'size':size,'digests':{'sha256':digest}})
        return choices
    with open_https(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as r:
        return json.load(r)['urls']

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024), b''):h.update(block)
    return h.hexdigest()

def fetch(item):
    target=out/item['filename']
    if target.exists() and sha(target)==item['sha256']:return
    parts=out/'remaining-parts'/item['filename']
    parts.mkdir(parents=True,exist_ok=True)
    size=item['size']; chunk=8*1024*1024
    def segment(start):
        end=min(start+chunk,size)-1
        path=parts/str(start)
        if path.exists() and path.stat().st_size==end-start+1:return
        for attempt in range(3):
            temp=path.with_suffix('.part')
            args=['curl','--fail','--silent','--show-error','--location','--proto','=https',
                  '--proto-redir','=https','--range',f'{start}-{end}','--connect-timeout','20',
                  '--max-time','90','--output',str(temp),'--write-out','%{http_code}',item['url']]
            result=subprocess.run(args,capture_output=True,text=True)
            if result.returncode==0 and result.stdout=='206' and temp.stat().st_size==end-start+1:
                temp.replace(path);return
            print('RETRY',item['name'],start,attempt,result.returncode,flush=True)
        raise RuntimeError(f'Range failed: {item["name"]} {start}')
    starts=list(range(0,size,chunk))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(segment,s) for s in starts]:future.result()
    combined=target.with_suffix('.combined')
    with combined.open('wb') as f:
        for start in starts:f.write((parts/str(start)).read_bytes())
    assert combined.stat().st_size==size and sha(combined)==item['sha256']
    combined.replace(target)
    print('VERIFIED',item['name'],size,flush=True)

# packaging itself is pure Python and is loaded from its hash-verified wheel.
pkg=next(x for x in metadata('packaging') if x['filename'].endswith('py3-none-any.whl'))
assert pkg['digests']['sha256'] in locked['packaging'][1]
record={'name':'packaging','version':locked['packaging'][0],'filename':pkg['filename'],
        'url':pkg['url'],'sha256':pkg['digests']['sha256'],'size':pkg['size']}
fetch(record)
sys.path.insert(0,str(out/pkg['filename']))
from packaging.tags import cpython_tags, compatible_tags
from packaging.utils import parse_wheel_filename
platforms=[f'manylinux_2_{n}_x86_64' for n in range(35,4,-1)]+['manylinux2014_x86_64','manylinux2010_x86_64','manylinux1_x86_64','linux_x86_64']
tags=list(cpython_tags((3,10),abis=['cp310'],platforms=platforms))+list(compatible_tags((3,10),interpreter='cp310',platforms=platforms))
rank={tag:i for i,tag in reversed(list(enumerate(tags)))}
completed=set()
for name in ('build-gpu-cdn-slow.log','build-python-cdn-slow.log','build-final-download-slow.log','build.log'):
    p=out.parent/name
    if p.exists():completed.update(re.findall(r'Downloaded ([a-z0-9_-]+)',p.read_text(errors='replace')))
for name in ('receipt.json','extra-receipt.json'):
    completed.update(x['name'] for x in json.loads((out/name).read_text()))
remaining=sorted(set(locked)-completed)
print('PREFETCH',remaining,flush=True)

def prepare(name):
    records=out/'remaining-records';records.mkdir(exist_ok=True)
    saved=records/(name+'.json')
    if saved.exists():
        item=json.loads(saved.read_text());target=out/item['filename']
        if item['version']==locked[name][0] and item['sha256'] in locked[name][1] and target.exists() and sha(target)==item['sha256']:return item
    choices=[]
    for x in metadata(name):
        if not x['filename'].endswith('.whl') or x['digests']['sha256'] not in locked[name][1]:continue
        wheel_tags=parse_wheel_filename(x['filename'])[3]
        matching=[rank[tag] for tag in wheel_tags if tag in rank]
        if matching:choices.append((min(matching),x))
    assert choices, f'No compatible locked official wheel: {name}'
    x=min(choices,key=lambda pair:pair[0])[1]
    item={'name':name,'version':locked[name][0],'filename':x['filename'],'url':x['url'],
          'sha256':x['digests']['sha256'],'size':x['size'],'hash_verified_against_original_lock':True}
    fetch(item)
    saved.write_text(json.dumps(item,indent=2)+'\n')
    return item
items=[]
with ThreadPoolExecutor(max_workers=4) as pool:
    for future in as_completed([pool.submit(prepare,name) for name in remaining]):items.append(future.result())
(out/'remaining-receipt.json').write_text(json.dumps(sorted(items,key=lambda x:x['name']),indent=2)+'\n')
print('REMAINING_WHEELS_READY',len(items),flush=True)
