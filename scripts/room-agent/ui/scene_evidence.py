"""Closed, read-only scene evidence catalog. Never execute source or accept model paths."""
import hashlib
import os
import stat
from pathlib import Path

TEXT_LIMIT=2*1024*1024
IMAGE_LIMIT=8*1024*1024

def sha(data):return hashlib.sha256(data).hexdigest()

def safe_file(root,path):
    root=Path(root).absolute();path=Path(path).absolute()
    if not path.is_relative_to(root) or '..' in path.parts:return False
    if any(p.is_symlink() for p in (path,*path.parents)):return False
    return path.is_file()

def bounded_read(path,limit):
    """Open each component without following links, including during rename races."""
    path=Path(path).absolute()
    if '..' in path.parts:raise ValueError('Evidence path traversal denied')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        child=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            if not stat.S_ISREG(os.fstat(child).st_mode):raise ValueError('Evidence must be a regular file')
            with os.fdopen(child,'rb',closefd=False) as stream:data=stream.read(limit+1)
        finally:os.close(child)
        if len(data)>limit:raise ValueError('Evidence file exceeds read limit')
        return data
    except OSError as exc:raise ValueError('Evidence path unavailable or linked') from exc
    finally:os.close(fd)

class EvidenceReader:
    def __init__(self,root,source,run_id,images=(),layout=None):
        self.root=Path(root).absolute();self.source=Path(source).absolute();self.run_id=run_id
        if not self.source.is_relative_to(self.root) or any(p.is_symlink() for p in (self.source,*self.source.parents)):
            raise ValueError('Scene evidence root is not a regular selected-run directory')
        self.entries={};self.note='Existing saved evidence only; no source execution or new rendering. Source values are not measured compiled dimensions. Existing images describe the saved export, not a newly rendered source. Searches/excerpts are bounded; omissions are not proof of absence.'
        paths=[]
        for base,dirs,files in os.walk(self.source,followlinks=False):
            rel=Path(base).relative_to(self.source)
            dirs[:]=sorted(d for d in dirs if (rel.parts or d in ('Object','Objects')) and not d.startswith('.') and not (Path(base)/d).is_symlink())
            for name in sorted(files):
                if (not rel.parts and name in ('Room.py','Room.md','manifest.json','textures.json') or
                    rel.parts and rel.parts[0] in ('Object','Objects') and name in ('object.py','object.json','object.md')):
                    paths.append(Path(base)/name)
        if layout:paths.append(Path(layout))
        for p in paths[:300]:self._add(p,'source')
        for p in list(images)[:120]:self._add(Path(p),'image')
        if len(paths)>300:self.note+=' Source catalog limited to 300 files.'

    def _add(self,path,kind):
        if not safe_file(self.root,path):return
        limit=IMAGE_LIMIT if kind=='image' else TEXT_LIMIT
        if path.stat().st_size>limit:return
        rel=path.relative_to(self.root).as_posix();key=('v' if kind=='image' else 's')+sha(rel.encode())[:14]
        self.entries[key]={'id':key,'kind':kind,'path':rel,'name':path.name,'bytes':path.stat().st_size}

    def catalog(self):
        return {'run_id':self.run_id,'files':[r for r in self.entries.values() if r['kind']=='source'],
                'images':[r for r in self.entries.values() if r['kind']=='image'],'note':self.note}

    def _load(self,key,kind):
        row=self.entries.get(key)
        if not row or row['kind']!=kind:raise ValueError('Unknown or unavailable evidence ID')
        path=self.root/row['path']
        if not safe_file(self.root,path):raise ValueError('Evidence file unavailable or linked')
        limit=IMAGE_LIMIT if kind=='image' else TEXT_LIMIT
        data=bounded_read(path,limit)
        if not safe_file(self.root,path):raise ValueError('Evidence path changed during read')
        return row,data

    def read(self,query):
        if not isinstance(query,dict) or set(query)!={'files','queries','images'}:raise ValueError('Invalid inspection request')
        files,queries,images=(query[k] for k in ('files','queries','images'))
        if (not isinstance(files,list) or len(files)>6 or not isinstance(queries,list) or len(queries)>8 or
            not all(isinstance(q,str) and 0<len(q.strip())<=100 for q in queries) or
            not isinstance(images,list) or len(images)>5 or not all(isinstance(i,str) for i in images)):
            raise ValueError('Inspection exceeds bounded read limits')
        for f in files:
            if (not isinstance(f,dict) or set(f)!={'id','start','lines'} or not isinstance(f['id'],str) or
                type(f['start']) is not int or f['start']<1 or type(f['lines']) is not int or not 1<=f['lines']<=120):raise ValueError('Invalid source range')
        records=[];paths=[];used=0;seen=set();cache={}
        def load(key):
            if key not in cache:cache[key]=self._load(key,'source')
            return cache[key]
        def excerpt(key,start,count):
            nonlocal used
            row,data=load(key)
            try:lines=data.decode('utf-8').splitlines()
            except UnicodeError:return
            end=min(start+count-1,len(lines));full='\n'.join(f'{i+1}: {lines[i]}' for i in range(start-1,end));text=full[:6000]
            signature=(key,start,end)
            if not text or signature in seen or len(records)>=30 or used+len(text)>24000:return
            seen.add(signature);used+=len(text)
            records.append({**row,'evidence_id':'e'+sha((key+sha(data)+str(start)+str(end)).encode())[:18],
                            'run_id':self.run_id,'sha256':sha(data),'start':start,'end':end,'excerpt':text,'truncated':len(full)>len(text)})
        for f in files:excerpt(f['id'],f['start'],f['lines'])
        scanned=0
        for key,row in self.entries.items():
            if not queries or row['kind']!='source':continue
            _,data=load(key);scanned+=len(data)
            if scanned>16*1024*1024:break
            try:lines=data.decode('utf-8').splitlines()
            except UnicodeError:continue
            for i,line in enumerate(lines):
                if any(q.casefold() in line.casefold() for q in queries):excerpt(key,max(1,i-5),18)
                if used>=22000 or len(records)>=30:break
        image_bytes=0
        for key in dict.fromkeys(images):
            row,data=self._load(key,'image');image_bytes+=len(data)
            if image_bytes>24*1024*1024:raise ValueError('Inspection image byte budget exceeded')
            if not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')):raise ValueError('Invalid image evidence')
            records.append({**row,'run_id':self.run_id,'sha256':sha(data),'evidence_id':'e'+sha((key+sha(data)).encode())[:18],
                            'label':'Existing matched pair: RENDER left, CAPTURE right. Check visible labels; partial views are not whole-room coverage.'})
            paths.append(self.root/row['path'])
        return records,paths

    def verify(self,records):
        for r in records:
            if r.get('run_id')!=self.run_id:raise ValueError('Evidence belongs to another scene')
            row,data=self._load(r['id'],r['kind'])
            if row['path']!=r['path'] or sha(data)!=r['sha256']:raise ValueError('Inspected evidence changed; inspect again')


def for_scene(service,run_id):
    from room_agent.revisions import selected_source
    from display import build_presentation
    info=selected_source(service,run_id,hashes=False)
    request,root=service._load(run_id)
    with service._graph(request,root) as (graph,_,_,config):state=dict(graph.get_state(config).values)
    pairs=[Path(p) for p in state.get('pairs',[]) if Path(p).name.startswith('pair_')]
    if not pairs:
        presentation=build_presentation(root,service.show(run_id))
        groups=presentation.get('comparisons') or presentation.get('diagnostics',{}).get('comparisons') or []
        if groups:pairs=[root/r['path'] for r in groups[0]['pairs']]
    layout=Path(state['preview'])/'room_layout.json' if state.get('preview') else None
    return EvidenceReader(root,Path(info['source']),run_id,pairs,layout)


def for_review(service,source_id,review_id):
    from room_agent.revisions import selected_source
    request,_=service._load(review_id)
    if request.get('operation')!='saved_scene_review' or request.get('parent_run_id')!=source_id or request.get('max_batches')!=0:
        raise ValueError('Evidence review does not belong to current source')
    current=selected_source(service,source_id)
    reviewed=selected_source(service,review_id)
    for key in ('source_files','seed_files','capture_files'):
        if current[key]!=request[key] or reviewed[key]!=request[key]:
            raise ValueError('Reviewed source or capture changed; inspect current evidence again')
    return for_scene(service,review_id)


def verify_bound(service,run_id,records):
    groups={}
    for record in records:groups.setdefault(record['run_id'],[]).append(record)
    for owner,rows in groups.items():
        reader=for_scene(service,run_id) if owner==run_id else for_review(service,run_id,owner)
        reader.verify(rows)
