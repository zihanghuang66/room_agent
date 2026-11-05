"""Build current full-run and saved-scene review packages from source only."""
from pathlib import Path
import subprocess
import sys

def main():
    root=Path(__file__).resolve().parent
    for entry in (root/'litereality-full-v0.3.1/build.py', root/'build_litereality_batch_review.py'):
        subprocess.run([sys.executable,str(entry)],check=True)
    print('ROOM_004_BUILD_OK',flush=True)

if __name__=='__main__':
    main()
