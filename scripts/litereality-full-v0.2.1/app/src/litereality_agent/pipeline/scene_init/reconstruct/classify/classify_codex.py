"""Strict, image-attached classification through the authenticated Codex CLI."""
import asyncio
import json
from pathlib import Path
from litereality_agent.agent.room_codex import ask,parse_json


def classify_image(image_path,prompt,response_schema,*,model=None,api_key=None,timeout=60,retries=3):
    image=Path(image_path).resolve()
    request=prompt+'\nReturn only JSON matching this schema:\n'+json.dumps(response_schema)
    last=None
    for _ in range(max(1,retries)):
        try:
            output=asyncio.run(ask(request,cwd=image.parent,images=[image],timeout=timeout))
            return parse_json(output,response_schema)
        except Exception as exc:last=exc
    raise RuntimeError('Codex classification failed; no Claude fallback') from last
