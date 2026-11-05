"""Choose only the explicit run supplied by the user; never pick a latest directory."""
import argparse,json,os,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('run_directory',type=Path)
a,extra=p.parse_known_args()
folder=a.run_directory.resolve()
if a.run_directory.is_symlink() or folder.parent.name!='artifacts' or folder.parent.parent.name!='return_output':
    p.error('Expected an explicit run under <project>/return_output/artifacts')
data=json.loads((folder/'input.json').read_text())
command=[sys.executable,'-u',str(Path(__file__).with_name('run.py')),
         '--project-root',str(folder.parent.parent.parent),'--scene',data['scene'],
         '--scan-archive',data['archive'],'--resume-from',str(folder),*extra]
os.execv(sys.executable,command)
