#!/usr/bin/env python3
"""OFFLINE TEST DOUBLE ONLY. Never package as or substitute for real Codex."""
import json
import os
from pathlib import Path
import sys
import numpy as np
from PIL import Image
args=sys.argv[1:]
if args==['--version']:print('MOCK ONLY codex 0.164.0');sys.exit(0)
if args==['login','status']:print('MOCK ONLY logged in using ChatGPT');sys.exit(0)
if args==['features','list']:print('image_generation stable true');sys.exit(0)
assert args[0]=='exec'
assert args[-2]=='--' and 'Use the built-in image generation tool' in args[-1]
assert Path(args[args.index('--image')+1]).is_file()
assert '--ignore-user-config' in args
assert 'OPENAI_API_KEY' not in os.environ and 'GEMINI_API_KEY' not in os.environ
out=Path.cwd()/'outputs';out.mkdir(exist_ok=True)
mode=os.environ.get('ROOM_MOCK_MODE','success')
if mode!='unavailable':
    y,x=np.indices((512,512));rgb=np.stack([x%255,y%255,(x+y)%255],axis=2).astype(np.uint8)
    Image.fromarray(rgb).save(out/'reference_raw.png')
    if mode=='success':print(json.dumps({'type':'item.completed','item':{'id':'MOCK_image1','type':'image_generation','status':'completed'}}))
else:print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'IMAGEGEN_UNAVAILABLE (MOCK ONLY)'}}))
print(json.dumps({'type':'turn.completed'}))
Path(args[args.index('--output-last-message')+1]).write_text('MOCK ONLY '+mode)
