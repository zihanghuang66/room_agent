import json
import os
from pathlib import Path
import stat

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
