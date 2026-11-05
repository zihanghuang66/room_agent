"""Validate Docker save metadata without extracting any archive members."""
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[2]
out = root / 'return_output'
archive = out / 'room-litereality-agent-0.1.3-cuda124-r5.docker.tar'
expected = 'room-litereality-agent:0.1.3-cuda124-r5'
names = set()
metadata = {}
with tarfile.open(archive, 'r|') as tar:
    for member in tar:
        names.add(member.name)
        if member.isfile() and member.size <= 4*1024*1024:
            data = tar.extractfile(member).read()
            if data.lstrip().startswith((b'{', b'[')):
                try:metadata[member.name] = json.loads(data)
                except (ValueError, UnicodeError):pass
assert 'manifest.json' in metadata, 'Docker save manifest missing'
manifest = metadata['manifest.json']
entry = next(x for x in manifest if expected in (x.get('RepoTags') or []))
assert entry['Config'] in names
assert all(layer in names for layer in entry['Layers'])
config = metadata[entry['Config']]
assert config['architecture']=='amd64' and config['os']=='linux'
assert config['config']['Cmd']==['/usr/local/bin/start-ssh.sh']
report = {'default_command':config['config']['Cmd'], 'status':'passed', 'archive':archive.name, 'bytes':archive.stat().st_size,
          'repo_tag':expected, 'architecture':config['architecture'], 'os':config['os'],
          'layer_count':len(entry['Layers']), 'docker_save_manifest':True,
          'oci_index_also_present':'index.json' in metadata}
(out/'ssh-platform-r5/export-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
