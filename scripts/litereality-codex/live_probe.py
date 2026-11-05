"""Two bounded Codex requests: image classification, then MCP plus a file edit."""
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import sys
from PIL import Image,ImageDraw
from litereality_agent.agent import providers
from litereality_agent.agent.room_codex import session_diagnostics,redact
from litereality_agent.pipeline.scene_init.reconstruct.classify.classification import classify_image


async def tool_check(work,server):
    nonce=secrets.token_hex(12);state=work/'mcp_state.txt';state.write_text(nonce)
    spec=providers.SessionSpec(
        prompt='Call the MCP tool get_probe_value exactly once. Write its returned string into result.txt in the current directory. Do not read mcp_state.txt or other files. Finish by replying DONE.',
        cwd=work,timeout_seconds=180,step_budget=12,
        stdio_mcp={'roomprobe':{'command':sys.executable,'args':[str(server),str(state)]}})
    terminal=None;called=False
    async for message in providers.resolve(override='codex').run(spec):
        for block in getattr(message,'content',[]):
            if isinstance(block,providers.ToolUseBlock) and 'get_probe_value' in block.name:called=True
        if isinstance(message,providers.SessionResult):terminal=message
    output=work/'result.txt'
    return {'mcp_tool_called':called,'file_content_matches':output.is_file() and output.read_text().strip()==nonce,
        'session_completed':bool(terminal and not terminal.is_error and not terminal.stopped),
        'diagnostics':session_diagnostics(terminal)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    report=args.report.resolve();work=report/'probe_workspace';work.mkdir(parents=True)
    result={'model':'gpt-6-astra','full_reconstruction_tested':False,'claude_used':False}
    try:
        result['stage']='vision_classification'
        picture=work/'vision.png';im=Image.new('RGB',(320,200),'white');d=ImageDraw.Draw(im)
        d.rectangle((30,60,100,130),fill='red');d.ellipse((200,60,270,130),fill='blue');im.save(picture)
        schema={'type':'object','required':['left_shape','left_color','right_shape','right_color'],
            'additionalProperties':False,'properties':{k:{'type':'string'} for k in ('left_shape','left_color','right_shape','right_color')}}
        answer=classify_image(picture,'Describe the left and right colored shapes. Use English lowercase words: square or circle for shape and a basic color name.',schema,timeout=180,retries=1)
        result['vision_answer']=answer
        result['vision_passed']=answer=={'left_shape':'square','left_color':'red','right_shape':'circle','right_color':'blue'}
        result['stage']='mcp_and_file_write'
        result['tools']=asyncio.run(tool_check(work,Path(__file__).with_name('probe_mcp.py')))
        result['status']='passed' if result['vision_passed'] and all(result['tools'][k] for k in ('mcp_tool_called','file_content_matches','session_completed')) else 'incomplete'
    except Exception as exc:
        result.update(status='failed',error_type=type(exc).__name__)
        chain=[];current=exc
        for _ in range(4):
            if current is None:break
            item={'type':type(current).__name__,'message':redact(str(current))}
            if hasattr(current,'diagnostics'):item['diagnostics']=current.diagnostics
            chain.append(item);current=current.__cause__
        result['error_chain']=chain
    (report/'live_probe.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
